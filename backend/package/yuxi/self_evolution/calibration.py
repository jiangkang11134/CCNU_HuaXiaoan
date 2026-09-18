"""阈值动态校准（P3，设计文档 §4.3）。

周期任务读上一窗口的监控信号（``metrics.py``），小幅调整 SystemKV
``correction_retrieval`` 中的 θ_d / θ_s，并把每次变更写成审计事件。

保守原则（与 v1.2"宁缺毋滥"一致）：

- **冷启动保护**：注入样本 < ``MIN_CALIBRATION_INJECTIONS`` 时一律不调阈值。
  冷启动期触发率天然偏低，此时放宽会把"阈值还没被验证"误当成"阈值太严"。
- **限幅**：每周期最多 ±10%，避免单周期噪声把阈值拉到底。
- **收紧优先于放宽**：触发率过高或有害率显著为正 → 收紧；只有在触发率长期
  过低**且**没有有害率证据时才放宽。
- **单条 confidence**：只用 EWMA 微调（样本 ≥ 3），不自动撤下——撤下走管理员撤回。
"""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.storage.postgres.models_business import CorrectionTicket, SystemKV

from .metrics import (
    collect_correction_metrics,
    collect_ticket_win_rates,
    resolve_window,
)
from .review_hook import REVIEWABLE_STATUSES, mark_conflicts_needing_review
from .service import (
    CORRECTION_RETRIEVAL_KV,
    DEFAULT_CONFLICT_DENSE_FLOOR,
    DEFAULT_CORRECTION_CONFIDENCE,
    DEFAULT_DENSE_THRESHOLD,
    DEFAULT_SPARSE_THRESHOLD,
    CorrectionService,
)

logger = logging.getLogger(__name__)

CALIBRATION_WINDOW_DAYS = 7
MIN_CALIBRATION_INJECTIONS = 200   # 冷启动保护：注入条次不足则不动阈值
MAX_ADJUST_STEP = 0.10             # 每周期限幅 ±10%
TRIGGER_RATE_HIGH = 0.15           # 健康区间上沿（设计文档 §4.2）
TRIGGER_RATE_LOW = 0.02            # 长期低于此值才考虑放宽
HARM_RATE_TOLERANCE = 0.05         # 有害率差值容差：超出才算"显著为正"
DENSE_BOUNDS = (0.40, 0.90)        # θ_d 调整上下限
SPARSE_BOUNDS = (0.10, 0.50)       # θ_s 调整上下限
CONFIDENCE_MIN_SAMPLES = 3         # 单条修正至少 3 次评价才更新 confidence
CONFIDENCE_EWMA_ALPHA = 0.3
CONFLICT_SCAN_LIMIT = 500          # 单次冲突扫描的工单上限
_AUDIT_UID = "system"


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _step(value: float, *, tighten: bool, bounds: tuple[float, float]) -> float:
    factor = 1 + MAX_ADJUST_STEP if tighten else 1 - MAX_ADJUST_STEP
    return round(_clamp(value * factor, bounds[0], bounds[1]), 4)


def _ewma(previous: float, sample: float, alpha: float = CONFIDENCE_EWMA_ALPHA) -> float:
    return (1 - alpha) * previous + alpha * sample


def decide_thresholds(current: dict, summary: dict) -> dict:
    """根据监控信号决定本周期阈值。纯函数，便于测试与回放。

    Returns:
        ``{"dense_threshold", "sparse_threshold", "action", "reason", "changed"}``，
        action ∈ {hold, tighten, loosen}。
    """
    dense = float(current.get("dense_threshold", DEFAULT_DENSE_THRESHOLD))
    sparse = float(current.get("sparse_threshold", DEFAULT_SPARSE_THRESHOLD))
    injections = int(summary.get("total_injections") or 0)
    unchanged = {"dense_threshold": dense, "sparse_threshold": sparse, "changed": False}

    if injections < MIN_CALIBRATION_INJECTIONS:
        return {**unchanged, "action": "hold",
                "reason": f"冷启动保护：注入样本 {injections} < {MIN_CALIBRATION_INJECTIONS}"}

    trigger = summary.get("trigger_rate")
    harm = summary.get("harm_rate_delta")
    if trigger is not None and trigger > TRIGGER_RATE_HIGH:
        action, reason = "tighten", f"触发率 {trigger:.1%} 高于健康上沿 {TRIGGER_RATE_HIGH:.0%}"
    elif harm is not None and harm > HARM_RATE_TOLERANCE:
        action, reason = "tighten", f"有害率差值 {harm:+.1%} 超过容差 {HARM_RATE_TOLERANCE:.0%}"
    elif trigger is not None and trigger < TRIGGER_RATE_LOW and harm is not None and harm <= 0:
        action, reason = "loosen", f"触发率 {trigger:.2%} 长期低于 {TRIGGER_RATE_LOW:.0%} 且无有害率证据"
    else:
        return {**unchanged, "action": "hold",
                "reason": f"信号不足或处于健康区间（触发率 {trigger}，有害率差值 {harm}）"}

    new_dense = _step(dense, tighten=action == "tighten", bounds=DENSE_BOUNDS)
    new_sparse = _step(sparse, tighten=action == "tighten", bounds=SPARSE_BOUNDS)
    if new_dense == dense and new_sparse == sparse:
        return {**unchanged, "action": "hold", "reason": f"{reason}；已触达调整边界"}
    return {"dense_threshold": new_dense, "sparse_threshold": new_sparse,
            "action": action, "reason": reason, "changed": True}


def decide_confidence_updates(ticket_stats: dict[int, dict], current: dict[int, float]) -> dict[int, float]:
    """单条修正置信度的 EWMA 更新；评价样本不足的票不动（防止单次点踩就降权）。"""
    updates: dict[int, float] = {}
    for ticket_id, stat in ticket_stats.items():
        if int(stat.get("rated") or 0) < CONFIDENCE_MIN_SAMPLES:
            continue
        win_rate = stat.get("win_rate")
        if win_rate is None:
            continue
        previous = float(current.get(ticket_id, DEFAULT_CORRECTION_CONFIDENCE))
        updates[int(ticket_id)] = round(_clamp(_ewma(previous, float(win_rate)), 0.0, 1.0), 4)
    return updates


async def _record_event(db: AsyncSession, payload: dict) -> None:
    """校准审计事件（追加写，与记忆事件同表，uid=system）。"""
    await CorrectionService._record_event(db, _AUDIT_UID, "correction_retrieval", 0, "calibrated",
                                      payload=payload, actor_type="system")


async def scan_conflicts(db: AsyncSession) -> list[tuple[int, int]]:
    """扫描已批准修正之间的适用范围冲突（P2 热路径冲突检测的离线版）。

    按 kb_id 分组：不同知识库的修正不会同时注入同一查询，跨库配对属误报。
    只处理带向量的票——无向量无法判断内容是否分歧（宁缺毋滥只作用于可判定项）。
    """
    from .service import _find_conflict_pairs

    kv = await db.get(SystemKV, CORRECTION_RETRIEVAL_KV)
    cfg = kv.value if kv is not None and isinstance(kv.value, dict) else {}
    floor = float(cfg.get("conflict_dense_floor", DEFAULT_CONFLICT_DENSE_FLOOR))
    rows = (await db.execute(select(CorrectionTicket).where(
        CorrectionTicket.status.in_(REVIEWABLE_STATUSES)).limit(CONFLICT_SCAN_LIMIT))).scalars().all()
    embeddings = {row.id: (getattr(row, "embedding", None) or {}).get("vector") for row in rows}
    grouped: dict[str | None, list] = {}
    for row in rows:
        grouped.setdefault(row.kb_id, []).append(row)
    pairs: list[tuple[int, int]] = []
    for group in grouped.values():
        pairs.extend((a.id, b.id) for a, b in _find_conflict_pairs(group, embeddings, floor))
    return pairs


async def _apply_confidence_updates(db: AsyncSession, ticket_stats: dict[int, dict]) -> dict[int, float]:
    if not ticket_stats:
        return {}
    rows = (await db.execute(select(CorrectionTicket).where(
        CorrectionTicket.id.in_(list(ticket_stats))))).scalars().all()
    by_id = {row.id: row for row in rows}
    current = {row.id: float(row.confidence) if row.confidence is not None else DEFAULT_CORRECTION_CONFIDENCE
               for row in rows}
    updates = decide_confidence_updates(ticket_stats, current)
    for ticket_id, value in updates.items():
        by_id[ticket_id].confidence = value
    return updates


async def _calibrate(db: AsyncSession, days: int) -> dict:
    since, until = resolve_window(days)
    summary = await collect_correction_metrics(db, since=since, until=until)
    kv = await db.get(SystemKV, CORRECTION_RETRIEVAL_KV)
    current = dict(kv.value) if kv is not None and isinstance(kv.value, dict) else {}
    decision = decide_thresholds(current, summary)
    ticket_stats = await collect_ticket_win_rates(db, since=since, until=until)
    confidence_updates = await _apply_confidence_updates(db, ticket_stats)
    # P2 遗留的冲突告警：热路径只写日志，这里做离线扫描 + 标记待裁决
    conflict_flags = await mark_conflicts_needing_review(db, await scan_conflicts(db))

    if decision["changed"] or confidence_updates:
        new_value = {**current,
                     "dense_threshold": decision["dense_threshold"],
                     "sparse_threshold": decision["sparse_threshold"],
                     "last_calibrated_at": until.isoformat()}
        if kv is None:
            db.add(SystemKV(key=CORRECTION_RETRIEVAL_KV, value=new_value,
                            description="反馈自进化检索阈值（P3 校准任务维护）"))
        else:
            kv.value = new_value
        await _record_event(db, {
            "action": decision["action"],
            "reason": decision["reason"],
            "window": {"since": since.isoformat(), "until": until.isoformat()},
            "thresholds": {
                "before": {"dense_threshold": current.get("dense_threshold", DEFAULT_DENSE_THRESHOLD),
                           "sparse_threshold": current.get("sparse_threshold", DEFAULT_SPARSE_THRESHOLD)},
                "after": {"dense_threshold": decision["dense_threshold"],
                          "sparse_threshold": decision["sparse_threshold"]},
            },
            "confidence_updates": {str(k): v for k, v in confidence_updates.items()},
            "metrics": {k: summary.get(k) for k in
                        ("total_answers", "injected_answers", "total_injections",
                         "trigger_rate", "harm_rate_delta")},
        })
    await db.commit()
    logger.info("correction calibration: action=%s reason=%s injections=%s",
                decision["action"], decision["reason"], summary.get("total_injections"))
    return {"decision": decision, "confidence_updates": confidence_updates,
            "conflict_flags": conflict_flags, "metrics": summary}


async def run_correction_calibration(*, days: int = CALIBRATION_WINDOW_DAYS, db: AsyncSession | None = None) -> dict:
    """周期校准入口：ARQ cron 调用（也可传 db 便于测试与人工重放）。"""
    if db is not None:
        return await _calibrate(db, days)
    from yuxi.storage.postgres.manager import pg_manager

    async with pg_manager.get_async_session_context() as session:
        return await _calibrate(session, days)
