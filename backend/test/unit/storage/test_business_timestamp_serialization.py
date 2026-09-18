"""业务表的时间戳序列化契约：naive UTC 列必须原样、带 `Z` 输出。

`models_business` 的列全是 `Column(DateTime, default=utc_now_naive)`，库里存的是
**naive UTC**。序列化走错函数就会错 8 小时：

- `format_utc_datetime` 内部 `ensure_utc()` 把 naive **当 Asia/Shanghai** → 瞬时被减 8 小时；
- 裸 `.isoformat()` 输出没有时区标记 → 前端 dayjs 按本地渲染 → 同样偏早 8 小时。

2026-09-17 统一改用 `format_naive_utc_datetime`（naive 视为 UTC，只补标记）。
这组用例遍历所有带 `to_dict()` 的业务模型，把"输出必须带 Z、且瞬时一分不动"钉死——
以后新增模型如果又写成裸 `.isoformat()`，这里会红。
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

import pytest
from sqlalchemy import DateTime

from yuxi.storage.postgres.models_business import (
    Agent,
    AgentEnv,
    AgentRun,
    APIKey,
    Conversation,
    ConversationStats,
    Department,
    MCPServer,
    Message,
    MessageFeedback,
    ModelProvider,
    Skill,
    SystemKV,
    TaskRecord,
    ToolCall,
    User,
    UserConfig,
)

#: 库里会存的瞬时（naive UTC）
SENTINEL = datetime(2026, 9, 17, 12, 0, 0)
#: 被当成 Asia/Shanghai 解释后的结果——出现它就说明又踩了那个函数
SHIFTED_VALUE = "2026-09-17T04:00:00"
EXPECTED_PREFIX = "2026-09-17T12:00:00"

ISO_LIKE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")

MODELS = [
    Department,
    User,
    AgentEnv,
    UserConfig,
    Conversation,
    Message,
    MessageFeedback,
    Skill,
    Agent,
    APIKey,
    TaskRecord,
    AgentRun,
    MCPServer,
    ModelProvider,
    SystemKV,
    ConversationStats,
    ToolCall,
]


def _fill_naive_datetimes(instance) -> None:
    """把所有"无时区 DateTime"列填成同一个已知瞬时（只填不读，不落库）。"""
    for column in instance.__table__.columns:
        if isinstance(column.type, DateTime) and not getattr(column.type, "timezone", False):
            setattr(instance, column.key, SENTINEL)


def _iter_iso_strings(node):
    if isinstance(node, str):
        if ISO_LIKE.match(node):
            yield node
    elif isinstance(node, dict):
        for value in node.values():
            yield from _iter_iso_strings(value)
    elif isinstance(node, (list, tuple)):
        for value in node:
            yield from _iter_iso_strings(value)


@pytest.mark.parametrize("model", MODELS, ids=lambda m: m.__name__)
def test_naive_datetime_columns_serialize_to_z_without_shifting(model):
    instance = model()
    _fill_naive_datetimes(instance)

    payload = instance.to_dict()
    stamps = list(_iter_iso_strings(payload))

    assert stamps, f"{model.__name__}.to_dict() 没有输出任何时间字段，用例失去意义"
    for stamp in stamps:
        assert stamp.endswith("Z"), f"{model.__name__}：{stamp} 没有时区标记（前端会按本地渲染）"
        assert SHIFTED_VALUE not in stamp, f"{model.__name__}：{stamp} 被当成了上海时间，偏早 8 小时"
        assert stamp.startswith(EXPECTED_PREFIX), f"{model.__name__}：{stamp} 的瞬时被改动了"


def test_aware_values_are_unaffected_by_the_switch():
    """列若是 timestamptz，asyncpg 返回 aware，此时新旧函数的输出必须完全一致。

    这条是"敢在没有 PG 的机器上做全量替换"的依据：aware 路径逐字未变，
    所以替换只可能修好 naive 情形，不可能弄坏 aware 情形。
    """
    aware = datetime(2026, 9, 17, 12, 0, 0, tzinfo=UTC)
    conversation = Conversation(created_at=aware, updated_at=aware)

    payload = conversation.to_dict()

    assert payload["created_at"] == "2026-09-17T12:00:00Z"
    assert payload["updated_at"] == "2026-09-17T12:00:00Z"


def test_none_datetimes_still_serialize_to_none():
    """`format_naive_utc_datetime` 对 None 的返回是 None，不能变成 "None" 字符串。"""
    conversation = Conversation(created_at=None, updated_at=None)

    payload = conversation.to_dict()

    assert payload["created_at"] is None
    assert payload["updated_at"] is None
