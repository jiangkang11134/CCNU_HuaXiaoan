"""Agent runtime streaming service.

This module is the LangGraph execution path used by the worker after an
``AgentRun`` has already been created. It restores input messages, builds the
agent runtime context, streams model/tool events, persists assistant output and
extracts UI-facing agent state.

Do not put run creation, request id idempotency, queueing or external
invocation response formatting here. Those responsibilities belong to
``agent_run_service`` and ``agent_invocation_service`` respectively. Keeping
this file focused on execution makes normal chat, resume runs and subagent runs
share the same runtime behavior once they reach the worker.
"""

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any, Literal, NamedTuple

from langchain.messages import AIMessage, AIMessageChunk
from langgraph.types import Command
from sqlalchemy import select
from yuxi import config as conf
from yuxi.config.user import DEFAULT_ENABLE_MEMORY
from yuxi.agents.buildin import agent_manager
from yuxi.agents.context import build_agent_input_context, normalize_agent_context_config
from yuxi.memory.service import MemoryService
from yuxi.memory.tasks import enqueue_turn_memory_extraction
from yuxi.self_evolution.service import CorrectionService
from yuxi.services.request_preprocessor import preprocess
from yuxi.services.intent_classifier import classify_with_api
from yuxi.storage.postgres.models_business import UserConfig
from yuxi.agents.state import AgentStatePayload
from yuxi.repositories.agent_repository import AgentRepository
from yuxi.repositories.agent_run_repository import AgentRunRepository
from yuxi.repositories.conversation_repository import ConversationRepository
from yuxi.repositories.subagent_thread_repository import SubagentThreadRepository
from yuxi.services.agent_prompt_policy import get_enabled_global_agent_prompt
from yuxi.services.conversation_service import serialize_attachment
from yuxi.services.input_message_service import AgentRunInputMessage
from yuxi.services.langfuse_service import (
    LangfuseRunContext,
    build_run_context,
    flush_langfuse,
    get_trace_info,
)
from yuxi.services.subagent_run_service import serialize_subagent_run_state
from yuxi.services.user_profile import build_profile_context, overridden_fact_keys
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import Agent, Message, User, SystemKV
from yuxi.utils.guard import ContentGuardConfigurationError, content_guard
from yuxi.utils.logging_config import logger
from yuxi.utils.question_utils import (
    normalize_questions as _normalize_interrupt_questions,
)
from yuxi.utils.thread_utils import extract_thread_id as _metadata_thread_id


def _build_state_files(attachments: list[dict]) -> dict:
    """将附件列表转换为 StateBackend 格式的 files 字典

    StateBackend 期望的格式:
    {
        "/attachments/file.md": {
            "content": ["line1", "line2", ...],
            "created_at": "...",
            "modified_at": "...",
        }
    }
    """
    files = {}
    for attachment in attachments:
        if attachment.get("status") != "parsed":
            continue

        file_path = attachment.get("file_path")
        markdown = attachment.get("markdown")

        if not file_path or not markdown:
            continue

        now = datetime.now(UTC).isoformat()
        # 将 markdown 内容按行拆分
        content_lines = markdown.split("\n")
        files[file_path] = {
            "content": content_lines,
            "created_at": attachment.get("uploaded_at", now),
            "modified_at": attachment.get("uploaded_at", now),
        }

    return files


def _build_agent_context(agent, input_context: dict):
    context = agent.context_schema()
    context.update(input_context)
    return context


class _MemoryInjectionResult(NamedTuple):
    """记忆与权威修正注入的结果。

    injections: 本次实际注入的修正埋点（P3，设计文档 §4.1），随回答消息落库。
    memory_enabled: 该用户的长期记忆开关。抽取已搬到后台任务，后者会自行复核同一开关，
        这里保留它只是为了调用方能观测到本次注入用的是哪个口径。
    """

    injections: list[dict]
    memory_enabled: bool


async def _last_user_query(db, thread_id: str) -> str:
    """取该会话最后一条用户消息文本。

    中断续跑（stream_agent_resume）请求本身不带新 query，但权威修正与记忆事实的
    检索需要原始问题作为输入，因此回查该线程最后一条 user 消息。取不到时返回空
    字符串，调用方据此跳过注入。
    """
    try:
        conv = await ConversationRepository(db).get_conversation_by_thread_id(thread_id)
        if not conv:
            return ""
        row = (await db.execute(
            select(Message.content)
            .where(Message.conversation_id == conv.id, Message.role == "user")
            .order_by(Message.created_at.desc())
            .limit(1)
        )).scalar_one_or_none()
        return (row or "").strip()
    except Exception:
        logger.exception("failed to resolve last user query for resume")
        return ""


MEMORY_OBSERVE_KV = "memory_observe"
# U1：意图 → 回答口径 的发版外开关
INTENT_CALIBER_KV = "intent_caliber"


async def _resolve_bool_switch(db, key: str, default: bool) -> bool:
    """SystemKV 里的布尔开关；读不到或格式不对一律回默认值。

    这类开关存在的唯一意义就是"出问题能立刻关掉而不发版"，所以读取失败绝不能抛，
    也不能把异常变成"关掉"——那会让一次数据库抖动悄悄改掉系统行为。
    """
    try:
        kv = await db.get(SystemKV, key)
        if kv and isinstance(kv.value, dict):
            enabled = kv.value.get("enabled")
            if isinstance(enabled, bool):
                return enabled
    except Exception:
        logger.exception("failed to load SystemKV switch %s", key)
    return default


async def _resolve_memory_observe(db) -> bool:
    """系统级开关：关闭后不再做记忆抽取（默认开启）。

    抽取已搬到后台独立任务（:mod:`yuxi.memory.extraction`），那里直接读同一个
    ``memory_observe`` SystemKV，因此这里保留的只是**开关名与读取语义的定义处**：
    出问题时仍能立刻关掉抽取而不发版，不必等下一次发版。
    """
    return await _resolve_bool_switch(db, MEMORY_OBSERVE_KV, True)


async def _resolve_intent_caliber(db) -> bool:
    """系统级开关：关闭后不再按意图注入回答口径（默认开启）。

    口径是一段 prompt 纪律，措辞不当会直接压低回答质量。留着开关才能在发现问题时
    立刻退回"所有意图共用同一套口径"的旧行为，不必等下一次发版。
    """
    return await _resolve_bool_switch(db, INTENT_CALIBER_KV, True)


async def _inject_memory_and_corrections(
    db,
    *,
    uid: str,
    thread_id: str,
    query: str,
    kb_ids: list[str] | None,
    intent: str | None,
    input_context: dict,
) -> _MemoryInjectionResult:
    """把 [当前可用事实]、[个人资料]、[权威修正] 依次追加到 input_context["system_prompt"]。

    顺序是刻意的：事实在前（个人资料的风格说明要能"指上文"），权威修正压在最后
    （它是结论依据的最高层）。个人资料段由 :func:`yuxi.services.user_profile
    .build_profile_context` 拼装，其中回答风格遵守
    「本轮会话事实 > 个人资料 > 长期记忆偏好」的三级顺序。

    主问答链路与中断续跑链路共用，保证续跑不会绕过已审核的权威修正约束。
    任何异常只记录日志，不阻塞主链路（与既有行为一致）。

    query 为空时修正检索无输入（直接返回空），调用方应保证传入真实问题文本。
    """
    memory_enabled = DEFAULT_ENABLE_MEMORY
    profile_major: str | None = None
    profile_style: str | None = None
    business_role: str | None = None
    dept_id: int | None = None

    # M1：作用域分流需要当前用户与其部门。user_pref 只注入给本人、
    # dept_rule 只注入给同部门，kb_truth 才全局——取不到就按最严的处理。
    # 同一行顺带取 business_role：个人资料要用它，且它和 department_id 一样都是
    # 注册时定死、会话内不会变的值，一次取完可为每轮都要走的热路径省一条查询。
    try:
        user_row = (await db.execute(
            select(User.department_id, User.business_role).where(User.uid == uid))).first()
        if user_row is not None:
            dept_id, business_role = user_row[0], user_row[1]
    except Exception:
        logger.exception("failed to resolve department for correction scope")

    try:
        # 无 user_config 行（绝大多数用户从未改过设置）时返回 None，
        # 此时按系统默认走，不能当成"关闭"——否则默认开启的策略在热路径上失效。
        # 顺带取个人资料里的专业与回答风格：这是每轮都要走的热路径，
        # 能为它们少发一条查询就少发一条。
        cfg_row = (await db.execute(
            select(UserConfig.enable_memory, UserConfig.major, UserConfig.response_style)
            .where(UserConfig.uid == uid))).first()
        if cfg_row is not None:
            memory_enabled = bool(cfg_row[0])
            profile_major = cfg_row[1]
            profile_style = cfg_row[2]
        # 个人资料覆盖了哪些长期记忆键，必须**在取记忆之前**算好：
        # 这些键要从 [当前可用事实] 里摘掉，否则同一件事会给模型两个答案。
        suppressed_fact_keys = overridden_fact_keys(major=profile_major)
        memory_context = await MemoryService.build_context(
            db, uid, thread_id, memory_enabled, query,
            suppressed_fact_keys=suppressed_fact_keys)
        if memory_context:
            input_context["system_prompt"] = (
                f"{input_context.get('system_prompt', '')}\n\n[当前可用事实]\n{memory_context}"
            )
    except Exception:
        logger.exception("failed to build memory context")

    # [个人资料] 必须紧跟在 [当前可用事实] 之后：它的风格优先级说明要写
    # "若上文 [当前可用事实] 里出现了…以它为准"，排到前面就成了指下文。
    # [权威修正] 留最后——它是结论依据的最高层，且 prompt 尾部的注意力更足。
    try:
        profile_context = build_profile_context(
            business_role=business_role,
            major=profile_major,
            response_style=profile_style,
        )
        if profile_context:
            input_context["system_prompt"] = (
                f"{input_context.get('system_prompt', '')}\n\n[个人资料]\n{profile_context}"
            )
    except Exception:
        logger.exception("failed to build profile context")

    injections: list[dict] = []
    try:
        correction_context, injections = await CorrectionService.build_correction_context_details(
            db, query, kb_ids=kb_ids or [], intent=intent, uid=uid, dept_id=dept_id)
        if correction_context:
            input_context["system_prompt"] = (
                f"{input_context.get('system_prompt', '')}\n\n[权威修正]\n{correction_context}"
            )
    except Exception:
        logger.exception("failed to build correction context")
    return _MemoryInjectionResult(injections=injections, memory_enabled=memory_enabled)


async def _get_langgraph_messages(agent_instance, config_dict, *, context):
    graph = await agent_instance.get_graph(context=context)
    state = await graph.aget_state(config_dict)

    if not state or not state.values:
        logger.warning("No state found in LangGraph")
        return None

    return state.values.get("messages", [])


def _build_langfuse_run_context(
    *,
    current_user,
    thread_id: str,
    agent_id: str,
    request_id: str,
    operation: str,
    backend_id: str | None = None,
    message_type: str | None = None,
    meta: dict | None = None,
) -> LangfuseRunContext:
    extra_metadata = None
    extra_tags = None
    invocation_meta = (meta or {}).get("agent_invocation_meta") if isinstance(meta, dict) else None
    evaluation = invocation_meta.get("evaluation") if isinstance(invocation_meta, dict) else None
    # 如果请求来自智能体评测，添加评测相关的 metadata 和 tags，方便在 Langfuse 中进行过滤和分析
    if (meta or {}).get("source") == "agent_evaluation" or (isinstance(evaluation, dict) and evaluation):
        extra_metadata = {
            "source": "agent_evaluation",
            "feature": "agent_evaluation",
        }
        extra_tags = ["agent_evaluation"]
        if isinstance(evaluation, dict):
            dataset_name = evaluation.get("dataset_name")
            experiment_name = evaluation.get("experiment_name")
            for key in ("dataset_name", "dataset_item_id", "experiment_name"):
                value = evaluation.get(key)
                if value:
                    extra_metadata[f"evaluation_{key}"] = str(value)
            if dataset_name:
                extra_tags.append(f"dataset:{dataset_name}")
            if experiment_name:
                extra_tags.append(f"experiment:{experiment_name}")

    return build_run_context(
        user_id=str(getattr(current_user, "uid", current_user.id)),
        thread_id=thread_id,
        agent_id=agent_id,
        request_id=request_id,
        operation=operation,
        backend_id=backend_id,
        message_type=message_type,
        username=getattr(current_user, "username", None),
        login_user_id=getattr(current_user, "uid", None),
        department_id=getattr(current_user, "department_id", None),
        extra_metadata=extra_metadata,
        extra_tags=extra_tags,
    )


def extract_agent_state(values: dict) -> AgentStatePayload:
    """从 LangGraph state 中提取 agent 状态"""
    if not isinstance(values, dict):
        return {"todos": [], "files": {}, "artifacts": [], "subagent_runs": [], "token_usage": None}

    # 直接获取，信任 state 的数据结构
    todos = values.get("todos")
    artifacts = values.get("artifacts")
    subagent_runs = values.get("subagent_runs")
    token_usage = values.get("token_usage")
    result: AgentStatePayload = {
        "todos": list(todos)[:20] if todos else [],
        "files": values.get("files") or {},
        "artifacts": list(artifacts) if artifacts else [],
        "subagent_runs": list(subagent_runs) if subagent_runs else [],
        "token_usage": dict(token_usage) if isinstance(token_usage, dict) else None,
    }

    return result


def _agent_state_signature(agent_state: AgentStatePayload | dict | None) -> str:
    if not agent_state:
        return ""
    try:
        return json.dumps(agent_state, ensure_ascii=False, sort_keys=True)
    except Exception:
        return str(agent_state)


def _metadata_namespace(metadata: dict | None) -> list[str]:
    if not isinstance(metadata, dict):
        return []
    namespace = metadata.get("namespace")
    if isinstance(namespace, list):
        return [str(item) for item in namespace]
    return []


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, dict):
        return {str(key): _json_safe(child) for key, child in value.items()}
    if isinstance(value, list | tuple):
        return [_json_safe(child) for child in value]
    if hasattr(value, "model_dump"):
        return _json_safe(value.model_dump())
    return str(value)


def _apply_model_override(input_context: dict, meta: dict | None) -> None:
    """对话级模型覆盖：meta.model_spec 优先于智能体配置的 model。值已在创建 run 时校验。"""
    model_spec = (meta or {}).get("model_spec")
    model_spec = model_spec.strip() if isinstance(model_spec, str) else model_spec
    if model_spec:
        input_context["model"] = model_spec


def _apply_subagent_runtime_context(input_context: dict, meta: dict | None) -> None:
    """把子智能体 run 的父线程和文件线程信息注入运行 context。"""
    meta = meta or {}
    # 仅对子智能体类型的 run 生效
    if meta.get("run_type") != "subagent":
        return
    # 这三个线程 ID 由 subagent_run_service 在创建 run 时写入 runtime，
    # 是子智能体区别于普通对话的唯一依据；缺失即上游契约被破坏，直接失败而非静默回退。
    for key in ("parent_thread_id", "file_thread_id", "skills_thread_id"):
        value = str(meta.get(key) or "").strip()
        if not value:
            raise ValueError(f"子智能体运行缺少必需的 {key}")
        input_context[key] = value
    # 标记为子智能体运行，供下游逻辑判断
    input_context["is_subagent_runtime"] = True


def _stream_message_key(metadata: dict | None, namespace: list[str], thread_id: str | None) -> tuple[str, str]:
    if not isinstance(metadata, dict):
        return thread_id or "", "/".join(namespace)
    return thread_id or "", str(metadata.get("run_id") or metadata.get("langgraph_node") or "/".join(namespace))


def _stream_message_id(
    message_ids: dict[tuple[str, str], str],
    key: tuple[str, str],
    preferred: str | None = None,
) -> str:
    if preferred:
        message_ids[key] = preferred
        return preferred
    return message_ids.setdefault(key, str(uuid.uuid4()))


def _message_chunk_yuxi_events(
    msg_dict: dict[str, Any],
    *,
    message_id: str,
    thread_id: str | None,
    namespace: list[str],
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    route = {"thread_id": thread_id, "namespace": namespace}
    content = msg_dict.get("content")
    additional_kwargs = msg_dict.get("additional_kwargs") if isinstance(msg_dict.get("additional_kwargs"), dict) else {}
    reasoning_content = msg_dict.get("reasoning_content")
    additional_reasoning_content = additional_kwargs.get("reasoning_content")

    message_event: dict[str, Any] = {"type": "message_delta", "message_id": message_id, **route}
    if isinstance(content, str) and content:
        message_event["content"] = content
    if isinstance(reasoning_content, str) and reasoning_content:
        message_event["reasoning_content"] = reasoning_content
    if isinstance(additional_reasoning_content, str) and additional_reasoning_content:
        message_event["additional_reasoning_content"] = additional_reasoning_content
    if len(message_event) > 4:
        events.append(message_event)

    tool_call_chunks = msg_dict.get("tool_call_chunks")
    if isinstance(tool_call_chunks, list):
        for tool_call_chunk in tool_call_chunks:
            if not isinstance(tool_call_chunk, dict):
                continue
            args_delta = tool_call_chunk.get("args")
            if args_delta is None:
                args_delta = ""
            elif not isinstance(args_delta, str):
                args_delta = json.dumps(args_delta, ensure_ascii=False)
            if not tool_call_chunk.get("id") and not tool_call_chunk.get("name") and not args_delta:
                continue
            events.append(
                {
                    "type": "tool_call_delta",
                    "message_id": message_id,
                    "tool_call_id": tool_call_chunk.get("id"),
                    "name": tool_call_chunk.get("name") or None,
                    "args_delta": args_delta,
                    "index": tool_call_chunk.get("index") if tool_call_chunk.get("index") is not None else 0,
                    **route,
                }
            )
    return events


def _protocol_event_yuxi_event(
    event: dict[str, Any],
    *,
    message_id: str | None,
    thread_id: str | None,
    namespace: list[str],
) -> dict[str, Any] | None:
    event_name = event.get("event")
    if event_name in {"message-start", "content-block-start", "message-finish"} or not message_id:
        return None

    route = {"thread_id": thread_id, "namespace": namespace}
    if event_name == "content-block-delta":
        delta = event.get("delta") if isinstance(event.get("delta"), dict) else {}
        text = delta.get("text")
        if delta.get("type") == "text-delta" and isinstance(text, str) and text:
            return {"type": "message_delta", "message_id": message_id, "content": text, **route}
        return None

    if event_name == "content-block-finish":
        content = event.get("content") if isinstance(event.get("content"), dict) else {}
        if content.get("type") != "tool_call" or not content.get("id") and not content.get("name"):
            return None
        return {
            "type": "tool_call",
            "message_id": message_id,
            "tool_call_id": content.get("id"),
            "name": content.get("name"),
            "args": content.get("args") if content.get("args") is not None else {},
            "index": event.get("index") if event.get("index") is not None else 0,
            **route,
        }

    return None


def _context_compression_payload(payload: Any) -> dict | None:
    if isinstance(payload, dict) and payload.get("type") == "yuxi.context_compression":
        return payload
    return None


def _stream_event_response(event: dict[str, Any]) -> str:
    if event.get("type") != "message_delta":
        return ""
    return str(event.get("content") or "")


def _message_payload_yuxi_events(
    msg: Any,
    *,
    metadata: dict[str, Any],
    namespace: list[str],
    thread_id: str | None,
    protocol_message_ids: dict[tuple[str, str], str],
) -> list[dict[str, Any]]:
    message_key = _stream_message_key(metadata, namespace, thread_id)
    if isinstance(msg, dict) and isinstance(msg.get("event"), str):
        preferred_message_id = str(msg["id"]) if msg.get("event") == "message-start" and msg.get("id") else None
        message_id = _stream_message_id(protocol_message_ids, message_key, preferred_message_id)
        stream_event = _protocol_event_yuxi_event(
            msg,
            message_id=message_id,
            thread_id=thread_id,
            namespace=namespace,
        )
        return [stream_event] if stream_event else []

    if isinstance(msg, AIMessageChunk) or hasattr(msg, "model_dump"):
        msg_dict = msg.model_dump()
    elif isinstance(msg, dict):
        msg_dict = dict(msg)
    else:
        msg_dict = {"content": str(msg)}

    message_id = str(msg_dict.get("id") or _stream_message_id(protocol_message_ids, message_key))
    return _message_chunk_yuxi_events(
        msg_dict,
        message_id=message_id,
        thread_id=thread_id,
        namespace=namespace,
    )


async def _stream_agent_events(agent, messages, *, input_context=None, **kwargs):
    async for mode, payload in agent.stream_messages_with_state(
        messages,
        input_context=input_context,
        **kwargs,
    ):
        yield mode, payload


async def _get_existing_message_ids(conv_repo: ConversationRepository, thread_id: str) -> set[str]:
    existing_messages = await conv_repo.get_messages_by_thread_id(thread_id)
    return {
        msg.extra_metadata["id"]
        for msg in existing_messages
        if msg.extra_metadata and "id" in msg.extra_metadata and isinstance(msg.extra_metadata["id"], str)
    }


async def _save_ai_message(
    conv_repo: ConversationRepository,
    thread_id: str,
    msg_dict: dict,
    trace_info: dict[str, Any] | None = None,
    run_id: str | None = None,
    request_id: str | None = None,
):
    content = msg_dict.get("content", "")
    tool_calls_data = msg_dict.get("tool_calls") or []
    if isinstance(content, list):
        if not tool_calls_data:
            tool_calls_data = [
                {"id": item.get("id"), "name": item.get("name"), "args": item.get("args") or {}}
                for item in content
                if isinstance(item, dict) and item.get("type") == "tool_call"
            ]
        content = "\n".join(
            item.get("text", "") for item in content if isinstance(item, dict) and isinstance(item.get("text"), str)
        )
    elif not isinstance(content, str):
        content = str(content)
    extra_metadata = dict(msg_dict)
    if trace_info:
        extra_metadata.update(trace_info)

    ai_msg = await conv_repo.add_message_by_thread_id(
        thread_id=thread_id,
        role="assistant",
        content=content,
        message_type="text",
        extra_metadata=extra_metadata,
        run_id=run_id,
        request_id=request_id,
    )

    if ai_msg and tool_calls_data:
        for tc in tool_calls_data:
            await conv_repo.add_tool_call(
                message_id=ai_msg.id,
                tool_name=tc.get("name") or "unknown",
                tool_input=tc.get("args", {}),
                status="pending",
                langgraph_tool_call_id=tc.get("id"),
            )

    return ai_msg


async def _save_tool_message(conv_repo: ConversationRepository, msg_dict: dict) -> None:
    tool_call_id = msg_dict.get("tool_call_id")
    content = msg_dict.get("content", "")

    if not tool_call_id:
        return

    if isinstance(content, list):
        tool_output = json.dumps(content) if content else ""
    else:
        tool_output = str(content)

    await conv_repo.update_tool_call_output(
        langgraph_tool_call_id=tool_call_id,
        tool_output=tool_output,
        status="success",
    )


async def save_partial_message(
    conv_repo: ConversationRepository,
    thread_id: str,
    full_msg=None,
    error_message: str | None = None,
    error_type: str = "interrupted",
    trace_info: dict[str, Any] | None = None,
    run_id: str | None = None,
    request_id: str | None = None,
):
    try:
        extra_metadata = {
            "error_type": error_type,
            "is_error": True,
            "error_message": error_message or f"发生错误: {error_type}",
        }
        if full_msg:
            msg_dict = full_msg.model_dump() if hasattr(full_msg, "model_dump") else {}
            content = full_msg.content if hasattr(full_msg, "content") else str(full_msg)
            extra_metadata = msg_dict | extra_metadata
        else:
            content = ""

        if trace_info:
            extra_metadata.update(trace_info)

        return await conv_repo.add_message_by_thread_id(
            thread_id=thread_id,
            role="assistant",
            content=content,
            message_type="text",
            extra_metadata=extra_metadata,
            run_id=run_id,
            request_id=request_id,
        )

    except Exception as e:
        logger.exception(f"Error saving message: {e}")
        return None


async def save_messages_from_langgraph_state(
    agent_instance,
    thread_id: str,
    conv_repo: ConversationRepository,
    config_dict: dict,
    context,
    trace_info: dict[str, Any] | None = None,
    run_id: str | None = None,
    request_id: str | None = None,
) -> None:
    messages = await _get_langgraph_messages(agent_instance, config_dict, context=context)
    if messages is None:
        return

    existing_ids = await _get_existing_message_ids(conv_repo, thread_id)

    last_ai_message = None
    for msg in messages:
        if hasattr(msg, "model_dump"):
            msg_dict = msg.model_dump()
        elif isinstance(msg, dict):
            msg_dict = dict(msg)
        else:
            continue

        msg_type = msg_dict.get("type", "unknown")
        if msg_type == "unknown":
            role = msg_dict.get("role")
            if role in {"assistant", "ai"}:
                msg_type = "ai"
            elif role in {"user", "human"}:
                msg_type = "human"
            elif role == "tool":
                msg_type = "tool"

        msg_id = getattr(msg, "id", None) or msg_dict.get("id")
        if msg_type == "human" or msg_id in existing_ids:
            continue

        if msg_type == "ai":
            last_ai_message = await _save_ai_message(
                conv_repo,
                thread_id,
                msg_dict,
                trace_info=trace_info,
                run_id=run_id,
                request_id=request_id,
            )
        elif msg_type == "tool":
            await _save_tool_message(conv_repo, msg_dict)

    if run_id and last_ai_message:
        run_repo = AgentRunRepository(conv_repo.db)
        await run_repo.set_output_message(run_id, last_ai_message.id)
        await conv_repo.db.commit()


def _extract_interrupt_info(state) -> Any | None:
    """从 LangGraph state 中提取中断信息"""
    if hasattr(state, "tasks") and state.tasks:
        for task in state.tasks:
            if hasattr(task, "interrupts") and task.interrupts:
                return task.interrupts[0]

    interrupt_data = state.values.get("__interrupt__")
    if isinstance(interrupt_data, list) and interrupt_data:
        return interrupt_data[0]

    return None


def _coerce_interrupt_payload(info: Any) -> dict:
    """将 LangGraph interrupt 对象转换为 dict 结构。"""
    if isinstance(info, dict):
        return info

    payload = getattr(info, "value", None)
    if isinstance(payload, dict):
        return payload

    questions = getattr(info, "questions", None)
    source = getattr(info, "source", None)
    result: dict[str, Any] = {}
    if isinstance(questions, list):
        result["questions"] = questions
    if isinstance(source, str) and source.strip():
        result["source"] = source
    return result


def _build_ask_user_question_payload(info: Any, thread_id: str) -> dict[str, Any]:
    """将 interrupt 信息标准化为 ask_user_question_required 载荷。"""
    payload = _coerce_interrupt_payload(info)

    questions = _normalize_interrupt_questions(payload.get("questions"))

    if not questions:
        questions = [
            {
                "question_id": str(uuid.uuid4()),
                "question": "请选择一个选项",
                "options": [],
                "multi_select": False,
                "allow_other": True,
            }
        ]

    source = str(payload.get("source") or payload.get("tool_name") or "interrupt")

    return {
        "questions": questions,
        "source": source,
        "thread_id": thread_id,
    }


def _ensure_full_msg(full_msg: AIMessage | None, accumulated_content: list[str]) -> AIMessage | None:
    """如果 full_msg 为空且有累积内容，构建 AIMessage"""
    if not full_msg and accumulated_content:
        return AIMessage(content="".join(accumulated_content))
    return full_msg


def _extract_ai_message(messages: list[Any] | None) -> AIMessage | None:
    """从消息列表中提取最后一条 AIMessage。"""
    if not isinstance(messages, list):
        return None

    for msg in reversed(messages):
        if isinstance(msg, AIMessage):
            return msg

        msg_dict = msg.model_dump() if hasattr(msg, "model_dump") else {}
        if msg_dict.get("type") == "ai":
            content = msg_dict.get("content", "")
            return msg if hasattr(msg, "content") else AIMessage(content=content)

    return None


async def _resolve_agent_runtime(
    *,
    db,
    user: User,
    requested_agent_slug: str | None,
    thread_id: str | None,
    agent_kind: Literal["main", "subagent"] = "main",
) -> tuple[Agent, Any, dict]:
    """解析智能体运行时，返回 (Agent, backend, agent_config)"""
    agent_repo = AgentRepository(db)
    conv_repo = ConversationRepository(db)
    resolved_agent_slug = requested_agent_slug

    if thread_id:
        conversation = await conv_repo.get_conversation_by_thread_id(thread_id)
        if conversation:
            if conversation.uid != str(user.uid) or conversation.status == "deleted":
                raise ValueError("对话线程不存在")
            # Conversation.agent_id 是历史字段名，实际保存的是 Agent.slug。
            if requested_agent_slug and requested_agent_slug != conversation.agent_id:
                raise ValueError("已有线程已绑定智能体，不能切换")
            resolved_agent_slug = conversation.agent_id

    if not resolved_agent_slug:
        raise ValueError("缺少必需的 agent_slug 字段")

    agent_item = await agent_repo.get_visible_by_slug(slug=resolved_agent_slug, user=user, kind=agent_kind)
    if not agent_item:
        raise ValueError("智能体不存在或无权限访问")

    backend = agent_manager.get_agent(agent_item.backend_id)
    if not backend:
        raise ValueError(f"智能体后端 {agent_item.backend_id} 不存在")

    agent_config = await normalize_agent_context_config(
        (agent_item.config_json or {}).get("context", {}),
        db=db,
        user=user,
        context_schema=backend.context_schema,
    )
    return agent_item, backend, agent_config


async def check_and_handle_interrupts(
    agent,
    langgraph_config: dict,
    make_chunk,
    meta: dict,
    thread_id: str,
    context,
) -> AsyncIterator[bytes]:
    try:
        graph = await agent.get_graph(context=context)
        state = await graph.aget_state(langgraph_config)

        if not state or not state.values:
            return

        interrupt_info = _extract_interrupt_info(state)
        if interrupt_info:
            question_payload = _build_ask_user_question_payload(interrupt_info, thread_id)
            meta["interrupt"] = question_payload
            yield make_chunk(status="ask_user_question_required", meta=meta, **question_payload)

    except Exception as e:
        logger.exception(f"Error checking interrupts: {e}")


async def _ensure_thread_bound_agent(
    *,
    conv_repo: ConversationRepository,
    thread_id: str,
    uid: str,
    agent_item: Agent,
) -> None:
    conversation = await conv_repo.get_conversation_by_thread_id(thread_id)
    if not conversation:
        await conv_repo.create_conversation(
            uid=uid,
            agent_id=agent_item.slug,
            thread_id=thread_id,
            metadata={"backend_id": agent_item.backend_id},
        )
        return

    if conversation.agent_id != agent_item.slug:
        raise ValueError("已有线程已绑定智能体，不能切换")


def _normalize_attachment_file_ids(meta: dict | None) -> list[str]:
    file_ids = (meta or {}).get("attachment_file_ids") or []
    if not isinstance(file_ids, list):
        return []

    normalized = []
    seen = set()
    for file_id in file_ids:
        value = str(file_id).strip()
        if not value or value in seen:
            continue
        seen.add(value)
        normalized.append(value)
    return normalized


async def _bind_request_attachments(
    *,
    conv_repo: ConversationRepository,
    thread_id: str,
    request_id: str,
    attachment_file_ids: list[str],
) -> list[dict]:
    conversation = await conv_repo.get_conversation_by_thread_id(thread_id)
    if not conversation:
        return []

    if attachment_file_ids:
        attachments = await conv_repo.bind_attachments_to_request(conversation.id, request_id, attachment_file_ids)
    else:
        attachments = await conv_repo.get_attachments_by_request_id(conversation.id, request_id)

    return [serialize_attachment(attachment) for attachment in attachments]


async def stream_agent_chat(
    *,
    agent_slug: str,
    thread_id: str | None,
    meta: dict,
    input_message: AgentRunInputMessage,
    current_user,
    db,
    save_user_message: bool = True,
) -> AsyncIterator[bytes]:
    start_time = asyncio.get_event_loop().time()

    def make_chunk(content=None, **kwargs):
        chunk_thread_id = kwargs.pop("thread_id", None) or meta.get("thread_id") or thread_id
        return (
            json.dumps(
                {"request_id": meta.get("request_id"), "response": content, "thread_id": chunk_thread_id, **kwargs},
                ensure_ascii=False,
            ).encode("utf-8")
            + b"\n"
        )

    meta = dict(meta or {})
    if "request_id" not in meta or not meta.get("request_id"):
        logger.warning("请求缺少 request_id，已自动生成一个新的 request_id")
        meta["request_id"] = str(uuid.uuid4())

    uid = str(current_user.uid)
    if not thread_id:
        thread_id = str(uuid.uuid4())
        logger.warning(f"No thread_id provided, generated new thread_id: {thread_id}")

    query = input_message.content
    image_content = input_message.image_content
    human_message = input_message.require_langchain_message()
    message_type = input_message.message_type

    if conf.enable_content_guard:
        try:
            is_blocked = await content_guard.check(query)
        except ContentGuardConfigurationError as exc:
            yield make_chunk(
                status="error",
                error_type="content_guard_configuration_error",
                error_message=str(exc),
                meta=meta,
            )
            return
        if is_blocked:
            yield make_chunk(
                status="error", error_type="content_guard_blocked", error_message="输入内容包含敏感词", meta=meta
            )
            return

    try:
        agent_item, agent, agent_config = await _resolve_agent_runtime(
            db=db,
            user=current_user,
            requested_agent_slug=agent_slug,
            thread_id=thread_id,
            agent_kind="subagent" if meta.get("run_type") == "subagent" else "main",
        )
    except ValueError as e:
        yield make_chunk(status="error", error_type="invalid_agent", error_message=str(e), meta=meta)
        return

    meta.update(
        {
            "query": query,
            "agent_slug": agent_item.slug,
            "backend_id": agent_item.backend_id,
            "thread_id": thread_id,
            "uid": current_user.uid,
            "has_image": bool(image_content),
        }
    )

    messages = [human_message]
    global_system_prompt = await get_enabled_global_agent_prompt(db)
    input_context = await build_agent_input_context(
        agent_config,
        thread_id=thread_id,
        uid=uid,
        run_id=meta.get("run_id"),
        request_id=meta.get("request_id"),
        global_system_prompt=global_system_prompt,
    )
    preprocess_result = preprocess(query)
    routing_row = await db.get(SystemKV, "model_routing")
    routing = routing_row.value if routing_row and isinstance(routing_row.value, dict) else {}
    if preprocess_result.intent == "graph_rag_query" and routing.get("intent_enabled", True):
        classified = await classify_with_api(query, routing)
        if classified and float(classified.get("confidence", 0)) >= 0.7:
            preprocess_result = type(preprocess_result)(classified["intent"], float(classified["confidence"]))
    meta["request_intent"] = preprocess_result.intent
    meta["request_intent_confidence"] = preprocess_result.confidence
    if preprocess_result.direct_answer is not None:
        yield make_chunk(preprocess_result.direct_answer, status="complete", meta=meta, fast_path=True)
        return
    # 记忆事实与权威修正注入（与中断续跑链路共用同一实现）。
    # 作用域：仅注入当前对话可访问知识库的修正（kb_id 为空的工单全局生效）。
    # 意图门槛：降级式——工单 intent_tags 非空且查询意图已知时才参与过滤（v3）。
    # 埋点（P3）：本次实际注入的修正与双路分数随回答消息落库，供监控与阈值校准（设计文档 §4.1）。
    injection_result = await _inject_memory_and_corrections(
        db,
        uid=uid,
        thread_id=thread_id,
        query=query,
        kb_ids=agent_config.get("knowledges") or [],
        intent=preprocess_result.intent,
        input_context=input_context,
    )
    correction_injections = injection_result.injections
    _apply_model_override(input_context, meta)
    _apply_subagent_runtime_context(input_context, meta)
    input_context["request_intent"] = preprocess_result.intent
    input_context["request_intent_confidence"] = preprocess_result.confidence
    input_context["intent_caliber"] = await _resolve_intent_caliber(db)
    if routing.get("chat_model_spec"):
        input_context["model"] = routing["chat_model_spec"]
    context = _build_agent_context(agent, input_context)
    langfuse_run = _build_langfuse_run_context(
        current_user=current_user,
        thread_id=thread_id,
        agent_id=agent_item.slug,
        backend_id=agent_item.backend_id,
        request_id=meta["request_id"],
        operation="agent_chat_stream",
        message_type=message_type,
        meta=meta,
    )
    full_msg = None
    accumulated_content: list[str] = []
    trace_info: dict[str, Any] = {}
    last_agent_state_signature = ""

    try:
        conv_repo = ConversationRepository(db)
        await _ensure_thread_bound_agent(
            conv_repo=conv_repo,
            thread_id=thread_id,
            uid=uid,
            agent_item=agent_item,
        )

        request_attachments = await _bind_request_attachments(
            conv_repo=conv_repo,
            thread_id=thread_id,
            request_id=meta["request_id"],
            attachment_file_ids=_normalize_attachment_file_ids(meta),
        )

        init_msg = {
            "role": "user",
            "content": query,
            "type": "human",
            "message_type": message_type,
            "extra_metadata": {
                "request_id": meta.get("request_id"),
                "attachments": request_attachments,
            },
        }
        if image_content:
            init_msg["image_content"] = image_content
        yield make_chunk(status="init", meta=meta, msg=init_msg)

        if save_user_message:
            try:
                await conv_repo.add_message_by_thread_id(
                    thread_id=thread_id,
                    role="user",
                    content=query,
                    message_type=message_type,
                    image_content=image_content,
                    # request_id 必须同时落到**列**上，不能只塞进 extra_metadata：
                    # assistant 行走的是列，user 行只放 metadata 会让同一轮的两条消息
                    # 一个能 join、一个 join 不到，按 request_id 复盘会话时凭空少一半。
                    request_id=meta.get("request_id"),
                    extra_metadata={
                        "raw_message": human_message.model_dump(),
                        "request_id": meta.get("request_id"),
                        "attachments": request_attachments,
                    },
                )
            except Exception as e:
                logger.error(f"Error saving user message: {e}")

        # 先构建 langgraph_config
        langgraph_config = {"configurable": {"thread_id": thread_id, "uid": uid}}

        # LangGraph 会自动从 checkpointer 恢复 state（包括 uploads）
        # 无需手动加载或传递

        protocol_message_ids: dict[tuple[str, str], str] = {}
        async for mode, payload in _stream_agent_events(
            agent,
            messages,
            input_context=input_context,
            callbacks=langfuse_run.callbacks,
            metadata=langfuse_run.metadata,
            tags=langfuse_run.tags,
        ):
            if mode == "values":
                agent_state = extract_agent_state(payload if isinstance(payload, dict) else {})
                signature = _agent_state_signature(agent_state)
                if signature and signature != last_agent_state_signature:
                    last_agent_state_signature = signature
                    yield make_chunk(status="agent_state", agent_state=agent_state, meta=meta)
                continue

            if mode == "custom":
                compression = _context_compression_payload(payload)
                if compression is not None:
                    yield make_chunk(status="context_compression", compression=compression, meta=meta)
                continue

            if mode == "stream_event":
                yield make_chunk(
                    status="stream_event",
                    event=payload,
                    namespace=payload.get("namespace") if isinstance(payload, dict) else [],
                    meta=meta,
                    thread_id=payload.get("thread_id") if isinstance(payload, dict) else None,
                )
                continue

            msg, metadata = payload
            namespace = _metadata_namespace(metadata)
            chunk_thread_id = _metadata_thread_id(metadata, thread_id if not namespace else None)
            if namespace and not chunk_thread_id:
                continue

            is_subagent_chunk = bool(chunk_thread_id and chunk_thread_id != thread_id)
            stream_events = _message_payload_yuxi_events(
                msg,
                metadata=metadata,
                namespace=namespace,
                thread_id=chunk_thread_id,
                protocol_message_ids=protocol_message_ids,
            )

            for stream_event in stream_events:
                content = _stream_event_response(stream_event)
                if not is_subagent_chunk and content:
                    trace_info = get_trace_info(langfuse_run)
                    accumulated_content.append(content)
                    content_for_check = "".join(accumulated_content[-10:])
                    if conf.enable_content_guard and await content_guard.check_with_keywords(content_for_check):
                        full_msg = AIMessage(content="".join(accumulated_content))
                        await save_partial_message(
                            conv_repo,
                            thread_id,
                            full_msg,
                            "content_guard_blocked",
                            trace_info=trace_info,
                            run_id=meta.get("run_id"),
                            request_id=meta.get("request_id"),
                        )
                        meta["time_cost"] = asyncio.get_event_loop().time() - start_time
                        yield make_chunk(status="interrupted", message="检测到敏感内容，已中断输出", meta=meta)
                        return

                yield make_chunk(
                    content=content,
                    stream_event=stream_event,
                    metadata=metadata,
                    status="loading",
                    thread_id=chunk_thread_id,
                )

        full_msg = _ensure_full_msg(full_msg, accumulated_content)
        trace_info = get_trace_info(langfuse_run)
        if correction_injections:
            trace_info["injected_corrections"] = correction_injections

        if conf.enable_content_guard and hasattr(full_msg, "content"):
            try:
                is_blocked = await content_guard.check(full_msg.content)
            except ContentGuardConfigurationError as exc:
                yield make_chunk(
                    status="error",
                    error_type="content_guard_configuration_error",
                    error_message=str(exc),
                    meta=meta,
                )
                return
            if is_blocked:
                await save_partial_message(
                    conv_repo,
                    thread_id,
                    full_msg,
                    "content_guard_blocked",
                    trace_info=trace_info,
                    run_id=meta.get("run_id"),
                    request_id=meta.get("request_id"),
                )
                meta["time_cost"] = asyncio.get_event_loop().time() - start_time
                yield make_chunk(status="interrupted", message="检测到敏感内容，已中断输出", meta=meta)
                return

        interrupted = False
        async for chunk in check_and_handle_interrupts(agent, langgraph_config, make_chunk, meta, thread_id, context):
            interrupted = True
            yield chunk

        meta["time_cost"] = asyncio.get_event_loop().time() - start_time
        try:
            graph = await agent.get_graph(context=context)
            state = await graph.aget_state(langgraph_config)
            agent_state = extract_agent_state(getattr(state, "values", {})) if state else {}
        except Exception:
            agent_state = {}

        final_signature = _agent_state_signature(agent_state)
        if final_signature and final_signature != last_agent_state_signature:
            last_agent_state_signature = final_signature
            yield make_chunk(status="agent_state", agent_state=agent_state, meta=meta)

        # 先存储数据库，再返回 finished，避免前端查询时数据未落库
        try:
            await save_messages_from_langgraph_state(
                agent_instance=agent,
                thread_id=thread_id,
                conv_repo=conv_repo,
                config_dict=langgraph_config,
                context=context,
                trace_info=trace_info,
                run_id=meta.get("run_id"),
                request_id=meta.get("request_id"),
            )
        except Exception as e:
            logger.exception(f"Error saving messages from LangGraph state: {e}")
            yield make_chunk(status="warning", message=f"消息保存失败: {e}", meta=meta)

        if interrupted:
            # 中断轮**刻意不入队**：这一轮的 output 只有半截，抽了会把游标推过去；
            # 续跑把回答补齐后，同一个 run 就再也抽不到了（游标只前进不后退）。
            # 留着不消费即可——续跑时会入队，用户直接问下一轮也会连批带它一起处理。
            return

        # 事实与长期记忆由后台任务独立抽取（yuxi.memory.extraction）。
        # 放在消息落库之后：抽取要按 input/output message 取回原文，落库前入队会白跑一趟。
        # 入队失败只记日志——记忆晚一轮生效可接受，但绝不能因此毁掉一次已生成的回答。
        try:
            await enqueue_turn_memory_extraction(thread_id)
        except Exception:
            logger.exception("failed to enqueue memory extraction")

        yield make_chunk(status="finished", meta=meta)

    except (asyncio.CancelledError, ConnectionError) as e:
        logger.warning(f"Client disconnected, cancelling stream: {e}")

        async def save_cleanup():
            nonlocal full_msg
            full_msg = _ensure_full_msg(full_msg, accumulated_content)

            async with pg_manager.get_async_session_context() as new_db:
                new_conv_repo = ConversationRepository(new_db)
                await save_partial_message(
                    new_conv_repo,
                    thread_id,
                    full_msg=full_msg,
                    error_message="对话已中断" if not full_msg else None,
                    error_type="interrupted",
                    trace_info=trace_info,
                    run_id=meta.get("run_id"),
                    request_id=meta.get("request_id"),
                )

        cleanup_task = asyncio.create_task(save_cleanup())
        try:
            await asyncio.shield(cleanup_task)
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.error(f"Error during cleanup save: {exc}")

        yield make_chunk(status="interrupted", message="对话已中断", meta=meta)

    except Exception as e:
        logger.exception(f"Error streaming messages: {e}")

        error_msg = f"Error streaming messages: {e}"
        error_type = "unexpected_error"

        full_msg = _ensure_full_msg(full_msg, accumulated_content)

        async with pg_manager.get_async_session_context() as new_db:
            new_conv_repo = ConversationRepository(new_db)
            await save_partial_message(
                new_conv_repo,
                thread_id,
                full_msg=full_msg,
                error_message=error_msg,
                error_type=error_type,
                trace_info=trace_info,
                run_id=meta.get("run_id"),
                request_id=meta.get("request_id"),
            )

        yield make_chunk(status="error", error_type=error_type, error_message=error_msg, meta=meta)
    finally:
        flush_langfuse()


async def stream_agent_resume(
    *,
    thread_id: str,
    resume_input: Any,
    meta: dict,
    current_user,
    db,
) -> AsyncIterator[bytes]:
    start_time = asyncio.get_event_loop().time()

    def make_resume_chunk(content=None, **kwargs):
        chunk_thread_id = kwargs.pop("thread_id", None) or meta.get("thread_id") or thread_id
        return (
            json.dumps(
                {"request_id": meta.get("request_id"), "response": content, "thread_id": chunk_thread_id, **kwargs},
                ensure_ascii=False,
            ).encode("utf-8")
            + b"\n"
        )

    yield make_resume_chunk(status="init", meta=meta)

    resume_command = Command(resume=resume_input)

    uid = str(current_user.uid)
    try:
        agent_item, agent, agent_config = await _resolve_agent_runtime(
            db=db,
            user=current_user,
            requested_agent_slug=None,
            thread_id=thread_id,
        )
    except ValueError as e:
        yield make_resume_chunk(status="error", error_type="invalid_agent", error_message=str(e), meta=meta)
        return

    meta["agent_slug"] = agent_item.slug
    meta["backend_id"] = agent_item.backend_id
    global_system_prompt = await get_enabled_global_agent_prompt(db)
    input_context = await build_agent_input_context(
        agent_config or {},
        thread_id=thread_id,
        uid=uid,
        run_id=meta.get("run_id"),
        request_id=meta.get("request_id"),
        global_system_prompt=global_system_prompt,
    )
    _apply_model_override(input_context, meta)
    # 回答链路不再产出任何协议块：抽取由后台任务独立调用完成（yuxi.memory.extraction），
    # 续跑与正常回答同一条纪律。口径仍按系统开关生效，不然中断恢复会悄悄松一档。
    input_context["intent_caliber"] = await _resolve_intent_caliber(db)
    # 续跑同样要受已审核的权威修正与会话事实约束，否则中断恢复后的回答会绕过权威层。
    # 续跑请求不带新 query，回查本线程最后一条用户消息作为检索输入。
    resume_query = await _last_user_query(db, thread_id)
    memory_enabled = False
    if resume_query:
        # 意图要同时驱动两件事：权威修正的过滤门槛（v3）与回答口径（U1）。
        # 只算一次、写回 input_context，否则中断恢复后的回答会比正常回答少一档口径。
        resume_intent = preprocess(resume_query)
        input_context["request_intent"] = resume_intent.intent
        input_context["request_intent_confidence"] = resume_intent.confidence
        resume_injection = await _inject_memory_and_corrections(
            db,
            uid=uid,
            thread_id=thread_id,
            query=resume_query,
            kb_ids=(agent_config or {}).get("knowledges") or [],
            intent=resume_intent.intent,
            input_context=input_context,
        )
        memory_enabled = resume_injection.memory_enabled
    context = _build_agent_context(agent, input_context)
    langfuse_run = _build_langfuse_run_context(
        current_user=current_user,
        thread_id=thread_id,
        agent_id=agent_item.slug,
        backend_id=agent_item.backend_id,
        request_id=meta.get("request_id") or str(uuid.uuid4()),
        operation="agent_chat_resume",
        message_type="resume",
        meta=meta,
    )
    trace_info: dict[str, Any] = {}
    last_agent_state_signature = ""

    stream_source = agent.stream_resume_with_state(
        resume_command,
        input_context=input_context,
        callbacks=langfuse_run.callbacks,
        metadata=langfuse_run.metadata,
        tags=langfuse_run.tags,
    )

    protocol_message_ids: dict[tuple[str, str], str] = {}

    try:
        async for mode, payload in stream_source:
            if mode == "values":
                agent_state = extract_agent_state(payload if isinstance(payload, dict) else {})
                signature = _agent_state_signature(agent_state)
                if signature and signature != last_agent_state_signature:
                    last_agent_state_signature = signature
                    yield make_resume_chunk(status="agent_state", agent_state=agent_state, meta=meta)
                continue

            if mode == "stream_event":
                event_payload = payload if isinstance(payload, dict) else {}
                yield make_resume_chunk(
                    status="stream_event",
                    event=event_payload,
                    namespace=event_payload.get("namespace") or [],
                    meta=meta,
                    thread_id=event_payload.get("thread_id"),
                )
                continue

            if mode == "custom":
                compression = _context_compression_payload(payload)
                if compression is not None:
                    yield make_resume_chunk(status="context_compression", compression=compression, meta=meta)
                continue

            if mode != "messages":
                continue

            msg, metadata = payload
            metadata = dict(metadata or {})
            namespace = _metadata_namespace(metadata)
            chunk_thread_id = _metadata_thread_id(metadata, thread_id if not namespace else None)
            if namespace and not chunk_thread_id:
                continue

            if chunk_thread_id == thread_id:
                trace_info = get_trace_info(langfuse_run)

            stream_events = _message_payload_yuxi_events(
                msg,
                metadata=metadata,
                namespace=namespace,
                thread_id=chunk_thread_id,
                protocol_message_ids=protocol_message_ids,
            )

            for stream_event in stream_events:
                content = _stream_event_response(stream_event)
                yield make_resume_chunk(
                    content=content,
                    stream_event=stream_event,
                    metadata=metadata,
                    status="loading",
                    thread_id=chunk_thread_id,
                )

        langgraph_config = {"configurable": {"thread_id": thread_id, "uid": uid}}
        interrupted = False
        async for chunk in check_and_handle_interrupts(
            agent, langgraph_config, make_resume_chunk, meta, thread_id, context
        ):
            interrupted = True
            yield chunk

        meta["time_cost"] = asyncio.get_event_loop().time() - start_time

        try:
            graph = await agent.get_graph(context=context)
            state = await graph.aget_state(langgraph_config)
            agent_state = extract_agent_state(getattr(state, "values", {})) if state else {}
        except Exception:
            agent_state = {}

        final_signature = _agent_state_signature(agent_state)
        if final_signature and final_signature != last_agent_state_signature:
            yield make_resume_chunk(status="agent_state", agent_state=agent_state, meta=meta)

        # 先存储数据库，再返回 finished，避免前端查询时数据未落库
        conv_repo = ConversationRepository(db)
        try:
            await save_messages_from_langgraph_state(
                agent_instance=agent,
                thread_id=thread_id,
                conv_repo=conv_repo,
                config_dict=langgraph_config,
                context=context,
                trace_info=trace_info,
                run_id=meta.get("run_id"),
                request_id=meta.get("request_id"),
            )
        except Exception as e:
            logger.exception(f"Error saving messages from LangGraph state: {e}")
            yield make_resume_chunk(status="warning", message=f"消息保存失败: {e}", meta=meta)

        if interrupted:
            # 同主链路：续跑再次被中断也不入队，把这一轮继续挂着等下一次补齐。
            return

        # 续跑入队：中断轮刻意没被消费（见主链路的说明），这里输出补齐后重放入队，
        # 游标就会连同该轮一起推进，不必单独维护"未抽取"的标记。
        try:
            await enqueue_turn_memory_extraction(thread_id)
        except Exception:
            logger.exception("failed to enqueue memory extraction on resume")

        yield make_resume_chunk(status="finished", meta=meta)

    except (asyncio.CancelledError, ConnectionError) as e:
        logger.warning(f"Client disconnected during resume: {e}")

        async with pg_manager.get_async_session_context() as new_db:
            new_conv_repo = ConversationRepository(new_db)
            await save_partial_message(
                new_conv_repo,
                thread_id,
                error_message="对话恢复已中断",
                error_type="resume_interrupted",
                trace_info=trace_info,
                run_id=meta.get("run_id"),
                request_id=meta.get("request_id"),
            )

        yield make_resume_chunk(status="interrupted", message="对话恢复已中断", meta=meta)

    except Exception as e:
        logger.exception(f"Error during resume: {e}")

        async with pg_manager.get_async_session_context() as new_db:
            new_conv_repo = ConversationRepository(new_db)
            await save_partial_message(
                new_conv_repo,
                thread_id,
                error_message=f"Error during resume: {e}",
                error_type="resume_error",
                trace_info=trace_info,
                run_id=meta.get("run_id"),
                request_id=meta.get("request_id"),
            )

        yield make_resume_chunk(message=f"Error during resume: {e}", status="error")
    finally:
        flush_langfuse()


def _serialize_state_messages(values: dict[str, Any]) -> list[dict[str, Any]]:
    messages = values.get("messages") if isinstance(values, dict) else None
    if not isinstance(messages, list):
        return []
    serialized = []
    for message in messages:
        if hasattr(message, "model_dump"):
            serialized.append(message.model_dump())
        elif isinstance(message, dict):
            serialized.append(dict(message))
        else:
            serialized.append({"type": "unknown", "content": str(message)})
    return serialized


async def _read_checkpoint_state(agent, *, uid: str, thread_id: str, context):
    graph = await agent.get_graph(context=context)
    langgraph_config = {"configurable": {"uid": uid, "thread_id": thread_id}}
    return await graph.aget_state(langgraph_config)


async def get_agent_state_view(
    *,
    thread_id: str,
    current_user: User,
    db,
    include_messages: bool = False,
) -> dict:
    from fastapi import HTTPException

    current_uid = str(current_user.uid)
    conv_repo = ConversationRepository(db)
    agent_repo = AgentRepository(db)
    run_repo = AgentRunRepository(db)
    conversation = await conv_repo.get_conversation_by_thread_id(thread_id)
    if conversation:
        if conversation.uid != str(current_uid) or conversation.status == "deleted":
            raise HTTPException(status_code=404, detail="对话线程不存在")

        agent_item = await agent_repo.get_by_slug(conversation.agent_id)
        if not agent_item:
            raise HTTPException(status_code=404, detail="智能体不存在")
        agent = agent_manager.get_agent(agent_item.backend_id)
        if not agent:
            raise HTTPException(status_code=404, detail="智能体后端不存在")
        agent_config = await normalize_agent_context_config(
            (agent_item.config_json or {}).get("context", {}),
            db=db,
            user=current_user,
            context_schema=agent.context_schema,
        )
        global_system_prompt = await get_enabled_global_agent_prompt(db)
        input_context = await build_agent_input_context(
            agent_config,
            thread_id=thread_id,
            uid=current_uid,
            global_system_prompt=global_system_prompt,
        )
        latest_run = await run_repo.get_latest_run_by_thread_for_user(thread_id, current_uid)
        if latest_run and isinstance(latest_run.input_payload, dict):
            model_spec = latest_run.input_payload.get("model_spec")
            if isinstance(model_spec, str) and model_spec.strip():
                input_context["model"] = model_spec.strip()
        context = _build_agent_context(agent, input_context)
        state = await _read_checkpoint_state(agent, uid=current_uid, thread_id=thread_id, context=context)
        values = getattr(state, "values", {}) if state else {}
        response = {"agent_state": extract_agent_state(values)}
        relation = await SubagentThreadRepository(db).get_by_child_conversation_for_user(
            conversation.id,
            str(current_uid),
        )
        if relation:
            parent_conversation = await conv_repo.get_conversation_by_id(relation.parent_conversation_id)
            if (
                not parent_conversation
                or parent_conversation.uid != str(current_uid)
                or parent_conversation.status == "deleted"
            ):
                raise HTTPException(status_code=404, detail="父对话线程不存在")
            response["parent_thread_id"] = parent_conversation.thread_id
            response["subagent_thread"] = relation.to_dict()
            latest_run = await run_repo.get_latest_subagent_run_by_thread_for_user(
                thread_id,
                str(current_uid),
            )
            if latest_run:
                try:
                    response["subagent_run"] = serialize_subagent_run_state(latest_run)
                except ValueError as exc:
                    logger.error(f"子智能体运行记录格式异常: thread_id={thread_id}, run_id={latest_run.id}, {exc}")
                    raise HTTPException(status_code=500, detail="子智能体运行记录格式异常") from exc
        if include_messages:
            response["messages"] = _serialize_state_messages(values)
        return response

    # 子智能体线程在创建时必然同时写入子对话与线程关系（见 SubagentRunService.start），
    # 由上面的 conversation 分支统一处理；走到这里说明该 thread 没有对应对话，即线程不存在。
    raise HTTPException(status_code=404, detail="对话线程不存在")
