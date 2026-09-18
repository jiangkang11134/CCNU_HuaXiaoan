from deepagents.middleware.patch_tool_calls import PatchToolCallsMiddleware
from langchain.agents import create_agent
from langchain.agents.middleware import ModelRetryMiddleware, TodoListMiddleware

from yuxi.agents import BaseAgent, load_chat_model, resolve_chat_model_spec
from yuxi.agents.backends import create_agent_filesystem_middleware
from yuxi.agents.context import (
    DEFAULT_SUMMARY_KEEP_MESSAGES,
    DEFAULT_SUMMARY_L2_TRIGGER_RATIO,
    DEFAULT_SUMMARY_THRESHOLD_K,
    DEFAULT_SUMMARY_TOOL_RESULT_TOKEN_LIMIT,
    DEFAULT_TOOL_RESULT_EVICTION_K_TOKENS,
    DEFAULT_YUXI_SUMMARY_PROMPT,
    prepare_agent_runtime_context,
)
from yuxi.agents.middlewares import (
    TokenUsageMiddleware,
    create_summary_middleware,
    save_attachments_to_fs,
)
from yuxi.agents.middlewares.skills import SkillsMiddleware
from yuxi.agents.middlewares.subagent_task import create_subagent_task_middleware
from yuxi.agents.toolkits.service import resolve_configured_runtime_tools
from yuxi.utils import logger

from .context import ChatBotContext
from .prompt import TODO_MID_PROMPT, build_prompt_with_context
from .state import ChatBotState

# 主备故障切换配置短期缓存：避免每次建图都查库，30 秒内复用。
_FALLBACK_ROUTING_CACHE: dict = {"at": 0.0, "specs": None}
_FALLBACK_ROUTING_TTL_SECONDS = 30.0


async def _load_chat_fallback_specs() -> list[str]:
    """读取主备故障切换的备用模型链。

    未启用或读取失败时返回空链，此时 load_chat_model 行为与改动前完全一致，
    避免配置/数据库异常反过来阻断正常对话。
    """
    import time

    now = time.monotonic()
    cache = _FALLBACK_ROUTING_CACHE
    if cache["specs"] is not None and now - cache["at"] < _FALLBACK_ROUTING_TTL_SECONDS:
        return list(cache["specs"])

    specs: list[str] = []
    try:
        from yuxi.storage.postgres.manager import pg_manager
        from yuxi.storage.postgres.models_business import SystemKV

        async with pg_manager.get_async_session_context() as db:
            row = await db.get(SystemKV, "model_routing")
        routing = row.value if row and isinstance(row.value, dict) else {}
        if routing.get("chat_fallback_enabled"):
            specs = [s.strip() for s in (routing.get("chat_fallback_specs") or []) if isinstance(s, str) and s.strip()]
    except Exception as e:
        logger.warning(f"读取主备切换配置失败，按未启用处理: {e}")
        specs = []

    cache["specs"] = list(specs)
    cache["at"] = now
    return specs


async def _build_middlewares(context, fallback_specs: list[str] | None = None):
    """构建中间件列表"""
    # summary middleware
    # 主 Agent 上下文优化：默认 100k tokens 触发压缩，压缩后保留最近 10 条消息
    summary_trigger_tokens = getattr(context, "summary_threshold", DEFAULT_SUMMARY_THRESHOLD_K) * 1024
    summary_keep_messages = getattr(context, "summary_keep_messages", DEFAULT_SUMMARY_KEEP_MESSAGES)
    summary_prompt = getattr(context, "summary_prompt", None) or DEFAULT_YUXI_SUMMARY_PROMPT
    summary_tool_result_token_limit = getattr(
        context,
        "summary_tool_result_token_limit",
        DEFAULT_SUMMARY_TOOL_RESULT_TOKEN_LIMIT,
    )
    summary_l2_trigger_ratio = getattr(context, "summary_l2_trigger_ratio", DEFAULT_SUMMARY_L2_TRIGGER_RATIO)
    model_spec = resolve_chat_model_spec(context.model)
    summary_middleware = create_summary_middleware(
        model=load_chat_model(fully_specified_name=model_spec, fallback_specs=fallback_specs),
        trigger=("tokens", summary_trigger_tokens),
        keep=("messages", summary_keep_messages),
        summary_prompt=summary_prompt,
        trim_tokens_to_summarize=summary_trigger_tokens,
        tool_result_offload_token_limit=summary_tool_result_token_limit,
        l1_l2_trigger_ratio=summary_l2_trigger_ratio,
    )

    middlewares = [
        create_agent_filesystem_middleware(
            getattr(context, "tool_token_limit", DEFAULT_TOOL_RESULT_EVICTION_K_TOKENS) * 1024,
            context=context,
        ),
        save_attachments_to_fs,
        SkillsMiddleware(),
    ]
    subagent_middleware = await create_subagent_task_middleware(context)
    if subagent_middleware:
        middlewares.append(subagent_middleware)
    middlewares.extend(
        [
            summary_middleware,
            TodoListMiddleware(system_prompt=TODO_MID_PROMPT),
            PatchToolCallsMiddleware(),
            ModelRetryMiddleware(max_retries=getattr(context, "model_retry_times", 2)),
            TokenUsageMiddleware(),
        ]
    )
    return middlewares


class ChatbotAgent(BaseAgent):
    name = "智能助手"
    description = "基础的对话机器人，可以回答问题，可在配置中启用需要的工具。"
    capabilities = ["file_upload", "files"]  # 支持文件上传功能
    context_schema = ChatBotContext

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    async def get_graph(self, context=None, **kwargs):

        context = await prepare_agent_runtime_context(
            context or self.context_schema(),
            context_schema=self.context_schema,
        )

        # 使用 create_agent 创建智能体
        model_spec = resolve_chat_model_spec(context.model)
        fallback_specs = await _load_chat_fallback_specs()
        graph = create_agent(
            model=load_chat_model(fully_specified_name=model_spec, fallback_specs=fallback_specs),
            tools=await resolve_configured_runtime_tools(context),
            system_prompt=build_prompt_with_context(context),
            middleware=await _build_middlewares(context, fallback_specs),
            state_schema=ChatBotState,
            checkpointer=await self._get_checkpointer(),
        )

        return graph


def main():
    pass


if __name__ == "__main__":
    main()
    # asyncio.run(main())
