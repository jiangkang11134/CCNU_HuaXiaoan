"""会话事实与长期记忆的生命周期服务（记忆模块）。

记忆只是个性化/指代消歧/检索提示用的元数据，**绝不能作为实验室安全规范、化学品
性质或合规结论的证据**；安全结论必须来自知识库 / Graph RAG 的可追溯证据。
每次生命周期变更都追加到 ``memory_events`` 供审计与回滚。

与自进化（纠错工单）模块的分工（2026-09-17 拆分，两边互不 import）：
- 本模块：会话事实 ``session_facts``、长期记忆 ``user_memory_facts``，
  以及 LLM 抽取结果的落库裁决 :meth:`MemoryService.apply_extracted_ops`。
- ``yuxi.self_evolution``：问题反馈 → 纠错工单 → 管理员审核 → 图谱写回。
  那是自进化闭环，和记忆没有任何关系。

抽取口径（用户明确）：会话事实与长期记忆的抽取**只由 LLM 完成**；正则抽取路径已于
2026-09-17 整体删除，不要再加回来。
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.storage.postgres.event_log import record_event
from yuxi.storage.postgres.models_business import (
    MemoryEvent,
    SessionFact,
    UserMemoryFact,
)

from .observation import CHANNEL_MEMORY, CHANNEL_SESSION, arbitrate, memory_fact_ttl
from .state_machine import transition

logger = logging.getLogger(__name__)

MAX_SESSION_FACTS = 50
MAX_MEMORY_FACTS = 10

SESSION_FACT_TTL_DAYS = 2
SESSION_FACT_TTL = timedelta(days=SESSION_FACT_TTL_DAYS)
ACTIVE_MEMORY = ("candidate", "pending_confirmation", "confirmed")


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _new_session_status() -> str:
    """会话事实的**出生状态**：经状态机 ``observed -> active``（设计文档 §6.1/§6.3）。

    刻意不依赖 ``SessionFact.status`` 的 ORM ``default="active"``：那样状态就有两个
    来源，"状态只能由后端状态机改变"这条约束在创建路径上就不成立了；默认值一改，
    事实会悄悄换个状态出生（本项目已有同类坑：``enable_memory`` 的默认值散在四处）。
    """
    return transition("observed", "active", memory=False)



def _tokens(text: str) -> set[str]:
    words = re.findall(r"[A-Za-z0-9_]{2,}|[\u4e00-\u9fff]", (text or "").lower())
    return set(words)


def _memory_expiry(fact_key: str) -> datetime | None:
    """新建长期记忆时的到期时间；该键不过期则返回 ``None``。

    ``None`` 直接落库成空列，这正是 ``list_memories`` / ``build_context`` 里
    ``expires_at IS NULL OR expires_at > now()`` 认定的"永不过期"。
    """
    ttl = memory_fact_ttl(fact_key)
    return None if ttl is None else _now() + ttl


def _refresh_memory_expiry(row: UserMemoryFact) -> bool:
    """再次提及同一条记忆时，把有效期往后推满一个 TTL。返回是否真的改了。

    「还在说」= 「还有效」。抽取只在**内容相同**时才走这里（内容变了会 supersede 重建，
    新行天然拿到新的 expires_at），所以这个刷新既不会延长用户已经改口的旧值，
    也不会让一条早就不再被提及的记忆靠后门续命。

    **不过期的键在这里什么都不做**：它们的 ``expires_at`` 本来就是空的，
    硬写一个值会把"永久"悄悄变成"某时刻过期"——这正是本项目反复踩过的
    "默认值/空值被赋了一个看似无害的具体值"那类坑。
    """
    ttl = memory_fact_ttl(row.fact_key)
    if ttl is None:
        return False
    row.expires_at = _now() + ttl
    return True


class MemoryService:
    @staticmethod
    async def _record_event(
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
        """追加审计事件。实现在 :mod:`yuxi.storage.postgres.event_log`（与自进化模块共用）。"""
        await record_event(db, uid, target_type, target_id, event_type, before, after,
                          payload, actor_type, actor_id, request_id)

    @staticmethod
    async def confirm_memory(db: AsyncSession, uid: str, memory_id: int,
                             reviewer_uid: str | None = None):
        row = (await db.execute(select(UserMemoryFact).where(UserMemoryFact.id == memory_id, UserMemoryFact.uid == uid))).scalar_one()
        before = row.status
        row.status = transition(before, "confirmed")
        row.confidence = max(float(row.confidence or 0), 1.0)
        row.confirmed_at, row.confirmed_by = _now(), reviewer_uid or uid
        await MemoryService._record_event(db, uid, "user_memory", row.id, "confirmed", before, row.status,
                                          actor_type="user" if not reviewer_uid else "admin", actor_id=reviewer_uid or uid)
        await db.commit()
        return row

    @staticmethod
    async def retract_memory(db: AsyncSession, uid: str, memory_id: int):
        row = (await db.execute(select(UserMemoryFact).where(UserMemoryFact.id == memory_id, UserMemoryFact.uid == uid))).scalar_one()
        before = row.status
        row.status = transition(before, "retracted")
        await MemoryService._record_event(db, uid, "user_memory", row.id, "retracted", before, row.status, actor_type="user", actor_id=uid)
        await db.commit()
        return row

    @staticmethod
    async def tombstone_memory(db: AsyncSession, uid: str, memory_id: int,
                               actor_uid: str | None = None):
        row = (await db.execute(select(UserMemoryFact).where(
            UserMemoryFact.id == memory_id, UserMemoryFact.uid == uid,
        ))).scalar_one()
        before = row.status
        target = "tombstoned" if before == "confirmed" else "rejected"
        row.status = transition(before, target)
        await MemoryService._record_event(
            db, uid, "user_memory", row.id, target, before, row.status,
            actor_type="admin" if actor_uid and actor_uid != uid else "user",
            actor_id=actor_uid or uid,
        )
        await db.commit()
        return row

    @staticmethod
    async def list_memories(db: AsyncSession, uid: str, limit: int = MAX_MEMORY_FACTS):
        limit = max(1, min(limit, 100))
        now = _now()
        return (await db.execute(select(UserMemoryFact).where(
            UserMemoryFact.uid == uid,
            UserMemoryFact.status.in_(ACTIVE_MEMORY),
            or_(UserMemoryFact.expires_at.is_(None), UserMemoryFact.expires_at > now),
        ).order_by(UserMemoryFact.updated_at.desc()).limit(limit))).scalars().all()

    @staticmethod
    async def list_all_memories(db: AsyncSession, limit: int = 100,
                                offset: int = 0, status: str | None = None):
        query = select(UserMemoryFact)
        if status:
            query = query.where(UserMemoryFact.status == status)
        query = query.order_by(UserMemoryFact.updated_at.desc()).offset(max(0, offset)).limit(
            max(1, min(limit, 500))
        )
        return (await db.execute(query)).scalars().all()

    @staticmethod
    async def list_events(db: AsyncSession, uid: str, target_type: str | None = None,
                          target_id: int | None = None, limit: int = 100):
        query = select(MemoryEvent).where(MemoryEvent.uid == uid)
        if target_type:
            query = query.where(MemoryEvent.target_type == target_type)
        if target_id is not None:
            query = query.where(MemoryEvent.target_id == target_id)
        return (await db.execute(query.order_by(MemoryEvent.created_at.desc()).limit(max(1, min(limit, 500))))).scalars().all()

    @staticmethod
    async def expire_memories(db: AsyncSession, limit: int = 1000) -> int:
        """Mark elapsed user-memory and session facts as expired; periodic worker job."""
        now = _now()
        rows = (await db.execute(select(UserMemoryFact).where(
            UserMemoryFact.status.in_(("candidate", "pending_confirmation", "confirmed")),
            UserMemoryFact.expires_at.is_not(None), UserMemoryFact.expires_at <= now,
        ).limit(max(1, min(limit, 10000))))).scalars().all()
        for row in rows:
            before = row.status
            row.status = transition(before, "expired")
            await MemoryService._record_event(db, row.uid, "user_memory", row.id, "expired", before, row.status)
        expired = len(rows)
        session_rows = (await db.execute(select(SessionFact).where(
            SessionFact.status == "active",
            SessionFact.expires_at.is_not(None), SessionFact.expires_at <= now,
        ).limit(max(1, min(limit, 10000))))).scalars().all()
        for row in session_rows:
            row.status = transition(row.status, "expired", memory=False)
            await MemoryService._record_event(db, row.uid, "session_fact", row.id, "expired", "active", row.status)
        expired += len(session_rows)
        if expired:
            await db.commit()
        return expired

    @staticmethod
    async def build_context_details(db: AsyncSession, uid: str, thread_id: str,
                                    enabled: bool = False, query: str = "") -> dict:
        now = _now()
        sessions = (await db.execute(select(SessionFact).where(
            SessionFact.uid == uid, SessionFact.thread_id == thread_id, SessionFact.status == "active",
            or_(SessionFact.expires_at.is_(None), SessionFact.expires_at > now),
        ).order_by(SessionFact.updated_at.desc()).limit(MAX_SESSION_FACTS))).scalars().all()
        memories = []
        if enabled:
            memories = (await db.execute(select(UserMemoryFact).where(
                UserMemoryFact.uid == uid, UserMemoryFact.status == "confirmed",
                or_(UserMemoryFact.expires_at.is_(None), UserMemoryFact.expires_at > now),
            ).order_by(UserMemoryFact.updated_at.desc()).limit(100))).scalars().all()
            qtokens = _tokens(query)
            if qtokens:
                ranked = []
                for m in memories:
                    hay = _tokens(f"{m.fact_key} {m.content} {' '.join(m.tags or [])} {' '.join(m.entity_hints or [])} {' '.join(m.intent_hints or [])}")
                    score = len(qtokens & hay)
                    if score or m.graph_query_role == "personalize":
                        ranked.append((score, m))
                memories = [m for _, m in sorted(ranked, key=lambda x: (x[0], x[1].updated_at), reverse=True)[:MAX_MEMORY_FACTS]]
            else:
                memories = memories[:MAX_MEMORY_FACTS]
        return {
            "session_facts": sessions,
            "memory_facts": memories,
            "entity_hints": sorted({h for m in memories for h in (m.entity_hints or [])}),
            "intent_hints": sorted({h for m in memories for h in (m.intent_hints or [])}),
            "domain_scope": sorted({m.domain_scope for m in memories if m.domain_scope}),
        }

    @staticmethod
    async def build_context(db: AsyncSession, uid: str, thread_id: str,
                            enabled: bool = False, query: str = "",
                            suppressed_fact_keys: set[str] | None = None) -> str:
        # 说明：此处曾有按 (uid, thread_id, query_hash) 缓存 30s 的实现，但读写两端都被
        # `if not query` 短路——问答路径 query 恒非空，因此缓存既不会命中也不会写入，
        # 属于死代码。且按 query 哈希做 key、30s TTL 在自然对话里命中率接近 0，启用后
        # 反而会让本轮刚提取的会话事实有最多 30s 不可见。故移除。
        # 若后续压测表明上下文构建成为瓶颈，应改做「会话级缓存 + 事实写入时主动失效」，
        # 而不是恢复按 query 哈希的短 TTL 缓存。
        #
        # suppressed_fact_keys：被个人资料覆盖掉的长期记忆键（`user.*`），由
        # `yuxi.services.user_profile.overridden_fact_keys` 给出。冲突仲裁是
        # 「会话事实 > 个人资料 > 长期记忆」，个人资料里有值时长期记忆必须让位，
        # 否则模型会同时看到「专业：应用化学」和 `[long_term] user.major: 化学工程`
        # 两个答案，等于把仲裁权又推回给模型。
        # **只摘长期记忆**：会话事实在这一规则里是最高层，永远不摘。
        # 只在注入文本层摘，不动 `build_context_details`——`/api/memory` 要给用户看
        # 自己的完整记忆，那里抑制是错的。
        details = await MemoryService.build_context_details(db, uid, thread_id, enabled, query)
        lines = [f"- [session] {r.fact_key}: {r.content}" for r in details["session_facts"]]
        suppressed = suppressed_fact_keys or set()
        lines.extend(
            f"- [long_term] {r.fact_key}: {r.content}"
            for r in details["memory_facts"]
            if r.fact_key not in suppressed
        )
        result = "\n".join(lines)
        if result:
            # 三级优先级（用户明确口径）：已批准并重新入库的修正 > 知识库/Graph RAG
            # 可追溯证据 > 本段记忆与偏好。
            # 这里必须把**完整三级**写出来，不能只写"安全结论必须来自知识库"——
            # 那样与 [权威修正] 那段"与知识库冲突时以修正为准"合起来是残缺的排序：
            # 模型会以为知识库是最高权威，而第一优先级其实是管理员批准的修正。
            result = (
                "以下内容仅用于个性化、指代消歧和检索提示，不得作为实验室安全规范、"
                "化学品性质或合规结论的证据。结论依据的优先级："
                "已批准并重新入库的修正 > 知识库/Graph RAG 可追溯证据 > 本段记忆与偏好。\n"
                + result
            )
        return result

    @staticmethod
    async def _spawn_session_fact(db: AsyncSession, uid: str, thread_id: str,
                                  fact_key: str, content: str, *, source: str,
                                  actor_type: str = "system",
                                  request_id: str | None = None,
                                  supersedes_id: int | None = None) -> SessionFact:
        """按状态机新建一条会话事实：``observed -> active``（设计文档 §6.1）。

        ``source``/``actor_type`` 只用于审计留痕。当前**唯一**产出路径是 LLM 观察协议
        （``source="llm_observation"`` / ``actor_type="llm"``）：正则抽取路径已于
        2026-09-17 按要求整体删除，会话事实与长期记忆的抽取只由 LLM 完成。

        为什么记**两个**事件而不是一个：``observed`` 在设计里的含义是
        "本轮刚观察到，尚未完成去重/冲突检查"（§6.1）。调用方已同步做完重复判等，
        所以同一事务内立刻 promote——但只有把这一步也留痕，"观察到"才在审计里可见。
        否则 ``observed`` 会退化成一个谁也没见过的状态（改前即如此：
        ``SESSION_TRANSITIONS`` 定义了它，全代码库却没有任何地方写入过；
        而前端 ``EVENT_LABELS`` 早已备好"系统观察到"的标签）。
        """
        row = SessionFact(
            uid=uid, thread_id=thread_id, fact_key=fact_key, content=content,
            status=_new_session_status(), source_request_id=request_id,
            supersedes_id=supersedes_id, expires_at=_now() + SESSION_FACT_TTL)
        db.add(row)
        await db.flush()
        await MemoryService._record_event(
            db, uid, "session_fact", row.id, "observed", None, "observed",
            {"fact_key": fact_key, "source": source},
            actor_type=actor_type, request_id=request_id)
        row.status = transition("observed", "active", memory=False)
        await MemoryService._record_event(
            db, uid, "session_fact", row.id, "promote", "observed", "active",
            {"fact_key": fact_key, "source": source},
            actor_type=actor_type, request_id=request_id)
        return row

    @staticmethod
    async def _apply_session_fact(
        db: AsyncSession,
        uid: str,
        thread_id: str,
        fact_key: str,
        content: str,
        *,
        source: str,
        request_id: str | None,
    ) -> bool:
        """写一条会话事实；内容相同视为已存在。返回是否新建。

        "已有即跳过"是幂等的关键：后台抽取可能因重试重放同一轮，
        内容没变就不该再产出一条新事实（否则每次重试都会刷一条 supersede 记录）。
        """
        old = (await db.execute(select(SessionFact).where(
            SessionFact.uid == uid, SessionFact.thread_id == thread_id,
            SessionFact.fact_key == fact_key, SessionFact.status == "active",
        ))).scalar_one_or_none()
        if old and old.content == content:
            return False
        if old:
            old.status = transition(old.status, "superseded", memory=False)
            await MemoryService._record_event(
                db, uid, "session_fact", old.id, "superseded", "active", "superseded",
                {"source": source}, request_id=request_id)
        await MemoryService._spawn_session_fact(
            db, uid, thread_id, fact_key, content, source=source,
            actor_type="llm", request_id=request_id,
            supersedes_id=old.id if old else None)
        return True

    @staticmethod
    async def _apply_memory_fact(
        db: AsyncSession,
        uid: str,
        fact_key: str,
        content: str,
        confidence: float,
        status: str,
        *,
        source: str,
        request_id: str | None,
    ) -> str:
        """写一条长期记忆，返回 ``created`` / ``confirmed`` / ``renewed`` / ``skipped``。

        已 confirmed 的旧事实不会被低置信度的新抽取覆盖——宁可保留用户已确认的，
        也不让模型的一次猜测把它推翻。

        新建时按 :func:`yuxi.memory.observation.memory_fact_ttl` 写入 ``expires_at``
        （没有 TTL 的键留空 = 永久）；**内容相同的重复提及会续期**，见
        :func:`_refresh_memory_expiry`。
        """
        memory = (await db.execute(select(UserMemoryFact).where(
            UserMemoryFact.uid == uid, UserMemoryFact.fact_key == fact_key,
            UserMemoryFact.status.in_(ACTIVE_MEMORY),
        ))).scalar_one_or_none()
        if memory and memory.content == content:
            # 内容没变，但"用户又提了一次"本身就是有效性信号：把有效期往后推。
            # 放在状态判断之前，是因为续期与"是否顺带升级成 confirmed"是两件事：
            # 一条 candidate 被再次提及也该续期，否则它在被确认前就可能先过期。
            renewed = _refresh_memory_expiry(memory)
            if status == "confirmed" and memory.status != "confirmed":
                before = memory.status
                memory.status = transition(before, "confirmed")
                memory.confidence = max(float(memory.confidence or 0.0), confidence)
                memory.confirmed_at, memory.confirmed_by = _now(), uid
                await MemoryService._record_event(
                    db, uid, "user_memory", memory.id, "confirmed", before, "confirmed",
                    {"source": source, "renewed": renewed}, actor_type="llm", request_id=request_id)
                return "confirmed"
            if renewed:
                # 记一条事件，否则"续期到底有没有生效"只能靠查 expires_at 猜。
                await MemoryService._record_event(
                    db, uid, "user_memory", memory.id, "renewed", memory.status, memory.status,
                    {"source": source, "expires_at": memory.expires_at.isoformat()},
                    actor_type="llm", request_id=request_id)
                return "renewed"
            return "skipped"
        if memory:
            if memory.status == "confirmed" and status != "confirmed":
                return "skipped"
            before = memory.status
            memory.status = transition(before, "superseded")
            await MemoryService._record_event(
                db, uid, "user_memory", memory.id, "superseded", before, "superseded",
                {"source": source}, request_id=request_id)
        row = UserMemoryFact(
            uid=uid, fact_key=fact_key, content=content, status=status,
            confidence=confidence, source_request_id=request_id,
            expires_at=_memory_expiry(fact_key))
        db.add(row)
        await db.flush()
        await MemoryService._record_event(
            db, uid, "user_memory", row.id, "created", None, status,
            {"fact_key": fact_key, "source": source, "confidence": confidence,
             "expires_at": row.expires_at.isoformat() if row.expires_at else None},
            actor_type="llm", request_id=request_id)
        return "created"

    @staticmethod
    async def apply_extracted_ops(
        db: AsyncSession,
        uid: str,
        thread_id: str,
        ops: list[tuple[str, str, str, float, str | None]],
        *,
        source: str = "async_extraction",
        memory_enabled: bool = True,
    ) -> dict:
        """按**显式通道**落库：``session`` 只进会话事实表，``memory`` 只进长期记忆表。

        ops 元素为 ``(channel, fact_key, content, confidence, request_id)``，通道由
        :func:`yuxi.memory.observation.canonical_channel` 按 key 前缀裁定。

        通道之所以由服务端定而不是信模型：key 前缀本身已经编码了归属，让模型自由填
        就会出现"偏好写进会话事实表、会话事实写进长期记忆表"的方向对穿——这正是改造前
        ``apply_observed_preferences`` 对一条 op 同时写两张表造成的缺陷。

        长期记忆仍受用户级 ``memory_enabled`` 控制；会话事实不受它管（设计文档 §2.3）。
        """
        stats = {"session": 0, "memory": 0, "confirmed": 0, "renewed": 0, "rejected": 0, "skipped": 0}
        if not ops:
            return stats
        for channel, fact_key, content, confidence, request_id in ops:
            if channel == CHANNEL_SESSION:
                if await MemoryService._apply_session_fact(
                    db, uid, thread_id, fact_key, content,
                    source=source, request_id=request_id,
                ):
                    stats["session"] += 1
                continue
            if channel != CHANNEL_MEMORY:
                # 通道是服务端裁定的，出现第三种取值说明调用方绕过了 canonical_channel。
                # 这里必须拒绝而不是"当成长期记忆"——默认落到更宽松的那一边，
                # 等于把"通道由服务端定"这条边界又还给了调用方。
                stats["rejected"] += 1
                continue
            if not memory_enabled:
                stats["skipped"] += 1
                continue
            status = arbitrate(fact_key, confidence)
            if status == "rejected":
                stats["rejected"] += 1
                continue
            outcome = await MemoryService._apply_memory_fact(
                db, uid, fact_key, content, confidence, status,
                source=source, request_id=request_id,
            )
            if outcome == "created":
                stats["memory"] += 1
            elif outcome == "confirmed":
                stats["confirmed"] += 1
            elif outcome == "renewed":
                # 单独计数而不是并进 skipped：续期是"这条记忆又被提了一次"的证据，
                # 与"内容重复、什么都没发生"在排查时含义完全相反。
                stats["renewed"] += 1
            else:
                stats["skipped"] += 1
        await db.commit()
        return stats
