"""「我的记忆」接口：会话事实与长期记忆（记忆模块）。

纠错工单（自进化）的接口在 ``server.routers.self_evolution_router``，两者已完全分开。
"""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from yuxi.memory.service import MemoryService
from yuxi.storage.postgres.models_business import User
from yuxi.utils.datetime_utils import format_naive_utc_datetime
from server.utils.auth_middleware import get_admin_user, get_db, get_required_user

memory_router = APIRouter(prefix="/memory", tags=["memory"])

# 本模块返回的**每一个**时间字段都必须带 UTC 标记。
#
# 这些列都由 `utc_now_naive()` 写入，本质是 **naive UTC**
# （`utc_now_naive()` = `datetime.now(UTC).replace(tzinfo=None)`，只是去掉了标记）。
# 直接 `isoformat()` 出来没有时区标记，前端 `utils/time.js:formatDateTime` 会用 dayjs
# 按**本地时区**渲染 → 所有时间统一偏早 8 小时。
#
# ⚠️ 这里**不能**用 `utils.datetime_utils.format_utc_datetime`：它内部走 `ensure_utc()`，
# 而 `ensure_utc` 的约定是"naive 视为 Asia/Shanghai"（为**用户输入**按本地时间解释而设计），
# 对 DB 里的 naive UTC 会再减 8 小时。实测：
#     format_utc_datetime(datetime(2026, 9, 17, 3, 0, 0))
#       -> "2026-09-16T19:00:00Z"      # 比真实瞬间早 8 小时
# 与该函数自己的 docstring 互相矛盾（该 docstring 已在 2026-09-17 更正为如实描述）。
# 全站其余序列化 DB 列的点已于 2026-09-17 一并切到 `format_naive_utc_datetime`，
# 只剩 `knowledge/eval/service.py` 保持 `format_utc_datetime`——那几张表是
# `DateTime(timezone=True)`，asyncpg 返回 aware，两函数对 aware 输入输出一致。
# 现在改用 `datetime_utils.format_naive_utc_datetime`（只补标记、不做偏移）。
# self_evolution_router 里有一份同样委托的实现，两处必须保持一致。


def _iso_utc(value: datetime | None) -> str | None:
    """把「DB 里的 naive UTC」序列化成带 UTC 标记的 ISO 串（形如 ``...Z``）。

    委托给 :func:`yuxi.utils.datetime_utils.format_naive_utc_datetime`。
    保留本名字是因为它是本模块的既有契约（单测直接 import 它）。
    """
    return format_naive_utc_datetime(value)


def _memory_item(row):
    return {
        "id": row.id,
        "uid": row.uid,
        "fact_key": row.fact_key,
        "content": row.content,
        "status": row.status,
        "confidence": row.confidence,
        "scope": row.scope,
        "tags": row.tags or [],
        "graph_query_role": row.graph_query_role,
        "entity_hints": row.entity_hints or [],
        "intent_hints": row.intent_hints or [],
        "domain_scope": row.domain_scope,
        "expires_at": _iso_utc(row.expires_at),
        "confirmed_at": _iso_utc(row.confirmed_at),
        "confirmed_by": row.confirmed_by,
        # 「我的记忆」页需要向用户交代"这条是什么时候记下的/最后何时变了"，
        # 没有时间就无法判断一条 candidate 是不是早就过期的观察。
        # 管理员审计接口共用本序列化，纯增量字段，不影响既有调用方。
        "created_at": _iso_utc(row.created_at),
        "updated_at": _iso_utc(row.updated_at),
    }


@memory_router.get("")
async def list_memory(limit: int = Query(20, ge=1, le=100), db: AsyncSession = Depends(get_db), user: User = Depends(get_required_user)):
    rows = await MemoryService.list_memories(db, user.uid, limit)
    return {"items": [_memory_item(r) for r in rows]}


@memory_router.get("/events")
async def list_memory_events(
    target_type: str | None = Query(None, max_length=32),
    target_id: int | None = Query(None, ge=1),
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_required_user),
):
    rows = await MemoryService.list_events(db, user.uid, target_type, target_id, limit)
    return {"items": [{
        "event_id": row.event_id,
        "target_type": row.target_type,
        "target_id": row.target_id,
        "event_type": row.event_type,
        "before_status": row.before_status,
        "after_status": row.after_status,
        "payload": row.payload,
        "actor_type": row.actor_type,
        "actor_id": row.actor_id,
        "request_id": row.request_id,
        "created_at": _iso_utc(row.created_at),
    } for row in rows]}


@memory_router.get("/admin/all")
async def list_all_memory(
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    status: str | None = Query(None, max_length=32),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_admin_user),
):
    del user
    rows = await MemoryService.list_all_memories(db, limit, offset, status)
    return {"items": [_memory_item(r) for r in rows], "limit": limit, "offset": offset}


@memory_router.delete("/admin/{uid}/{memory_id}")
async def admin_delete_memory(
    uid: str,
    memory_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_admin_user),
):
    try:
        row = await MemoryService.tombstone_memory(db, uid, memory_id, user.uid)
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"id": row.id, "uid": row.uid, "status": row.status}


@memory_router.post("/{memory_id}/confirm")
async def confirm_memory(memory_id: int, db: AsyncSession = Depends(get_db), user: User = Depends(get_required_user)):
    try: row = await MemoryService.confirm_memory(db, user.uid, memory_id, user.uid)
    except Exception as exc: raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"id": row.id, "status": row.status}


@memory_router.post("/{memory_id}/retract")
async def retract_memory(memory_id: int, db: AsyncSession = Depends(get_db), user: User = Depends(get_required_user)):
    try: row = await MemoryService.retract_memory(db, user.uid, memory_id)
    except Exception as exc: raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"id": row.id, "status": row.status}


@memory_router.delete("/{memory_id}")
async def delete_memory(memory_id: int, db: AsyncSession = Depends(get_db), user: User = Depends(get_required_user)):
    try:
        row = await MemoryService.tombstone_memory(db, user.uid, memory_id)
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"id": row.id, "status": row.status}
