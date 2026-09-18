"""
Datetime helper utilities for consistent timezone handling.

The backend stores timestamps in UTC and exposes ISO 8601 strings with an
explicit timezone designator. For user-facing displays we typically convert to
Asia/Shanghai.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable
from zoneinfo import ZoneInfo

UTC = dt.UTC
SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
_ISO_Z_SUFFIX = "+00:00"


def utc_now() -> dt.datetime:
    """Return the current UTC time as an aware datetime."""
    return dt.datetime.now(UTC)


def utc_now_naive() -> dt.datetime:
    """Return the current UTC time as a naive datetime (for DB fields without timezone)."""
    return dt.datetime.now(UTC).replace(tzinfo=None)


def shanghai_now() -> dt.datetime:
    """Return the current Asia/Shanghai time as an aware datetime."""
    return utc_now().astimezone(SHANGHAI_TZ)


def ensure_utc(value: dt.datetime) -> dt.datetime:
    """
    Convert a datetime to UTC.

    Naive values are assumed to be in Asia/Shanghai.
    """
    if value.tzinfo is None:
        value = value.replace(tzinfo=SHANGHAI_TZ)
    return value.astimezone(UTC)


def ensure_shanghai(value: dt.datetime) -> dt.datetime:
    """
    Convert a datetime to Asia/Shanghai.

    Naive values are assumed to be in Asia/Shanghai.
    """
    if value.tzinfo is None:
        value = value.replace(tzinfo=SHANGHAI_TZ)
    return value.astimezone(SHANGHAI_TZ)


def utc_isoformat(value: dt.datetime | None = None) -> str:
    """Return an ISO 8601 string in UTC with a trailing Z suffix."""
    value = ensure_utc(value or utc_now())
    iso_string = value.isoformat()
    if iso_string.endswith(_ISO_Z_SUFFIX):
        return iso_string.replace(_ISO_Z_SUFFIX, "Z")
    return iso_string


def shanghai_isoformat(value: dt.datetime | None = None) -> str:
    """Return an ISO 8601 string in Asia/Shanghai timezone."""
    value = ensure_shanghai(value or shanghai_now())
    return value.isoformat()


def coerce_datetime(value: dt.datetime | None) -> dt.datetime | None:
    """Normalize persisted datetimes to UTC, handling nulls gracefully."""
    if value is None:
        return None
    return ensure_utc(value)


def coerce_any_to_utc_datetime(value: dt.datetime | int | float | str | None) -> dt.datetime | None:
    """
    Convert heterogeneous timestamp representations to an aware UTC datetime.

    Supports:
      * aware or naive datetime objects
      * unix timestamps (seconds) as int/float
      * ISO 8601 strings
    """
    if value is None:
        return None

    if isinstance(value, dt.datetime):
        return ensure_utc(value)

    if isinstance(value, (int, float)):
        return dt.datetime.fromtimestamp(value, tz=UTC)

    if isinstance(value, str):
        # Attempt to parse ISO 8601 strings.
        try:
            parsed = dt.datetime.fromisoformat(value.replace("Z", _ISO_Z_SUFFIX))
            return ensure_utc(parsed)
        except ValueError:
            # Attempt fallback to numeric string
            try:
                as_number = float(value)
                return dt.datetime.fromtimestamp(as_number, tz=UTC)
            except ValueError:
                raise ValueError(f"Unsupported datetime string format: {value!r}") from None

    raise TypeError(f"Unsupported datetime value: {value!r}")


def normalize_iterable_to_utc(values: Iterable[dt.datetime | None]) -> list[dt.datetime | None]:
    """Normalize each datetime in iterable to UTC."""
    return [coerce_datetime(item) if isinstance(item, dt.datetime) else None for item in values]


def format_utc_datetime(value: dt.datetime | None) -> str | None:
    """
    Format a datetime to a UTC ISO 8601 string (trailing ``Z``).

    Returns None for None input.

    ⚠️ naive 输入的语义（与旧文档描述相反，务必先确认来源）：
    本函数委托给 :func:`utc_isoformat` → :func:`ensure_utc`，而 ``ensure_utc`` 的约定是
    **naive 视为 Asia/Shanghai**，因此 naive 输入会被真正换算，**瞬时整 8 小时地改变**
    （并不是"补个标记"）::

        >>> format_utc_datetime(dt.datetime(2026, 9, 17, 3, 0, 0))
        '2026-09-16T19:00:00Z'

    项目里 **DB 列存的是 naive UTC**（``utc_now_naive()`` / ``MemoryService._now()`` /
    ``Column(DateTime, default=utc_now_naive)``），所以本函数只适用于
    **用户输入**（表单/API 传来的无时区串，按本地时间解释）这类值的序列化。

    要序列化 DB 列（naive UTC）请用 :func:`format_naive_utc_datetime`。
    """
    if value is None:
        return None
    return utc_isoformat(value)


def format_naive_utc_datetime(value: dt.datetime | None) -> str | None:
    """
    Serialize a datetime that is **already UTC**, tolerating naive values.

    - naive → 视为 UTC，**只补标记、不做任何偏移**（对应 DB 列约定：``utc_now_naive()``）
    - aware → 正常换算到 UTC

    Returns None for None input.

    与 :func:`format_utc_datetime` 的唯一差别在 naive 输入：那个把 naive 当 Asia/Shanghai。
    对 aware 输入两者输出完全一致（都是 ``astimezone(UTC)`` + ``Z``）。
    """
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace(_ISO_Z_SUFFIX, "Z")


def utc_isoformat_from_timestamp(timestamp: float | int | None) -> str | None:
    """Format a Unix timestamp as an ISO 8601 UTC datetime string."""
    if timestamp is None:
        return None
    return dt.datetime.fromtimestamp(timestamp, tz=UTC).isoformat()


__all__ = [
    "UTC",
    "SHANGHAI_TZ",
    "utc_now",
    "utc_now_naive",
    "shanghai_now",
    "ensure_utc",
    "ensure_shanghai",
    "utc_isoformat",
    "shanghai_isoformat",
    "coerce_datetime",
    "coerce_any_to_utc_datetime",
    "normalize_iterable_to_utc",
    "format_utc_datetime",
    "format_naive_utc_datetime",
    "utc_isoformat_from_timestamp",
]
