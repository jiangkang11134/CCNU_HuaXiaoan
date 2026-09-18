import random
from typing import Any

from langchain.chat_models import BaseChatModel
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_openai import ChatOpenAI
from pydantic import Field, SecretStr

from yuxi import config as sys_config
from yuxi.models.providers.cache import model_cache
from yuxi.utils import get_docker_safe_url
from yuxi.utils.logging_config import logger

_TOOL_IMAGE_USER_TEXT = "Images returned by read_file are attached below. Inspect them when answering."


def resolve_chat_model_spec(model_spec: str | None, *, fallback: str | None = None) -> str:
    """解析空模型配置，不吞掉已经配置但无效的模型值。

    这里仅处理模型为空时的优先级：请求或配置值、调用方 fallback、系统默认模型；
    具体模型是否存在、是否为聊天模型仍由 model_cache 校验。
    """
    for candidate in (model_spec, fallback, sys_config.default_model):
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    raise ValueError("model spec 不能为空")


def resolve_chat_model_chain(
    primary_spec: str,
    fallback_specs: list[str] | None = None,
) -> list[str]:
    """把主模型与备用模型拼成有序调用链，去重且保持顺序。

    备用只是"主不可用时的替补"，因此主模型即使重复出现在备用列表里也只保留首次出现，
    空串与空白项直接丢弃。
    """
    chain: list[str] = []
    seen: set[str] = set()
    for candidate in (primary_spec, *(fallback_specs or [])):
        if not isinstance(candidate, str):
            continue
        spec = candidate.strip()
        if not spec or spec in seen:
            continue
        seen.add(spec)
        chain.append(spec)
    return chain


class FallbackChatModel(BaseChatModel):
    """按 spec 顺序故障切换的聊天模型包装器。

    保持 BaseChatModel 身份，使 create_agent 仍能 bind_tools；bind_tools 返回副本
    而不是"绑定后的单个模型"，否则工具绑定会把整条备用链丢掉。

    流式只在尚未产出第一个 chunk 前切换：已经吐给前端的内容无法撤回，切到备用会造成
    内容重复，因此一旦有输出就认定该模型可用、不再切换。
    """

    specs: list[str] = Field(default_factory=list, description="有序模型 spec 链，首个为主模型")
    tools: Any = Field(default=None, exclude=True, description="待绑定工具，bind_tools 后透传给链上每个模型")
    tool_kwargs: dict[str, Any] = Field(default_factory=dict, exclude=True)
    # 构造底层模型时的透传参数（temperature 等），备用模型同样生效，避免主备行为不一致。
    model_kwargs: Any = Field(default_factory=dict, exclude=True)

    @property
    def _llm_type(self) -> str:
        return "chat-model-fallback"

    def _build_models(self) -> list[tuple[str, BaseChatModel]]:
        """按序构造链上模型，返回 (spec, model) 对。

        单个备用 spec 失效（被删、拼错）时只跳过它，绝不能让坏备用把能用的主模型一起拖挂。
        """
        built: list[tuple[str, BaseChatModel]] = []
        for spec in self.specs:
            try:
                model = load_chat_model(spec, **dict(self.model_kwargs or {}))
            except Exception as e:  # noqa: BLE001
                logger.warning(f"主备切换: 跳过无法加载的模型 {spec}: {e}")
                continue
            if self.tools is not None:
                model = model.bind_tools(self.tools, **self.tool_kwargs)
            built.append((spec, model))
        return built

    def bind_tools(self, tools, **kwargs):
        """绑定工具并保留整条备用链。"""
        return self.model_copy(update={"tools": tools, "tool_kwargs": dict(kwargs)})

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        last_error: Exception | None = None
        for position, (spec, model) in enumerate(self._build_models()):
            try:
                message = model.invoke(messages, stop=stop, **kwargs)
            except Exception as e:  # noqa: BLE001 - 故障切换需要逐个吞掉模型失败
                last_error = e
                logger.warning(f"主备切换: 模型 {spec} 调用失败，尝试下一个: {e}")
                continue
            if position:
                logger.info(f"主备切换: 已由备用模型 {spec} 接管")
            return ChatResult(generations=[ChatGeneration(message=message)])
        raise last_error or RuntimeError("FallbackChatModel 没有可用模型")

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        last_error: Exception | None = None
        for position, (spec, model) in enumerate(self._build_models()):
            try:
                message = await model.ainvoke(messages, stop=stop, **kwargs)
            except Exception as e:  # noqa: BLE001
                last_error = e
                logger.warning(f"主备切换: 模型 {spec} 调用失败，尝试下一个: {e}")
                continue
            if position:
                logger.info(f"主备切换: 已由备用模型 {spec} 接管")
            return ChatResult(generations=[ChatGeneration(message=message)])
        raise last_error or RuntimeError("FallbackChatModel 没有可用模型")

    def _stream(self, messages, stop=None, run_manager=None, **kwargs):
        last_error: Exception | None = None
        for position, (spec, model) in enumerate(self._build_models()):
            try:
                iterator = model.stream(messages, stop=stop, **kwargs)
                first = next(iterator)
            except StopIteration:
                return
            except Exception as e:  # noqa: BLE001
                last_error = e
                logger.warning(f"主备切换: 模型 {spec} 流式调用失败，尝试下一个: {e}")
                continue
            if position:
                logger.info(f"主备切换: 已由备用模型 {spec} 接管")
            yield first
            yield from iterator
            return
        if last_error:
            raise last_error

    async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
        last_error: Exception | None = None
        for position, (spec, model) in enumerate(self._build_models()):
            try:
                iterator = model.astream(messages, stop=stop, **kwargs)
                # 复用同一个迭代器：重新调用 astream 会重启生成，导致首 chunk 重复。
                first = await anext(iterator)
            except StopAsyncIteration:
                return
            except Exception as e:  # noqa: BLE001
                last_error = e
                logger.warning(f"主备切换: 模型 {spec} 流式调用失败，尝试下一个: {e}")
                continue
            if position:
                logger.info(f"主备切换: 已由备用模型 {spec} 接管")
            yield first
            async for chunk in iterator:
                yield chunk
            return
        if last_error:
            raise last_error


def load_chat_model(
    fully_specified_name: str | None,
    fallback_specs: list[str] | None = None,
    **kwargs,
) -> BaseChatModel:
    fully_specified_name = resolve_chat_model_spec(fully_specified_name)

    info = model_cache.get_model_info(fully_specified_name)
    if not info:
        available_specs = model_cache.get_all_specs("chat")
        available_ids = [item.spec for item in available_specs[:10]]
        raise ValueError(
            f"Unknown model spec: '{fully_specified_name}'. "
            f"Available chat models ({len(available_specs)}): {available_ids}"
        )

    if info.model_type != "chat":
        raise ValueError(f"Model {fully_specified_name} is not a chat model (type={info.model_type})")

    # 配了备用链就整体交给包装器：由它按序构造并在失败时切换。提前返回避免主模型被构造两次。
    chain = resolve_chat_model_chain(fully_specified_name, fallback_specs)
    if len(chain) > 1:
        return FallbackChatModel(specs=chain, model_kwargs=dict(kwargs))

    # 多账号按权重随机选择，单次模型实例固定端点，避免流式请求中途切换。
    endpoint = None
    enabled_accounts = [a for a in info.accounts if a.get("enabled", True) and (a.get("api_key") or a.get("base_url"))]
    if enabled_accounts:
        expanded = [a for a in enabled_accounts for _ in range(max(1, int(a.get("weight", 1))))]
        endpoint = random.choice(expanded)
    api_key = (endpoint or {}).get("api_key") or info.api_key
    base_url = get_docker_safe_url((endpoint or {}).get("base_url") or info.base_url)

    logger.debug(f"Loading model {fully_specified_name} with provider_type={info.provider_type}")

    if info.provider_type == "anthropic":
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(
            model=info.model_id,
            api_key=SecretStr(api_key),
            base_url=base_url,
            **kwargs,
        )
    if info.provider_type == "gemini":
        from langchain_google_genai import ChatGoogleGenerativeAI

        return ChatGoogleGenerativeAI(
            model=info.model_id,
            google_api_key=SecretStr(api_key),
            **kwargs,
        )

    return _ToolCallChunkFixChatOpenAI(
        model=info.model_id,
        api_key=SecretStr(api_key),
        base_url=base_url,
        stream_usage=True,
        **kwargs,
    )


class _ToolCallChunkFixChatOpenAI(ChatOpenAI):
    """归一化流式 tool_call 续片中的空串 name/id，规避 v3 流式累积缺陷。"""

    def _get_request_payload(self, input_, *, stop=None, **kwargs):
        """Override to bridge tool image blocks to user messages."""
        payload = super()._get_request_payload(input_, stop=stop, **kwargs)
        return _bridge_tool_images_to_user_messages(payload)

    async def _astream(self, *args, **kwargs):
        async for chunk in super()._astream(*args, **kwargs):
            _normalize_tool_call_chunks(chunk.message)
            yield chunk

    def _stream(self, *args, **kwargs):
        for chunk in super()._stream(*args, **kwargs):
            _normalize_tool_call_chunks(chunk.message)
            yield chunk


def _bridge_tool_images_to_user_messages(payload: dict[str, Any]) -> dict[str, Any]:
    """将工具调用返回的 image_url 块桥接到用户消息中，避免工具消息中包含图片导致的渲染问题。"""
    messages = payload.get("messages")
    if not isinstance(messages, list):
        return payload
    if not any(isinstance(m, dict) and m.get("role") == "tool" and _tool_image_blocks(m) for m in messages):
        return payload

    bridged_messages: list[dict[str, Any]] = []
    pending_images: list[dict[str, Any]] = []

    def flush_pending_images() -> None:
        nonlocal pending_images
        if not pending_images:
            return

        bridged_messages.append(
            {
                "role": "user",
                "content": [{"type": "text", "text": _TOOL_IMAGE_USER_TEXT}, *pending_images],
            }
        )
        pending_images = []

    for message in messages:
        if not isinstance(message, dict):
            flush_pending_images()
            bridged_messages.append(message)
            continue

        role = message.get("role")
        if role != "tool":
            flush_pending_images()

        image_blocks = _tool_image_blocks(message) if role == "tool" else []
        if image_blocks:
            pending_images.extend(image_blocks)

            content = _text_without_images(message.get("content"), image_blocks)
            if not content:
                content = (
                    f"read_file returned {len(image_blocks)} image(s). "
                    "The image content is attached in the following user message for visual inspection."
                )
            message = {**message, "content": content}

        bridged_messages.append(message)

    flush_pending_images()

    return {**payload, "messages": bridged_messages}


def _normalize_tool_call_chunks(message) -> None:
    """把工具调用续片里空字符串的 name/id 归一化为 None。

    LangGraph v3 流式累积对 tool_call 字段是“后值覆盖”：部分 OpenAI 兼容提供商
    （siliconflow、阿里云百炼等）在续片里把 name/id 下发为空字符串 ""，会覆盖首片
    的真实值（siliconflow 丢 name、百炼丢 id），导致工具结果无法按 tool_call_id
    关联、工具状态停留在“进行中”。OpenAI 官方在续片里发 None 不会触发覆盖，这里
    把空串归一化为 None 对齐该行为。待上游修复 v3 协议后可移除。
    """
    for chunk in message.tool_call_chunks:
        if chunk.get("name") == "":
            chunk["name"] = None
        if chunk.get("id") == "":
            chunk["id"] = None


def _tool_image_blocks(message: dict[str, Any]) -> list[dict[str, Any]]:
    content = message.get("content")
    if not isinstance(content, list):
        return []
    return [
        block
        for block in content
        if isinstance(block, dict)
        and block.get("type") == "image_url"
        and isinstance(block.get("image_url"), dict)
        and isinstance(block["image_url"].get("url"), str)
    ]


def _text_without_images(content: Any, image_blocks: list[dict[str, Any]]) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""

    image_ids = {id(block) for block in image_blocks}
    parts: list[str] = []
    for block in content:
        if id(block) in image_ids:
            continue
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict) and block.get("type") in {"text", "input_text"}:
            text = block.get("text")
            if isinstance(text, str):
                parts.append(text)
    return "\n".join(part for part in parts if part)
