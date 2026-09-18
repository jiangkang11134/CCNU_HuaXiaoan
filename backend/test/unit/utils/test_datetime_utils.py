"""两个 UTC 序列化助手的语义边界。

同一份 naive 值在本项目里有两种含义：

- **DB 列**（`utc_now_naive()` / `Column(DateTime, default=utc_now_naive)`）= naive **UTC**
- **用户输入**（表单/API 传来的无时区串）= **Asia/Shanghai**

所以序列化助手也必须是两个，而它们的**唯一**差别应当只落在 naive 输入上——
对 aware 输入必须完全一致，否则"换用哪个助手"就会变成一次静默的行为变更。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from yuxi.utils.datetime_utils import (
    SHANGHAI_TZ,
    format_naive_utc_datetime,
    format_utc_datetime,
    utc_now_naive,
)

pytestmark = [pytest.mark.unit]

NAIVE = datetime(2026, 9, 17, 3, 0, 0)
AS_UTC = "2026-09-17T03:00:00Z"
AS_SHANGHAI = "2026-09-16T19:00:00Z"


# ---------------------------------------------------------------------------
# format_naive_utc_datetime：DB 列（naive UTC）的正解
# ---------------------------------------------------------------------------

def test_naive_helper_marks_without_shifting():
    """naive 值只补标记，不做任何换算。"""
    assert format_naive_utc_datetime(NAIVE) == AS_UTC


def test_naive_helper_leaves_aware_utc_untouched():
    assert format_naive_utc_datetime(NAIVE.replace(tzinfo=timezone.utc)) == AS_UTC


def test_naive_helper_normalizes_non_utc_aware_to_utc():
    """上海本地时间 11:00 == UTC 03:00，两者必须序列化成同一个串。"""
    assert format_naive_utc_datetime(datetime(2026, 9, 17, 11, 0, 0, tzinfo=SHANGHAI_TZ)) == AS_UTC


def test_naive_helper_returns_none_for_none():
    assert format_naive_utc_datetime(None) is None


def test_naive_helper_round_trips_utc_now_naive():
    """热路径写法：写库用 utc_now_naive()，同一个值直接序列化回去不能偏。"""
    now = utc_now_naive()

    parsed = datetime.fromisoformat(format_naive_utc_datetime(now))

    assert abs(parsed.replace(tzinfo=None) - now) < timedelta(seconds=1)


# ---------------------------------------------------------------------------
# 两者的分工：只在 naive 输入上不同
# ---------------------------------------------------------------------------

def test_helpers_agree_on_every_aware_input():
    """aware 输入下两者必须逐字一致。

    这是"把调用点从 format_utc_datetime 换成 format_naive_utc_datetime 不会改变
    已 aware 的列"的保证——也就是换助手的安全性前提。
    """
    aware_values = [
        NAIVE.replace(tzinfo=timezone.utc),
        datetime(2026, 9, 17, 11, 0, 0, tzinfo=SHANGHAI_TZ),
        datetime(2026, 9, 16, 21, 0, 0, tzinfo=timezone(timedelta(hours=-6))),
    ]

    for aware in aware_values:
        assert format_naive_utc_datetime(aware) == format_utc_datetime(aware), aware


def test_helpers_differ_only_on_naive_input_and_by_exactly_the_offset():
    """naive 输入下差 8 小时，方向固定：naive 助手（按 UTC 解释）更晚。"""
    marked = datetime.fromisoformat(format_naive_utc_datetime(NAIVE))
    shifted = datetime.fromisoformat(format_utc_datetime(NAIVE))

    assert marked - shifted == timedelta(hours=8)


def test_format_utc_datetime_still_treats_naive_as_shanghai():
    """钉住既有行为：本函数服务于**用户输入**，naive 按本地时间解释。

    `ensure_utc`/`ensure_shanghai` 的 "naive = Asia/Shanghai" 语义本身是对的，
    修 DB 序列化时不要顺手把它改掉（`user_router` 的 API Key 过期时间依赖它）。
    """
    assert format_utc_datetime(NAIVE) == AS_SHANGHAI
