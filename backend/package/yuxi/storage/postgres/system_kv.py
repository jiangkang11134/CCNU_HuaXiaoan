"""SystemKV 读取助手。

⚠️ 不要用 ``db.get(SystemKV, key)``：SystemKV 的主键是自增整型 ``id``，
按主键取行必须传 int；传字符串 key 会被 asyncpg 拒绝并抛
``DataError: 'str' object cannot be interpreted as an integer``。

更隐蔽的是：抛错之后**当前事务会进入 aborted 状态**，同一 session 里后续
所有查询都会跟着报 ``InFailedSQLTransactionError``——即使调用点外面包了
try/except，事务也救不回来。所以每个读取 SystemKV 的地方都必须走这里。
"""

from __future__ import annotations

from sqlalchemy import select
from yuxi.storage.postgres.models_business import SystemKV


async def get_system_kv(db, key: str):
    """按 key 取 SystemKV 行；不存在返回 None。"""
    result = await db.execute(select(SystemKV).filter(SystemKV.key == key))
    return result.scalar_one_or_none()


__all__ = ["get_system_kv"]
