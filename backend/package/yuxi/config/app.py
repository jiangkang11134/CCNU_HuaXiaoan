"""应用配置模块。"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse

import tomli
import tomli_w
from pydantic import BaseModel, Field, PrivateAttr

from yuxi.config import cache as runtime_cache
from yuxi.utils.logging_config import logger

READONLY_CONFIG_FIELDS = frozenset({"save_dir"})
DEFAULT_OCR_ENGINE = "rapid_ocr"
DEFAULT_MINERU_API_BASE_URL = "https://mineru.net/api/v4"
DEFAULT_PADDLEOCR_API_URL = "https://paddleocr.aistudio-app.com/api/v2/ocr/jobs"


def _env_str(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def _env_int(name: str, default: int) -> int:
    value = (os.getenv(name) or "").strip()
    return int(value) if value else default


def _env_list(name: str) -> list[str]:
    value = (os.getenv(name) or "").strip()
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def _get_available_ocr_engines() -> set[str]:
    from yuxi.config.ocr import get_available_processor_types

    return {"disable", *get_available_processor_types()}


def _normalize_default_ocr_engine(value: Any) -> str:
    engine = str(value or "").strip() or DEFAULT_OCR_ENGINE
    if engine not in _get_available_ocr_engines():
        raise ValueError(f"不支持的默认 OCR 引擎: {engine}")
    return engine


def _normalize_mineru_api_uri(value: Any) -> str:
    uri = str(value or "").strip().rstrip("/")
    if not uri:
        raise ValueError("MinerU 官方 API 基础地址不能为空")

    parsed = urlparse(uri)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("MinerU 官方 API 基础地址必须是完整的 http(s) 地址")
    if parsed.params or parsed.query or parsed.fragment:
        raise ValueError("MinerU 官方 API 基础地址不能包含参数、查询字符串或片段")

    normalized_path = parsed.path.rstrip("/")
    if normalized_path not in {"", "/api/v4"}:
        raise ValueError("MinerU 官方 API 地址应填写基础地址，例如 https://mineru.net/api/v4，不要填写具体任务接口")

    return urlunparse((parsed.scheme, parsed.netloc, normalized_path, "", "", ""))


class Config(BaseModel):
    """应用配置类。

    `save_dir` 只在启动时决定配置文件位置，运行时不可修改。管理员保存配置时先写
    `base.toml`，再把可运行时同步的字段写入 Redis 快照（`yuxi:runtime_config`）。
    其他进程通过 `start_runtime_sync()` 启动的后台线程周期性拉取该快照刷新内存值。
    """

    save_dir: str = Field(default="saves", description="保存目录", exclude=True)
    enable_content_guard: bool = Field(default=False, description="是否启用内容审查")
    enable_content_guard_llm: bool = Field(default=False, description="是否启用LLM内容审查")
    default_model: str = Field(
        default="siliconflow-cn:Pro/MiniMaxAI/MiniMax-M2.5",
        description="默认对话模型",
    )
    fast_model: str = Field(
        default="siliconflow-cn:Pro/MiniMaxAI/MiniMax-M2.5",
        description="快速响应模型",
    )
    embed_model: str = Field(
        default="siliconflow-cn:Pro/BAAI/bge-m3",
        description="默认 Embedding 模型",
    )
    reranker: str = Field(
        default="siliconflow-cn:Pro/BAAI/bge-reranker-v2-m3",
        description="默认 Re-Ranker 模型",
    )
    content_guard_llm_model: str = Field(
        default="siliconflow-cn:Pro/MiniMaxAI/MiniMax-M2.5",
        description="内容审查LLM模型",
    )
    default_ocr_engine: str = Field(default=DEFAULT_OCR_ENGINE, description="默认 OCR 解析引擎")
    tavily_api_key: str = Field(
        default_factory=lambda: _env_str("TAVILY_API_KEY"),
        description="Tavily 网页搜索 API Key",
    )
    url_whitelist: list[str] = Field(
        default_factory=lambda: _env_list("YUXI_URL_WHITELIST"),
        description="URL 解析白名单域名，留空时关闭 URL 解析",
    )
    mineru_api_key: str = Field(
        default_factory=lambda: _env_str("MINERU_API_KEY"),
        description="MinerU 官方 API Key",
    )
    mineru_api_uri: str = Field(
        default_factory=lambda: _env_str("MINERU_API_URI", DEFAULT_MINERU_API_BASE_URL),
        description="MinerU 官方 API 地址",
    )
    mineru_timeout_seconds: int = Field(
        default_factory=lambda: _env_int("MINERU_TIMEOUT", 1800),
        description="MinerU 解析超时时间（秒）",
    )
    paddleocr_api_token: str = Field(
        default_factory=lambda: _env_str("PADDLEOCR_API_TOKEN"),
        description="PaddleOCR 云服务 API Token",
    )
    paddleocr_api_url: str = Field(
        default_factory=lambda: _env_str("PADDLEOCR_API_URL", DEFAULT_PADDLEOCR_API_URL),
        description="PaddleOCR 云服务任务地址",
    )
    deepseek_ocr_api_key: str = Field(
        default_factory=lambda: _env_str("SILICONFLOW_API_KEY"),
        description="DeepSeek OCR 使用的 SiliconFlow API Key",
    )

    sandbox_provider: str = Field(
        default_factory=lambda: _env_str("SANDBOX_PROVIDER", "provisioner"),
        description="沙箱提供者",
    )
    sandbox_provisioner_url: str = Field(
        default_factory=lambda: _env_str("SANDBOX_PROVISIONER_URL", "http://sandbox-provisioner:8002"),
        description="沙箱服务地址",
    )
    sandbox_virtual_path_prefix: str = Field(
        default_factory=lambda: _env_str("SANDBOX_VIRTUAL_PATH_PREFIX", "/home/gem/user-data"),
        description="沙箱用户目录前缀",
    )
    sandbox_exec_timeout_seconds: int = Field(
        default_factory=lambda: _env_int("SANDBOX_EXEC_TIMEOUT_SECONDS", 180),
        description="沙箱执行超时时间（秒）",
    )
    sandbox_max_output_bytes: int = Field(
        default_factory=lambda: _env_int("SANDBOX_MAX_OUTPUT_BYTES", 262144),
        description="沙箱最大输出字节数",
    )
    sandbox_keepalive_interval_seconds: int = Field(
        default_factory=lambda: _env_int("SANDBOX_KEEPALIVE_INTERVAL_SECONDS", 30),
        description="沙箱保活间隔",
    )

    _config_file: Path | None = PrivateAttr(default=None)
    _runtime_sync_thread: Any = PrivateAttr(default=None)

    model_config = {"arbitrary_types_allowed": True, "extra": "allow"}

    def __init__(self, **data):
        super().__init__(**data)
        self._setup_paths()
        self._load_user_config()
        self._handle_environment()

    def _setup_paths(self) -> None:
        self._config_file = Path(self.save_dir) / "config" / "base.toml"
        self._config_file.parent.mkdir(parents=True, exist_ok=True)

    def _load_user_config(self) -> None:
        if not self._config_file or not self._config_file.exists():
            logger.info(f"Config file not found, using defaults: {self._config_file}")
            return

        logger.info(f"Loading config from {self._config_file}")
        try:
            with open(self._config_file, "rb") as f:
                user_config = tomli.load(f)

            for key, value in user_config.items():
                if key in READONLY_CONFIG_FIELDS:
                    logger.warning(f"Readonly config key ignored: {key}")
                elif key in type(self).model_fields:
                    try:
                        setattr(self, key, self._normalize_config_value(key, value))
                    except ValueError as exc:
                        logger.warning(f"Invalid config key ignored: {key} ({exc})")
                else:
                    logger.warning(f"Unknown config key: {key}")

        except Exception as e:
            logger.error(f"Failed to load config from {self._config_file}: {e}")

    def _handle_environment(self) -> None:
        self.sandbox_provider = (self.sandbox_provider or "provisioner").strip()
        self.sandbox_provisioner_url = (self.sandbox_provisioner_url or "").strip()
        self.sandbox_virtual_path_prefix = (self.sandbox_virtual_path_prefix or "").strip()
        if self.sandbox_provider.lower() != "provisioner":
            raise ValueError("Only sandbox_provider=provisioner is supported.")
        if not self.sandbox_provisioner_url:
            raise ValueError("sandbox_provisioner_url is required when sandbox provider is provisioner.")
        if not self.sandbox_virtual_path_prefix.startswith("/"):
            self.sandbox_virtual_path_prefix = f"/{self.sandbox_virtual_path_prefix}"

    def start_runtime_sync(self, interval: float = runtime_cache.RUNTIME_CONFIG_SYNC_INTERVAL_SECONDS) -> None:
        """启动后台线程周期性从 Redis 同步运行时配置。多次调用仅启动一次。"""
        self._runtime_sync_thread = runtime_cache.start_runtime_sync(
            self,
            self._runtime_sync_thread,
            interval=interval,
        )

    def refresh(self) -> None:
        """从 Redis 快照刷新公开配置字段到内存；Redis 不可用或无快照时保持当前值。"""
        runtime_cache.refresh_runtime_config(self)

    def save(self) -> None:
        if not self._config_file:
            logger.warning("Config file path not set")
            return

        logger.info(f"Saving config to {self._config_file}")
        user_modified = {}
        for field_name, field_info in type(self).model_fields.items():
            if field_info.exclude:
                continue
            current_value = getattr(self, field_name)
            if current_value != field_info.default:
                user_modified[field_name] = current_value

        try:
            with open(self._config_file, "wb") as f:
                tomli_w.dump(user_modified, f)
            logger.info(f"Config saved to {self._config_file}")
            runtime_cache.save_runtime_config(self)
        except Exception as e:
            logger.error(f"Failed to save config to {self._config_file}: {e}")
            raise RuntimeError(f"配置文件保存失败: {e}") from e

    def dump_config(self) -> dict[str, Any]:
        config_dict = self.model_dump()
        fields_info = {}
        for field_name, field_info in Config.model_fields.items():
            if field_info.exclude:
                continue
            fields_info[field_name] = {
                "des": field_info.description,
                "default": field_info.default,
                "type": field_info.annotation.__name__
                if hasattr(field_info.annotation, "__name__")
                else str(field_info.annotation),
                "exclude": field_info.exclude if hasattr(field_info, "exclude") else False,
            }
        config_dict["_config_items"] = fields_info
        return config_dict

    def update(self, other: dict[str, Any]) -> None:
        for key, value in other.items():
            if self.can_update(key):
                self.set_value(key, value)
            elif key in READONLY_CONFIG_FIELDS:
                logger.warning(f"Readonly config key ignored: {key}")
            else:
                logger.warning(f"Unknown config key: {key}")

    def can_update(self, key: object) -> bool:
        return isinstance(key, str) and key in type(self).model_fields and key not in READONLY_CONFIG_FIELDS

    def set_value(self, key: str, value: Any) -> None:
        if not self.can_update(key):
            raise ValueError(f"配置项不可修改: {key}")
        setattr(self, key, self._normalize_config_value(key, value))

    def _normalize_config_value(self, key: str, value: Any) -> Any:
        if key == "default_ocr_engine":
            return _normalize_default_ocr_engine(value)
        if key == "url_whitelist":
            if isinstance(value, str):
                return [item.strip() for item in value.split(",") if item.strip()]
            if isinstance(value, list):
                return [str(item).strip() for item in value if str(item).strip()]
            raise ValueError("url_whitelist must be a list or comma-separated string")
        if key in {
            "mineru_timeout_seconds",
            "sandbox_exec_timeout_seconds",
            "sandbox_max_output_bytes",
            "sandbox_keepalive_interval_seconds",
        }:
            normalized = int(value)
            if normalized <= 0:
                raise ValueError(f"{key} must be greater than 0")
            return normalized
        if key == "mineru_api_uri":
            return _normalize_mineru_api_uri(value)
        if key in {
            "tavily_api_key",
            "mineru_api_key",
            "paddleocr_api_token",
            "paddleocr_api_url",
            "deepseek_ocr_api_key",
            "sandbox_provider",
            "sandbox_provisioner_url",
            "sandbox_virtual_path_prefix",
        }:
            return str(value or "").strip()
        return value


config = Config()
