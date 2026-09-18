"""文档重摄入触发的修正复核（P3，设计文档 §4.4）。

修正的失效场景不是"过了 N 天"，而是"它所修正的对比基准（源文档）变了"。因此
陈旧防护走事件驱动，而非日历定时器：

1. 文档重新摄入/新增时，图谱抽取产出**本次**的实体集（由
   ``MilvusGraphService.build_pending_chunks`` 在抽取循环里收集）；
2. 与该 KB 内 approved/applied 修正的 ``entity_hints`` 求交集；
3. 有交集的修正标记 ``needs_review``，提示管理员复核。

设计取舍：**只标记，不撤下注入**。文档更新不等于修正失效（更新后的文档往往仍
与修正一致），自动撤下会把"提示"变成"误伤"。撤回仍由管理员明确操作。

替代方案的取舍：若改为"查该 KB 全部历史实体"，则任何一次文档入库都会惊动所有
修正，标记会退化成噪音；所以必须用"本次抽取"的实体集。
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.storage.postgres.models_business import CorrectionTicket

from .service import CorrectionService

logger = logging.getLogger(__name__)

MAX_MATCHED_IN_REASON = 5
REVIEWABLE_STATUSES = ("approved", "applied")


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _normalize(names) -> set[str]:
    """实体名归一化（去空白 + 大小写折叠）；跳过 None/空串，避免把 str(None) 当成实体。"""
    return {str(n).strip().casefold() for n in (names or []) if n is not None and str(n).strip()}


def _matched_hints(entity_hints, normalized_names: set[str]) -> list[str]:
    """返回命中的 entity_hints 原文（保留大小写，便于管理员识别）。"""
    return [str(h).strip() for h in (entity_hints or [])
            if h is not None and str(h).strip() and str(h).strip().casefold() in normalized_names]


async def mark_corrections_needing_review(db: AsyncSession, *, kb_id: str, entity_names) -> dict:
    """把与本次抽取实体重叠的已批准修正标为待复核。幂等（已标记的不重复处理）。"""
    normalized = _normalize(entity_names)
    if not kb_id or not normalized:
        return {"candidates": 0, "marked": 0, "ticket_ids": []}

    rows = (await db.execute(select(CorrectionTicket).where(
        CorrectionTicket.kb_id == kb_id,
        CorrectionTicket.status.in_(REVIEWABLE_STATUSES),
        CorrectionTicket.needs_review.is_(False),
    ))).scalars().all()
    if not rows:
        return {"candidates": 0, "marked": 0, "ticket_ids": []}

    marked_ids: list[int] = []
    now = _now()
    for row in rows:
        matched = _matched_hints(row.entity_hints, normalized)
        if not matched:
            continue
        reason = ("源文档已更新，涉及实体：" + "、".join(matched[:MAX_MATCHED_IN_REASON]))[:200]
        row.needs_review = True
        row.needs_review_reason = reason
        row.needs_review_at = now
        marked_ids.append(row.id)
        await CorrectionService._record_event(
            db, row.uid, "correction", row.id, "needs_review", None, None,
            {"reason": reason, "matched_entities": matched, "kb_id": kb_id},
            actor_type="system",
        )
    if marked_ids:
        await db.commit()
        logger.warning("corrections flagged for review after KB %s re-ingest: %s", kb_id, marked_ids)
    return {"candidates": len(rows), "marked": len(marked_ids), "ticket_ids": marked_ids}


async def mark_corrections_for_reingested_entities(kb_id: str, entity_names) -> dict:
    """图谱构建侧调用的入口：自建 session，失败只告警，绝不影响文档摄入主链路。"""
    normalized = _normalize(entity_names)
    if not kb_id or not normalized:
        return {"candidates": 0, "marked": 0, "ticket_ids": []}
    try:
        from yuxi.storage.postgres.manager import pg_manager

        async with pg_manager.get_async_session_context() as db:
            return await mark_corrections_needing_review(db, kb_id=kb_id, entity_names=normalized)
    except Exception:
        logger.exception("failed to flag corrections for review (kb_id=%s)", kb_id)
        return {"candidates": 0, "marked": 0, "ticket_ids": []}


async def mark_conflicts_needing_review(db: AsyncSession, pairs: list[tuple[int, int]]) -> dict:
    """冲突告警落地（P3 §4.3，补上 P2 遗留的"热路径只记日志"）。

    适用范围重叠但终裁内容分歧的修正对，双方标记 needs_review 交给管理员裁决。
    已标记的票跳过，避免每周重复告警（原因串不覆盖）。
    """
    if not pairs:
        return {"marked": 0, "ticket_ids": []}
    counterpart: dict[int, set[int]] = {}
    for a, b in pairs:
        counterpart.setdefault(int(a), set()).add(int(b))
        counterpart.setdefault(int(b), set()).add(int(a))

    rows = (await db.execute(select(CorrectionTicket).where(
        CorrectionTicket.id.in_(list(counterpart))))).scalars().all()
    marked_ids: list[int] = []
    now = _now()
    for row in rows:
        if row.needs_review:
            continue
        others = sorted(counterpart.get(row.id, ()))
        reason = ("与修正#" + "、#".join(str(o) for o in others) + " 适用范围重叠但内容存在分歧，待裁决")[:200]
        row.needs_review = True
        row.needs_review_reason = reason
        row.needs_review_at = now
        marked_ids.append(row.id)
        await CorrectionService._record_event(
            db, row.uid, "correction", row.id, "conflict_flagged", None, None,
            {"counterparts": others}, actor_type="system",
        )
    if marked_ids:
        await db.commit()
        logger.warning("conflicting corrections flagged for review: %s", marked_ids)
    return {"marked": len(marked_ids), "ticket_ids": marked_ids}


async def clear_review_flag(db: AsyncSession, ticket_id: int, *, reviewer_uid: str,
                           note: str | None = None):
    """管理员复核完成：清除标记（修正内容仍有效时调用）。复核历史留在审计事件里。"""
    row = (await db.execute(select(CorrectionTicket).where(
        CorrectionTicket.id == ticket_id))).scalar_one()
    if not row.needs_review:
        raise ValueError("ticket is not flagged for review")
    row.needs_review = False
    row.needs_review_reason = (note or "").strip()[:200] or None
    row.needs_review_at = None
    await CorrectionService._record_event(
        db, row.uid, "correction", row.id, "review_acknowledged", None, None,
        {"note": (note or "")[:500]}, actor_type="admin", actor_id=reviewer_uid,
    )
    await db.commit()
    return row
