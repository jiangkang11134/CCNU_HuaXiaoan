"""SystemKV 假查询助手（仅测试用）。

SystemKV 必须**按 key 查**（`select(SystemKV).filter(SystemKV.key == key)`），
不能按主键 `db.get(SystemKV, key)`——主键是整型 id。所以假 DB 的 `execute`
得能认出这条查询并把对应行交回去，否则被测代码会走空/报错。
"""

from __future__ import annotations

from types import SimpleNamespace


def system_kv_key(query) -> str | None:
    """从 ``select(SystemKV).filter(SystemKV.key == key)`` 里取出 key。

    不是这条查询时返回 None，调用方据此回落到自己的批次逻辑。
    """
    descs = getattr(query, "column_descriptions", None) or []
    if not descs:
        return None
    entity = descs[0].get("entity") if isinstance(descs[0], dict) else None
    if getattr(entity, "__name__", "") != "SystemKV":
        return None
    where = getattr(query, "whereclause", None)
    if where is None:
        return None
    value = getattr(getattr(where, "right", None), "value", None)
    return value if isinstance(value, str) else None


def system_kv_row(kv: dict, key: str | None):
    """把 kv 字典里的一条配置包装成带 .value 的行对象。"""
    if key is None or not isinstance(kv, dict):
        return None
    value = kv.get(key)
    if value is None:
        return None
    return SimpleNamespace(value=value)


class SystemKvResult:
    """最小 Result 替身：只需满足 get_system_kv 用到的 scalar_one_or_none。"""

    def __init__(self, row):
        self._row = row

    def scalar_one_or_none(self):
        return self._row

    def scalar(self):
        return self._row

    def scalars(self):
        return self

    def all(self):
        return [] if self._row is None else [self._row]

    def first(self):
        return self._row

    def __iter__(self):  # 兼容 list(result) 的用法
        return iter(self.all())


def fake_execute(db, query, fallback_result):
    """假 DB 的 execute 统一入口。

    SystemKV 按 key 的查询在这里被拦下并返回对应行；其他查询原样回落，
    这样同一个假 DB 既能喂业务行，也能喂配置行。
    """
    key = system_kv_key(query)
    if key is None:
        # 回落值允许是 callable：假 DB 常有"按调用顺序喂结果"的逻辑，
        # SystemKV 查询不应该白白消耗掉一个业务结果。
        return fallback_result() if callable(fallback_result) else fallback_result
    # kv 有两种形态，都要支持：
    #   - {key: value} 映射（按本次查询的 key 取）
    #   - 已经是行对象（带 .value），此时它代表该假 DB 唯一关心的那条配置
    for attr in ("_kv", "kv", "_system_kv", "_kvs"):
        candidate = getattr(db, attr, None)
        if candidate is None:
            continue
        if isinstance(candidate, dict):
            return SystemKvResult(system_kv_row(candidate, key))
        return SystemKvResult(candidate)
    return SystemKvResult(None)
