"""``memory_events`` 审计事件的唯一写入器。

这张表被**两个互不相关的模块**共用：

- 记忆模块（``yuxi.memory``）：会话事实 / 长期记忆的生命周期变更；
- 自进化模块（``yuxi.self_evolution``）：纠错工单的建单 / 审核 / 写回 / 撤回。

两者都有自己的 ``_record_event`` 静态方法委托到这里，所以 ``event_id`` 的派生方式
只有一份实现。这不是"记忆和工单耦合"——表只是一条 append-only 事件流，两个模块
各写各的 ``target_type``，互不读取对方的行。

⚠️ 表名 ``memory_events`` 是历史遗留（拆分前纠错工单也住在 memory 包里）。
要改名得走一次 DB 迁移，不在本次范围。
"""
from __future__ import annotations

import hashlib
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.storage.postgres.models_business import MemoryEvent


async def record_event(
    db: AsyncSession,
    uid: str,
    target_type: str,
    target_id: int,
    event_type: str,
    before: str | None = None,
    after: str | None = None,
    payload: dict | None = None,
    actor_type: str = "system",
    actor_id: str | None = None,
    request_id: str | None = None,
) -> None:
    """追加一条审计事件（不 commit，由调用方在事务里统一提交）。

    ``event_id`` 前缀 uid / target / event_type，尾接 uuid4：既保证同一次变更在
    日志里可读，又保证不会因重复操作而撞主键。
    """
    event_id = hashlib.sha256(
        f"{uid}:{target_type}:{target_id}:{event_type}:{uuid4()}".encode()
    ).hexdigest()[:64]
    db.add(MemoryEvent(event_id=event_id, uid=uid, target_type=target_type,
                       target_id=int(target_id), event_type=event_type,
                       before_status=before, after_status=after,
                       payload=payload or {}, actor_type=actor_type,
                       actor_id=actor_id, request_id=request_id))
