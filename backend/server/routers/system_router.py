import os
import uuid
from pathlib import Path

import aiofiles
import yaml
from fastapi import APIRouter, Body, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession
from yuxi import config, get_version
from yuxi.storage.postgres.models_business import SystemKV, User
from yuxi.utils.upload_utils import read_upload_with_limit
from yuxi.utils.logging_config import logger

from server.utils.auth_middleware import get_admin_user, get_db, get_required_user
from yuxi.services.agent_prompt_policy import (
    get_global_agent_prompt_config as load_global_agent_prompt_config,
    get_global_agent_prompt_record,
    normalize_global_agent_prompt_config,
)
from server.utils.frontend_portal_policy import (
    FRONTEND_CHAT_CONFIG_KEY,
    get_frontend_chat_config as load_frontend_chat_config,
    get_frontend_chat_config_record,
    list_chat_agents,
    normalize_frontend_chat_config,
)

system = APIRouter(prefix="/system", tags=["system"])
SENSITIVE_CONFIG_FIELDS = frozenset(
    {
        "tavily_api_key",
        "mineru_api_key",
        "paddleocr_api_token",
        "deepseek_ocr_api_key",
    }
)
FRONTEND_ASSET_DIR_NAME = "frontend-assets"
FRONTEND_ASSET_MAX_SIZE_BYTES = 5 * 1024 * 1024
FRONTEND_ASSET_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".webp", ".gif", ".svg"})
FRONTEND_ASSET_CONTENT_TYPES = frozenset(
    {
        "image/jpeg",
        "image/png",
        "image/webp",
        "image/gif",
        "image/svg+xml",
    }
)


def _dump_public_config() -> dict:
    payload = config.dump_config()
    for key in SENSITIVE_CONFIG_FIELDS:
        if key in payload:
            payload[f"{key}_configured"] = bool(payload.get(key))
            payload[key] = ""
    return payload


def _remove_empty_sensitive_config_values(items: dict) -> dict:
    sanitized = dict(items)
    for key in SENSITIVE_CONFIG_FIELDS:
        if key in sanitized and not str(sanitized.get(key) or "").strip():
            sanitized.pop(key)
    return sanitized


def _frontend_asset_root() -> Path:
    return (Path(config.save_dir) / FRONTEND_ASSET_DIR_NAME).resolve()


def _validate_frontend_asset_upload(file: UploadFile) -> str:
    filename = Path(file.filename or "").name
    suffix = Path(filename).suffix.lower()
    if suffix not in FRONTEND_ASSET_EXTENSIONS:
        raise HTTPException(status_code=400, detail="仅支持 jpg、png、webp、gif、svg 图片")
    if file.content_type not in FRONTEND_ASSET_CONTENT_TYPES:
        raise HTTPException(status_code=400, detail="上传文件必须是图片类型")
    return suffix


def _resolve_frontend_asset_path(filename: str) -> Path:
    asset_root = _frontend_asset_root()
    path = (asset_root / Path(filename).name).resolve()
    if path.parent != asset_root:
        raise HTTPException(status_code=400, detail="非法资源路径")
    return path

# =============================================================================
# === 健康检查分组 ===
# =============================================================================


@system.get("/health")
async def health_check():
    """系统健康检查接口（公开接口）"""
    return {"status": "ok", "message": "服务正常运行", "version": get_version()}


@system.get("/discovery")
async def discovery():
    """系统能力发现接口（公开接口）"""
    return {
        "name": "Yuxi",
        "version": get_version(),
        "api_prefix": "/api",
        "capabilities": {
            "cli": {
                "min_cli_version": "0.1.0",
                "browser_login": True,
                "api_key_auth": True,
                "remote_config": True,
                "kb_upload": True,
            }
        },
        "endpoints": {
            "health": "/api/system/health",
            "auth_me": "/api/auth/me",
            "cli_auth_sessions": "/api/auth/cli/sessions",
            "cli_auth_authorize": "/auth/cli/authorize",
        },
    }


# =============================================================================
# === 配置管理分组 ===
# =============================================================================


@system.get("/config")
async def get_config(current_user: User = Depends(get_required_user)):
    """获取系统配置"""
    return _dump_public_config()


@system.post("/config")
async def update_config_single(key=Body(...), value=Body(...), current_user: User = Depends(get_admin_user)) -> dict:
    """更新单个配置项"""
    if not isinstance(key, str) or key not in type(config).model_fields:
        raise HTTPException(status_code=400, detail=f"未知配置项: {key}")
    if not config.can_update(key):
        raise HTTPException(status_code=400, detail=f"配置项不可修改: {key}")
    if key in SENSITIVE_CONFIG_FIELDS and not str(value or "").strip():
        return _dump_public_config()
    try:
        config.set_value(key, value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        config.save()
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return _dump_public_config()


@system.post("/config/update")
async def update_config_batch(items: dict = Body(...), current_user: User = Depends(get_admin_user)) -> dict:
    """批量更新配置项"""
    try:
        config.update(_remove_empty_sensitive_config_values(items))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        config.save()
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return _dump_public_config()


@system.get("/frontend-chat-config")
async def get_frontend_chat_config(db: AsyncSession = Depends(get_db), current_user: User = Depends(get_required_user)):
    return {"success": True, "data": await load_frontend_chat_config(db)}


@system.put("/frontend-chat-config")
async def update_frontend_chat_config(
    payload: dict = Body(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_admin_user),
):
    agent_options = await list_chat_agents(db)
    config_data = normalize_frontend_chat_config(payload, agent_options)
    persisted_value = {key: value for key, value in config_data.items() if key != "agent_options"}

    kv = await get_frontend_chat_config_record(db)
    if kv is None:
        kv = SystemKV(
            key=FRONTEND_CHAT_CONFIG_KEY,
            value=persisted_value,
            description="前台问答门户配置",
        )
        db.add(kv)
    else:
        kv.value = persisted_value

    await db.commit()
    await db.refresh(kv)
    return {"success": True, "data": normalize_frontend_chat_config(kv.value, agent_options)}


@system.post("/frontend-assets")
async def upload_frontend_asset(
    file: UploadFile = File(...),
    current_user: User = Depends(get_admin_user),
):
    """上传前端品牌图片到持久化资源目录。"""
    suffix = _validate_frontend_asset_upload(file)
    asset_root = _frontend_asset_root()
    asset_root.mkdir(parents=True, exist_ok=True)

    storage_name = f"{uuid.uuid4().hex}{suffix}"
    target_path = _resolve_frontend_asset_path(storage_name)
    try:
        content = await read_upload_with_limit(
            file,
            max_size_bytes=FRONTEND_ASSET_MAX_SIZE_BYTES,
            too_large_message="图片大小不能超过 5MB",
        )
        async with aiofiles.open(target_path, "wb") as output_file:
            await output_file.write(content)
    except ValueError as exc:
        if target_path.exists():
            target_path.unlink()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except OSError as exc:
        logger.error("前端资源图片保存失败: %s", exc)
        if target_path.exists():
            target_path.unlink()
        raise HTTPException(status_code=500, detail="图片保存失败，请检查服务端存储目录") from exc

    return {"success": True, "data": {"url": f"/api/system/frontend-assets/{storage_name}"}}


@system.get("/frontend-assets/{filename}")
async def get_frontend_asset(filename: str):
    """读取后台上传的前端品牌图片。"""
    asset_path = _resolve_frontend_asset_path(filename)
    if not asset_path.exists() or not asset_path.is_file():
        raise HTTPException(status_code=404, detail="资源不存在")
    return FileResponse(asset_path)


@system.get("/global-agent-prompt")
async def get_global_agent_prompt(db: AsyncSession = Depends(get_db), current_user: User = Depends(get_required_user)):
    """获取全局智能体附加提示词配置。"""
    return {"success": True, "data": await load_global_agent_prompt_config(db)}


@system.put("/global-agent-prompt")
async def update_global_agent_prompt(
    payload: dict = Body(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_admin_user),
):
    """更新全局智能体附加提示词配置。"""
    try:
        config_data = normalize_global_agent_prompt_config(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    kv = await get_global_agent_prompt_record(db)
    if kv is None:
        kv = SystemKV(
            key="global_agent_prompt",
            value=config_data,
            description="全局智能体附加提示词配置",
        )
        db.add(kv)
    else:
        kv.value = config_data

    await db.commit()
    await db.refresh(kv)
    return {"success": True, "data": normalize_global_agent_prompt_config(kv.value)}


@system.get("/logs")
async def get_system_logs(levels: str | None = None, current_user: User = Depends(get_admin_user)):
    """获取系统日志

    Args:
        levels: 可选的日志级别过滤，多个级别用逗号分隔，如 "INFO,ERROR,DEBUG,WARNING"
    """
    try:
        from yuxi.utils.logging_config import LOG_FILE

        # 解析日志级别过滤条件
        level_filter = None
        if levels:
            level_filter = set(level.strip().upper() for level in levels.split(",") if level.strip())

        #  修复 GBK 编码报错：强制 utf-8 读取，忽略错误
        async with aiofiles.open(LOG_FILE, encoding="utf-8", errors="ignore") as f:
            # 读取最后1000行
            lines = []
            async for line in f:
                filtered_line = line.rstrip("\n\r")
                # 如果指定了日志级别过滤，则按级别过滤
                if level_filter:
                    # 日志格式: 2025-03-10 08:26:37,269 - INFO - module - message
                    # 提取日志级别
                    parts = filtered_line.split(" - ")
                    if len(parts) >= 2 and parts[1].strip() in level_filter:
                        lines.append(filtered_line + "\n")
                    # 继续读取以保持行数统计准确
                    if len(lines) > 1000:
                        lines.pop(0)
                else:
                    lines.append(filtered_line + "\n")
                    if len(lines) > 1000:
                        lines.pop(0)

        log = "".join(lines)
        return {"log": log, "message": "success", "log_file": LOG_FILE}
    except Exception as e:
        logger.error(f"获取系统日志失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取系统日志失败: {str(e)}")


# =============================================================================
# === 信息管理分组 ===
# =============================================================================


async def load_info_config():
    """加载信息配置文件"""
    try:
        # 配置文件路径
        brand_file_path = os.environ.get("YUXI_BRAND_FILE_PATH", "package/yuxi/config/static/info.local.yaml")
        config_path = Path(brand_file_path)

        # 检查文件是否存在
        if not config_path.exists():
            logger.debug(f"The config file {config_path} does not exist, using default config")
            config_path = Path("package/yuxi/config/static/info.template.yaml")

        # 异步读取配置文件
        async with aiofiles.open(config_path, encoding="utf-8") as file:
            content = await file.read()

        # 注入版本号占位符
        content = content.replace("{{YUXI_VERSION}}", get_version())

        config = yaml.safe_load(content)

        return config

    except Exception as e:
        logger.error(f"Failed to load info config: {e}")
        return {}


@system.get("/info")
async def get_info_config(db: AsyncSession = Depends(get_db)):
    """获取系统信息配置（公开接口，无需认证）"""
    try:
        info_config = await load_info_config()
        frontend_config = await load_frontend_chat_config(db)

        organization = {
            **(info_config.get("organization") or {}),
            "name": frontend_config["organization_name"],
            "logo": frontend_config["organization_logo"],
            "avatar": frontend_config["organization_avatar"],
            "login_bg": frontend_config["login_bg"],
        }
        branding = {
            **(info_config.get("branding") or {}),
            "name": frontend_config["system_name"],
            "title": frontend_config["browser_title"],
        }
        merged_config = {
            **info_config,
            "organization": organization,
            "branding": branding,
            "frontend": frontend_config,
        }
        return {"success": True, "data": merged_config}
    except Exception as e:
        logger.error(f"获取信息配置失败: {e}")
        raise HTTPException(status_code=500, detail="获取信息配置失败")


@system.post("/info/reload")
async def reload_info_config(current_user: User = Depends(get_admin_user)):
    """重新加载信息配置"""
    try:
        config = await load_info_config()
        return {"success": True, "message": "配置重新加载成功", "data": config}
    except Exception as e:
        logger.error(f"重新加载信息配置失败: {e}")
        raise HTTPException(status_code=500, detail="重新加载信息配置失败")


# =============================================================================
# === OCR服务分组 ===
# =============================================================================


@system.get("/ocr/health")
async def check_ocr_services_health(current_user: User = Depends(get_admin_user)):
    """
    检查所有OCR服务的健康状态
    返回各个OCR服务的可用性信息
    """
    from yuxi.knowledge.parser.factory import DocumentProcessorFactory

    try:
        # 使用统一的健康检查接口
        health_status = await DocumentProcessorFactory.check_all_health_async()

        # 格式化健康检查响应
        formatted_status = {}
        for service_name, health_info in health_status.items():
            formatted_status[service_name] = {
                "status": health_info.get("status", "unknown"),
                "message": health_info.get("message", ""),
                "details": health_info.get("details", {}),
            }

        # 计算整体健康状态
        overall_status = (
            "healthy" if any(svc["status"] == "healthy" for svc in formatted_status.values()) else "unhealthy"
        )

        return {
            "overall_status": overall_status,
            "services": formatted_status,
            "message": "OCR服务健康检查完成",
        }

    except Exception as e:
        logger.error(f"OCR健康检查失败: {str(e)}")
        return {
            "overall_status": "error",
            "services": {},
            "message": f"OCR健康检查失败: {str(e)}",
        }
