#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
智能体全局提示词配置。

提供后台配置的全局附加提示词，替代用户文件区 AGENTS.md 注入能力。
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.storage.postgres.models_business import SystemKV

# ===============================
# 配置
# ===============================

GLOBAL_AGENT_PROMPT_CONFIG_KEY = "global_agent_prompt"
GLOBAL_AGENT_PROMPT_DEFAULT = {
    "enabled": False,
    "prompt": "",
}


# ===============================
# 核心逻辑
# ===============================

def normalize_global_agent_prompt_config(config_value: dict) -> dict:
    """
    校验并标准化全局智能体提示词配置。

    Args:
        config_value: 待校验的配置对象。

    Returns:
        dict: 标准化后的配置。
    """
    if not isinstance(config_value, dict):
        raise ValueError("全局智能体提示词配置必须是对象")

    config_data = {**GLOBAL_AGENT_PROMPT_DEFAULT, **config_value}
    if not isinstance(config_data.get("enabled"), bool):
        raise ValueError("全局智能体提示词启用状态必须是布尔值")
    if not isinstance(config_data.get("prompt"), str):
        raise ValueError("全局智能体提示词内容必须是字符串")

    return {
        "enabled": config_data["enabled"],
        "prompt": config_data["prompt"].strip(),
    }


async def get_global_agent_prompt_record(db: AsyncSession) -> SystemKV | None:
    """
    获取全局智能体提示词配置记录。

    Args:
        db: 数据库会话。

    Returns:
        SystemKV | None: 配置记录，不存在时返回 None。
    """
    result = await db.execute(select(SystemKV).filter(SystemKV.key == GLOBAL_AGENT_PROMPT_CONFIG_KEY))
    return result.scalar_one_or_none()


async def get_global_agent_prompt_config(db: AsyncSession) -> dict:
    """
    读取并标准化全局智能体提示词配置。

    Args:
        db: 数据库会话。

    Returns:
        dict: 标准化后的配置。
    """
    kv = await get_global_agent_prompt_record(db)
    value = kv.value if kv else GLOBAL_AGENT_PROMPT_DEFAULT
    return normalize_global_agent_prompt_config(value)


async def get_enabled_global_agent_prompt(db: AsyncSession) -> str:
    """
    获取当前启用的全局智能体提示词。

    Args:
        db: 数据库会话。

    Returns:
        str: 启用且非空时返回提示词，否则返回空字符串。
    """
    config_data = await get_global_agent_prompt_config(db)
    if not config_data["enabled"]:
        return ""
    return config_data["prompt"]
