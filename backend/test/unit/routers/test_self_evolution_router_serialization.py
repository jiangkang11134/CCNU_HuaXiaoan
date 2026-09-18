"""self_evolution_router 的时间字段契约：写入侧落 naive UTC。

从 test_memory_router_serialization.py 拆出（2026-09-17 路由分离：
纠错工单接口已从 ``/api/memory/corrections*`` 迁到 ``/api/self-evolution/corrections*``）。

管理员给的 ``expires_at`` 必须转成 naive UTC 落库，因为读取侧
（``self_evolution.service._now()``、``build_correction_context_details`` 的
``CorrectionTicket.expires_at > _now()``）全部按 naive UTC 比较。
容器 TZ 是 Asia/Shanghai，一旦用无参 ``astimezone()`` 落**本地**墙钟，
权威修正就会比预期晚 8 小时失效。
"""
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException

from server.routers.self_evolution_router import _parse_expires_at
from yuxi.utils.datetime_utils import SHANGHAI_TZ, utc_now_naive

pytestmark = [pytest.mark.unit]


# ---------------------------------------------------------------------------
# 写入侧：_parse_expires_at（本轮修的 bug）
# ---------------------------------------------------------------------------

def test_parse_expires_at_converts_shanghai_input_to_naive_utc():
    """管理员写"9-20 00:00 CST"，落库必须是 9-19 16:00 UTC。

    这是本次修复的核心断言：旧实现用无参 astimezone()（= 转本地）会落成 00:00，
    与读取侧的 naive UTC 差 8 小时，权威修正因此晚 8 小时失效。
    """
    assert _parse_expires_at("2026-09-20T00:00:00+08:00") == datetime(2026, 9, 19, 16, 0, 0)


def test_parse_expires_at_keeps_utc_input_as_is():
    assert _parse_expires_at("2026-09-20T00:00:00Z") == datetime(2026, 9, 20, 0, 0, 0)


def test_parse_expires_at_treats_naive_input_as_shanghai():
    """无时区输入按 Asia/Shanghai 解释——与 user_router 的 API Key 过期时间同一套约定，
    结果不再依赖容器的 TZ 设置。"""
    assert _parse_expires_at("2026-09-20T00:00:00") == datetime(2026, 9, 19, 16, 0, 0)


def test_parse_expires_at_is_offset_agnostic():
    """同一个瞬间，无论用什么时区写成字符串，落库结果必须一致。"""
    same_instant = [
        _parse_expires_at("2026-09-20T00:00:00Z"),
        _parse_expires_at("2026-09-20T08:00:00+08:00"),
        _parse_expires_at("2026-09-19T16:00:00-08:00"),
    ]

    assert same_instant[0] == same_instant[1] == same_instant[2]


def test_parse_expires_at_matches_the_reader_convention_end_to_end():
    """写入侧与读取侧必须能直接比较：读取侧拿 utc_now_naive() 比。

    给一个"恰好 1 小时后"的有效期（按上海时间字符串表达），落库值减去 now 应该≈1 小时。
    旧实现会落成上海墙钟，差值≈9 小时——本用例就是为了钉死这个 8 小时偏差。
    """
    now_utc = utc_now_naive()
    one_hour_later = (now_utc + timedelta(hours=1)).replace(tzinfo=UTC).astimezone(SHANGHAI_TZ)

    stored = _parse_expires_at(one_hour_later.isoformat())

    assert timedelta(minutes=59) < stored - now_utc < timedelta(minutes=61)


def test_parse_expires_at_blank_means_never_expires():
    assert _parse_expires_at(None) is None
    assert _parse_expires_at("") is None
    assert _parse_expires_at("   ") is None


def test_parse_expires_at_rejects_garbage_with_400():
    with pytest.raises(HTTPException) as excinfo:
        _parse_expires_at("明天下班前")

    assert excinfo.value.status_code == 400
