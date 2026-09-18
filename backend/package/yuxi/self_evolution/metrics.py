"""反馈自进化监控指标（P3，设计文档 §4.2）。

信号源复用既有数据，不新建表：

- 是否注入、注入了哪些修正：``messages.extra_metadata['injected_corrections']``
  （P3 埋点，见 ``CorrectionService.build_correction_context_details``）
- 赞踩：``message_feedbacks.rating``（既有 MessageFeedback）

三个指标：

- **触发率** = 注入修正的回答数 / 总回答数。健康区间约 5%~15%；过高说明阈值过松或反馈泛滥。
- **有害率** = 注入组点踩率 − 未注入组点踩率（差值）。持续为正 → 全局收紧 θ。
- **胜率** = 单条修正被注入回答的赞踩比，供 ``calibration`` 做 confidence 的 EWMA 更新。

口径诚实声明：这是**观察性对比，不是因果推断**——被注入修正的问题本来就更容易是
"历史上答错过的难题"，因此有害率为正不必然归因于注入本身。正因如此校准只在信号
足够强时小幅调整（见 calibration.py 的冷启动保护与 ±10% 限幅）。

另一条口径前提：埋点字段是 P3 才写入的，此前的历史回答没有该字段、会被计为"未注入"，
因此上线后第一个窗口的触发率天然偏低。校准的冷启动保护（注入条次 < 200 一律不动）
天然规避了这个问题；人工看报表时请只采信埋点生效之后的窗口。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.storage.postgres.models_business import Message, MessageFeedback

LIKE = "like"
DISLIKE = "dislike"
DEFAULT_METRICS_LIMIT = 20000


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _ratio(numerator: int, denominator: int) -> float | None:
    """分母为 0 时返回 None（信号不足，不参与判定），避免把"无数据"当成 0。"""
    if denominator <= 0:
        return None
    return numerator / denominator


def _dislike_rate(like: int, dislike: int) -> float | None:
    return _ratio(dislike, like + dislike)


def summarize_injections(rows: list[tuple[int, str | None]]) -> dict:
    """聚合回答级信号。

    Args:
        rows: ``(injected_count, rating)`` 列表。``injected_count`` 为该回答实际注入的
            修正条数（0 = 未注入）；``rating`` 为 ``"like"`` / ``"dislike"`` / None。
    """
    total_answers = len(rows)
    injected_answers = 0
    total_injections = 0
    stats = {
        True: {"like": 0, "dislike": 0},
        False: {"like": 0, "dislike": 0},
    }
    for injected_count, rating in rows:
        injected = int(injected_count or 0) > 0
        if injected:
            injected_answers += 1
            total_injections += int(injected_count)
        if rating in (LIKE, DISLIKE):
            stats[injected][rating] += 1

    injected_dislike_rate = _dislike_rate(stats[True]["like"], stats[True]["dislike"])
    baseline_dislike_rate = _dislike_rate(stats[False]["like"], stats[False]["dislike"])
    harm_delta = None
    if injected_dislike_rate is not None and baseline_dislike_rate is not None:
        harm_delta = injected_dislike_rate - baseline_dislike_rate

    return {
        "total_answers": total_answers,
        "injected_answers": injected_answers,
        "total_injections": total_injections,
        "trigger_rate": _ratio(injected_answers, total_answers),
        "injected": {**stats[True], "rated": stats[True]["like"] + stats[True]["dislike"],
                     "dislike_rate": injected_dislike_rate},
        "baseline": {**stats[False], "rated": stats[False]["like"] + stats[False]["dislike"],
                     "dislike_rate": baseline_dislike_rate},
        "harm_rate_delta": harm_delta,
    }


def summarize_ticket_win_rates(rows: list[tuple[int, str | None]]) -> dict[int, dict]:
    """聚合单条修正的胜率。

    Args:
        rows: ``(ticket_id, rating)`` 列表，每个元素是"一条被注入回答 + 该回答的评价"。
    """
    per_ticket: dict[int, dict] = {}
    for ticket_id, rating in rows:
        bucket = per_ticket.setdefault(int(ticket_id), {"injections": 0, "like": 0, "dislike": 0})
        bucket["injections"] += 1
        if rating in (LIKE, DISLIKE):
            bucket[rating] += 1
    for bucket in per_ticket.values():
        rated = bucket["like"] + bucket["dislike"]
        bucket["rated"] = rated
        bucket["win_rate"] = _ratio(bucket["like"], rated)
    return per_ticket


def _injections_of(meta) -> list:
    if not isinstance(meta, dict):
        return []
    value = meta.get("injected_corrections")
    return value if isinstance(value, list) else []


async def collect_correction_metrics(db: AsyncSession, *, since: datetime, until: datetime | None = None,
                                     limit: int = DEFAULT_METRICS_LIMIT) -> dict:
    """统计窗口 [since, until) 内的回答级监控指标。"""
    until = until or _now()
    rows = (await db.execute(
        select(Message.id, Message.extra_metadata)
        .where(Message.role == "assistant",
               Message.created_at >= since,
               Message.created_at < until)
        .order_by(Message.id.desc())
        .limit(max(1, min(limit, 100000))))).all()

    counts: dict[int, int] = {}
    for message_id, meta in rows:
        counts[int(message_id)] = len(_injections_of(meta))

    ratings: dict[int, str] = {}
    if counts:
        feedback_rows = (await db.execute(
            select(MessageFeedback.message_id, MessageFeedback.rating)
            .where(MessageFeedback.message_id.in_(list(counts)))
            .order_by(MessageFeedback.created_at.asc()))).all()
        for message_id, rating in feedback_rows:
            # 同一回答可能被多个用户评价：以最新一条为准，与前端展示口径一致
            ratings[int(message_id)] = str(rating)

    summary = summarize_injections([(n, ratings.get(mid)) for mid, n in counts.items()])
    summary["window"] = {"since": since.isoformat(), "until": until.isoformat()}
    return summary


async def collect_ticket_win_rates(db: AsyncSession, *, since: datetime, until: datetime | None = None,
                                   limit: int = DEFAULT_METRICS_LIMIT) -> dict[int, dict]:
    """统计窗口内每条修正的胜率（只统计被注入过的回答）。"""
    until = until or _now()
    rows = (await db.execute(
        select(Message.id, Message.extra_metadata)
        .where(Message.role == "assistant",
               Message.created_at >= since,
               Message.created_at < until)
        .order_by(Message.id.desc())
        .limit(max(1, min(limit, 100000))))).all()

    message_injections: dict[int, list] = {}
    for message_id, meta in rows:
        injections = _injections_of(meta)
        if injections:
            message_injections[int(message_id)] = injections

    pairs: list[tuple[int, str | None]] = []
    if message_injections:
        feedback_rows = (await db.execute(
            select(MessageFeedback.message_id, MessageFeedback.rating)
            .where(MessageFeedback.message_id.in_(list(message_injections)))
            .order_by(MessageFeedback.created_at.asc()))).all()
        ratings = {int(mid): str(rating) for mid, rating in feedback_rows}
        for message_id, injections in message_injections.items():
            rating = ratings.get(message_id)
            for item in injections:
                ticket_id = item.get("ticket_id") if isinstance(item, dict) else None
                if ticket_id is not None:
                    pairs.append((int(ticket_id), rating))
    return summarize_ticket_win_rates(pairs)


def resolve_window(days: int, *, until: datetime | None = None) -> tuple[datetime, datetime]:
    """把"最近 N 天"解析成 [since, until)。"""
    until = until or _now()
    return until - timedelta(days=max(1, int(days))), until
