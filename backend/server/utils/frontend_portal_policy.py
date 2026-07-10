#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
前台问答门户的运营策略。

集中管理前台普通用户的可用智能体、输入能力和配置校验，避免业务权限散落在路由中。
"""

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.storage.postgres.models_business import Agent, SystemKV, User

# ===============================
# 配置
# ===============================

FRONTEND_BUSINESS_ROLES = {"student", "faculty"}
FRONTEND_CHAT_CONFIG_KEY = "frontend_chat_config"
FRONTEND_CHAT_CONFIG_DEFAULT = {
    "system_name": "智能AI对话系统",
    "welcome_title_mode": "dynamic",
    "welcome_title": "智能AI对话系统",
    "frontend_input_placeholder": "基于知识库的 RAG 问答，快速准确地回答问题",
    "default_agent_id": "",
    "selectable_agent_ids": [],
    "allow_user_select_agents": False,
    "show_agent_selector": True,
    "show_model_selector": False,
    "show_web_search_toggle": False,
    "show_file_upload": False,
    "show_image_upload": False,
    "show_voice_input": False,
    "show_send_button": True,
    "show_thought_process": True,
    "show_reference_documents": True,
}

BOOLEAN_CONFIG_KEYS = {
    "allow_user_select_agents",
    "show_agent_selector",
    "show_model_selector",
    "show_web_search_toggle",
    "show_file_upload",
    "show_image_upload",
    "show_voice_input",
    "show_send_button",
    "show_thought_process",
    "show_reference_documents",
}
STRING_CONFIG_KEYS = {
    "system_name",
    "welcome_title_mode",
    "welcome_title",
    "frontend_input_placeholder",
    "default_agent_id",
}


# ===============================
# 核心逻辑
# ===============================

def is_frontend_chat_user(user: User) -> bool:
    """
    判断用户是否是前台问答门户用户。

    Args:
        user: 当前登录用户。

    Returns:
        bool: 普通前台用户返回 True，后台管理员或工作区用户返回 False。
    """
    return user.role == "user" and user.business_role in FRONTEND_BUSINESS_ROLES


async def list_chat_agents(db: AsyncSession) -> list[dict]:
    """
    列出可作为前台候选的主智能体。

    Args:
        db: 数据库会话。

    Returns:
        list[dict]: 智能体选项列表。
    """
    result = await db.execute(
        select(Agent)
        .filter(Agent.is_subagent.is_(False))
        .order_by(Agent.is_default.desc(), Agent.id.asc())
    )
    return [
        {
            "id": agent.slug,
            "name": agent.name,
            "description": agent.description,
            "icon": agent.icon,
            "is_default": bool(agent.is_default),
        }
        for agent in result.scalars().all()
    ]


async def get_frontend_chat_config_record(db: AsyncSession) -> SystemKV | None:
    """
    获取前台问答门户配置记录。

    Args:
        db: 数据库会话。

    Returns:
        SystemKV | None: 配置记录，不存在时返回 None。
    """
    result = await db.execute(select(SystemKV).filter(SystemKV.key == FRONTEND_CHAT_CONFIG_KEY))
    return result.scalar_one_or_none()


def normalize_frontend_chat_config(config_value: dict, agent_options: list[dict]) -> dict:
    """
    校验并标准化前台问答门户配置。

    Args:
        config_value: 待校验的配置对象。
        agent_options: 当前系统可用的主智能体选项。

    Returns:
        dict: 标准化后的配置。
    """
    if not isinstance(config_value, dict):
        raise HTTPException(status_code=400, detail="前台配置必须是对象")

    config_data = {**FRONTEND_CHAT_CONFIG_DEFAULT, **config_value}
    for key in STRING_CONFIG_KEYS:
        if not isinstance(config_data.get(key), str):
            raise HTTPException(status_code=400, detail=f"前台配置 {key} 必须是字符串")
    for key in BOOLEAN_CONFIG_KEYS:
        if not isinstance(config_data.get(key), bool):
            raise HTTPException(status_code=400, detail=f"前台配置 {key} 必须是布尔值")

    if config_data["welcome_title_mode"] not in {"dynamic", "custom"}:
        raise HTTPException(status_code=400, detail="输入框标题模式必须是 dynamic 或 custom")

    known_agent_ids = {agent["id"] for agent in agent_options}
    selectable_agent_ids = config_data.get("selectable_agent_ids") or []
    if not isinstance(selectable_agent_ids, list):
        raise HTTPException(status_code=400, detail="候选智能体必须是数组")

    normalized_selectable_ids = []
    for agent_id in selectable_agent_ids:
        if not isinstance(agent_id, str) or not agent_id:
            raise HTTPException(status_code=400, detail="候选智能体 ID 必须是非空字符串")
        if agent_id not in known_agent_ids:
            raise HTTPException(status_code=400, detail=f"候选智能体不存在: {agent_id}")
        if agent_id not in normalized_selectable_ids:
            normalized_selectable_ids.append(agent_id)

    default_agent_id = config_data.get("default_agent_id") or ""
    if default_agent_id and default_agent_id not in normalized_selectable_ids:
        raise HTTPException(status_code=400, detail="默认智能体必须在候选智能体中")

    if not config_data["show_agent_selector"] and len(normalized_selectable_ids) > 1 and not default_agent_id:
        raise HTTPException(status_code=400, detail="隐藏智能体选择时必须设置默认智能体")

    config_data["selectable_agent_ids"] = normalized_selectable_ids
    config_data["default_agent_id"] = default_agent_id
    config_data["allow_user_select_agents"] = bool(config_data["show_agent_selector"] and len(normalized_selectable_ids) > 1)
    config_data["agent_options"] = agent_options
    return config_data


async def get_frontend_chat_config(db: AsyncSession) -> dict:
    """
    读取并标准化前台问答门户配置。

    Args:
        db: 数据库会话。

    Returns:
        dict: 标准化后的前台配置。
    """
    agent_options = await list_chat_agents(db)
    kv = await get_frontend_chat_config_record(db)
    value = kv.value if kv else FRONTEND_CHAT_CONFIG_DEFAULT
    return normalize_frontend_chat_config(value, agent_options)


def get_frontend_allowed_agent_ids(config_data: dict) -> set[str]:
    """
    根据前台配置计算普通用户实际可调用的智能体集合。

    Args:
        config_data: 标准化后的前台配置。

    Returns:
        set[str]: 允许调用的智能体 slug 集合。
    """
    selectable_ids = list(config_data.get("selectable_agent_ids") or [])
    if config_data.get("show_agent_selector"):
        return set(selectable_ids)

    default_agent_id = config_data.get("default_agent_id") or ""
    if default_agent_id:
        return {default_agent_id}
    if len(selectable_ids) == 1:
        return {selectable_ids[0]}
    if len(selectable_ids) > 1:
        raise HTTPException(status_code=409, detail="前台配置缺少默认智能体")
    return set()


async def ensure_frontend_agent_allowed(db: AsyncSession, user: User, agent_slug: str) -> None:
    """
    校验前台普通用户是否可以调用指定智能体。

    Args:
        db: 数据库会话。
        user: 当前用户。
        agent_slug: 智能体 slug。
    """
    if not is_frontend_chat_user(user):
        return

    config_data = await get_frontend_chat_config(db)
    if agent_slug not in get_frontend_allowed_agent_ids(config_data):
        raise HTTPException(status_code=403, detail="该智能体未开放给前台用户")


async def ensure_frontend_run_allowed(
    db: AsyncSession,
    user: User,
    *,
    agent_slug: str,
    model_spec: str | None = None,
    has_image_content: bool = False,
) -> None:
    """
    校验前台普通用户发起智能体运行时的业务约束。

    Args:
        db: 数据库会话。
        user: 当前用户。
        agent_slug: 智能体 slug。
        model_spec: 对话级模型覆盖。
        has_image_content: 是否携带图片内容。
    """
    if not is_frontend_chat_user(user):
        return

    config_data = await get_frontend_chat_config(db)
    if agent_slug not in get_frontend_allowed_agent_ids(config_data):
        raise HTTPException(status_code=403, detail="该智能体未开放给前台用户")
    if model_spec and not config_data.get("show_model_selector"):
        raise HTTPException(status_code=403, detail="前台未开放模型选择")
    if has_image_content and not config_data.get("show_image_upload"):
        raise HTTPException(status_code=403, detail="前台未开放图片上传")


async def ensure_frontend_file_upload_allowed(db: AsyncSession, user: User) -> None:
    """
    校验前台普通用户是否可以上传文件附件。

    Args:
        db: 数据库会话。
        user: 当前用户。
    """
    if not is_frontend_chat_user(user):
        return

    config_data = await get_frontend_chat_config(db)
    if not config_data.get("show_file_upload"):
        raise HTTPException(status_code=403, detail="前台未开放文件上传")


async def ensure_frontend_image_upload_allowed(db: AsyncSession, user: User) -> None:
    """
    校验前台普通用户是否可以上传图片。

    Args:
        db: 数据库会话。
        user: 当前用户。
    """
    if not is_frontend_chat_user(user):
        return

    config_data = await get_frontend_chat_config(db)
    if not config_data.get("show_image_upload"):
        raise HTTPException(status_code=403, detail="前台未开放图片上传")
