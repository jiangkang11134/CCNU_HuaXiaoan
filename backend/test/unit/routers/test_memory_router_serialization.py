"""memory_router 的时间字段契约：所有时间字段都带 UTC 标记。

**返回侧**（`_iso_utc`）：所有时间字段都要带 UTC 标记（形如 "...Z"）。
这些列由 `utc_now_naive()` 写入，是无时区的 UTC；前端 `utils/time.js:formatDateTime`
用 dayjs 按本地时区渲染，不带标记就会偏早 8 小时。

为什么不用 `utils.datetime_utils.format_utc_datetime`：它内部走 `ensure_utc()`，
而 `ensure_utc` 的约定是"naive 视为 Asia/Shanghai"，对 DB 里的 naive UTC 会**再减 8 小时**
（实测 `format_utc_datetime(datetime(2026,9,17,3,0,0)) == "2026-09-16T19:00:00Z"`），
与它自己的 docstring 矛盾。那是全站级问题，本次只保证 memory 模块正确。

**写入侧**（`expires_at` 怎么落库）已随纠错工单接口迁到
``test/unit/routers/test_self_evolution_router_serialization.py``。
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from server.routers.memory_router import (
    _iso_utc,
    _memory_item,
    list_memory,
    list_memory_events,
)
from yuxi.utils.datetime_utils import SHANGHAI_TZ, format_utc_datetime, utc_now_naive

pytestmark = [pytest.mark.unit]

NAIVE_UTC = datetime(2026, 9, 16, 7, 30, 0)


class _Row:
    """只暴露 _memory_item 读到的字段的最小 row 替身。"""

    def __init__(self, **overrides):
        self.id = 7
        self.uid = "u1"
        self.fact_key = "user.response_style"
        self.content = "偏好简短回答"
        self.status = "candidate"
        self.confidence = 0.6
        self.scope = "user"
        # 故意留 None，验证序列化层会兜成 []
        self.tags = None
        self.graph_query_role = "personalize"
        self.entity_hints = None
        self.intent_hints = None
        self.domain_scope = "general"
        self.expires_at = None
        self.confirmed_at = None
        self.confirmed_by = None
        self.created_at = NAIVE_UTC
        self.updated_at = NAIVE_UTC
        for key, value in overrides.items():
            setattr(self, key, value)


class _Scalars:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return list(self._rows)


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return _Scalars(self._rows)


class _ListDB:
    """list_memories / list_events 都只做一次 select().scalars().all()。"""

    def __init__(self, rows):
        self.rows = rows

    async def execute(self, _stmt):
        return _Result(self.rows)


def _as_utc(value: str) -> datetime:
    return datetime.fromisoformat(value)


# ---------------------------------------------------------------------------
# 返回侧：所有时间字段都要带 UTC 标记
# ---------------------------------------------------------------------------

def test_memory_item_marks_every_timestamp_as_utc():
    item = _memory_item(_Row(
        expires_at=datetime(2026, 9, 20, 0, 0, 0),
        confirmed_at=datetime(2026, 9, 16, 8, 0, 0),
    ))

    for field in ("created_at", "updated_at", "expires_at", "confirmed_at"):
        assert item[field].endswith("Z"), f"{field}={item[field]!r} 缺少 UTC 标记"
        assert _as_utc(item[field]).utcoffset() == timedelta(0)

    # 标记的是"这本来就是 UTC"，不是把墙上时间重新解释一遍
    assert item["created_at"] == "2026-09-16T07:30:00Z"
    assert item["expires_at"] == "2026-09-20T00:00:00Z"


def test_memory_item_accepts_missing_timestamps():
    item = _memory_item(_Row(created_at=None, updated_at=None, expires_at=None, confirmed_at=None))

    assert item["created_at"] is None
    assert item["updated_at"] is None
    assert item["expires_at"] is None
    assert item["confirmed_at"] is None


def test_memory_item_normalizes_list_fields_to_empty_list():
    item = _memory_item(_Row())

    assert item["tags"] == []
    assert item["entity_hints"] == []
    assert item["intent_hints"] == []


# ---------------------------------------------------------------------------
# 各接口端到端：时间字段一律可直接交给 dayjs 渲染
# ---------------------------------------------------------------------------

async def test_list_memory_returns_timestamps_the_frontend_can_render():
    db = _ListDB([_Row(), _Row(id=8, fact_key="project.experiment", status="confirmed")])

    payload = await list_memory(limit=50, db=db, user=SimpleNamespace(uid="u1"))

    assert [row["id"] for row in payload["items"]] == [7, 8]
    for row in payload["items"]:
        assert _as_utc(row["created_at"]).utcoffset() == timedelta(0)
        assert _as_utc(row["updated_at"]).utcoffset() == timedelta(0)


async def test_list_memory_events_returns_renderable_timestamps():
    """「我的记忆」的变更记录弹窗直接展示 event.created_at，落库是 naive UTC。"""
    event = SimpleNamespace(
        event_id="evt-1", target_type="user_memory", target_id=7,
        event_type="confirmed", before_status="candidate", after_status="confirmed",
        payload={}, actor_type="user", actor_id="u1", request_id=None,
        created_at=NAIVE_UTC,
    )

    payload = await list_memory_events(limit=50, db=_ListDB([event]), user=SimpleNamespace(uid="u1"))

    assert payload["items"][0]["created_at"] == "2026-09-16T07:30:00Z"


async def test_list_memory_events_handles_missing_timestamp():
    event = SimpleNamespace(
        event_id="evt-2", target_type="user_memory", target_id=7,
        event_type="retracted", before_status="confirmed", after_status="retracted",
        payload={}, actor_type="user", actor_id="u1", request_id=None,
        created_at=None,
    )

    payload = await list_memory_events(limit=50, db=_ListDB([event]), user=SimpleNamespace(uid="u1"))

    assert payload["items"][0]["created_at"] is None


# ---------------------------------------------------------------------------
# 顺带记录：shared helper 的 docstring 与实现不一致（全站级，本次未改）
# ---------------------------------------------------------------------------

@pytest.mark.xfail(
    strict=True,
    reason=(
        "format_utc_datetime 把 naive 当 Asia/Shanghai（经 ensure_utc），对 DB 里的 "
        "naive UTC 少 8 小时。它自己的 docstring 已在 2026-09-17 更正为如实描述，"
        "但**行为**没动——因为是否该改取决于真实列类型（见下），且另有约 60 个调用点。"
        "根因是 schema 声明不一致：ORM 侧 models_business.py 用 `Column(DateTime)`（naive），"
        "而 manager.py 的 ensure_knowledge_schema/ensure_business_schema 对同名表写 "
        "`TIMESTAMPTZ`；create_all 先跑，所以库里大概率是 naive 列。"
        "谁定了要统一，就把它改成按 docstring 行为并更新本用例（XPASS 会被 strict 拦下）。"
    ),
)
def test_format_utc_datetime_treats_naive_as_utc_per_its_docstring():
    assert format_utc_datetime(datetime(2026, 9, 17, 3, 0, 0)) == "2026-09-17T03:00:00Z"


def test_iso_utc_does_not_shift_the_instant_it_serializes():
    """对照用例：本模块的 _iso_utc 只贴标记、不偏移，与上面那个 helper 的行为刻意不同。"""
    assert _iso_utc(datetime(2026, 9, 17, 3, 0, 0)) == "2026-09-17T03:00:00Z"
