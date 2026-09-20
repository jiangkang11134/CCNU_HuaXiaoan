"""自进化（问题反馈 → 纠错工单 → 图谱写回）管理端接口。

与 ``memory_router`` 完全独立：那边是"我的记忆"（会话事实 / 长期记忆），
这边是纠错工单的建单、审核、图谱写回与撤回。

2026-09-17 从 ``memory_router`` 拆出，HTTP 路径随之从
``/api/memory/corrections*`` 迁到 ``/api/self-evolution/corrections*``。
"""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.exc import NoResultFound
from sqlalchemy.ext.asyncio import AsyncSession
from yuxi.self_evolution.enrichment import enrich_correction
from yuxi.self_evolution.graph_writeback import withdraw_correction_from_graph, write_correction_to_graph
from yuxi.self_evolution.metrics import collect_correction_metrics, collect_ticket_win_rates, resolve_window
from yuxi.self_evolution.review_hook import clear_review_flag
from yuxi.self_evolution.scope import SCOPE_KB_TRUTH, SCOPE_LABELS, VALID_SCOPES
from yuxi.self_evolution.service import CorrectionService
from yuxi.self_evolution.tasks import enqueue_correction_graph_writeback
from yuxi.storage.postgres.system_kv import get_system_kv
from yuxi.storage.postgres.models_business import CorrectionTicket, SystemKV, User
from yuxi.utils.datetime_utils import ensure_utc, format_naive_utc_datetime
from server.utils.auth_middleware import get_admin_user, get_db, get_required_user

self_evolution_router = APIRouter(prefix="/self-evolution", tags=["self-evolution"])


def _iso_utc(value: datetime | None) -> str | None:
    """把「DB 里的 naive UTC」序列化成带 UTC 标记的 ISO 串（形如 ``...Z``）。

    这些列由 ``utc_now_naive()`` / ``self_evolution.service._now()`` 写入，本质是
    **naive UTC**（只是去掉了标记）。不带标记的话前端 dayjs 会按本地时区渲染，
    所有时间统一偏早 8 小时。**不能**改用 ``format_utc_datetime``：它走 ``ensure_utc()``
    （约定 naive = Asia/Shanghai），对 DB 里的 naive UTC 会再减 8 小时。

    单一事实来源是 :func:`yuxi.utils.datetime_utils.format_naive_utc_datetime`；
    ``memory_router`` 里有一份同样委托的实现，两处必须保持一致。
    """
    return format_naive_utc_datetime(value)


def _parse_expires_at(raw) -> datetime | None:
    """有效期可选参数：ISO 日期/日期时间；空 = 永不过期（v1.2 定稿）。

    落库值必须是 **naive UTC**，与读取侧完全对齐：``self_evolution.service._now()``、
    ``build_correction_context_details`` 的 ``CorrectionTicket.expires_at > _now()``。

    这里曾经用无参 ``astimezone()``（= 转**本地**时区）再 strip tz。容器 TZ 是 Asia/Shanghai
    （``docker/api.Dockerfile``、``docker-compose*.yml`` 的 ``TZ=${TZ:-Asia/Shanghai}``），
    于是管理员写"9-20 00:00 CST"会落库成 ``2026-09-20T00:00``，而 now 是 UTC——
    权威修正因此**比预期晚 8 小时失效**（``expires_at`` 越大、活得越久）。
    现改为经 ``ensure_utc()`` 统一转 UTC：与 ``user_router`` 的 API Key 过期时间同一套约定
    （无时区输入按 Asia/Shanghai 解释，与时区环境无关）。
    """
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None
    try:
        parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="expires_at must be ISO 8601 datetime") from exc
    return ensure_utc(parsed).replace(tzinfo=None)


@self_evolution_router.post("/corrections")
async def create_correction(payload: dict, db: AsyncSession = Depends(get_db), user: User = Depends(get_required_user)):
    try:
        row = await CorrectionService.create_correction(
            db, user.uid, str(payload.get("thread_id") or ""),
            str(payload.get("original_content") or ""), str(payload.get("proposed_content") or ""),
            payload.get("request_id"), str(payload.get("target_type") or "answer"),
            payload.get("target_id"), payload.get("kb_id"), str(payload.get("risk_level") or "medium"),
        )
    except ValueError as exc: raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"id": row.id, "status": row.status}


@self_evolution_router.post("/corrections/{ticket_id}/review")
async def review_correction(ticket_id: int, payload: dict, db: AsyncSession = Depends(get_db), user: User = Depends(get_admin_user)):
    """审核纠错：approved=false 驳回；true+无 override_content=默认（老师反馈为准）；
    true+override_content=修正（管理员终裁内容替换老师反馈）。
    可选 confidence（默认 0.8）与 expires_at（ISO 时间，默认永不过期）。
    批准后自动生成 entity_hints/intent_tags（尽力而为，失败不阻塞）。

    write_to_graph（默认 true）：批准后异步触发 P4 图谱写回（LLM 改写成图块）。
    写回是异步任务，本接口不等待结果——列表里的 graph_written 才是真实状态，
    失败可用 POST /corrections/{id}/graph 手动重试。
    """
    write_to_graph = payload.get("write_to_graph", True)
    raw_scope = payload.get("scope")
    if raw_scope is not None and raw_scope not in VALID_SCOPES:
        raise HTTPException(status_code=400, detail=f"invalid scope: {raw_scope}")
    try:
        confidence = payload.get("confidence")
        row = await CorrectionService.review_correction(
            db, ticket_id, user.uid, bool(payload.get("approved")),
            payload.get("note"), payload.get("override_content"),
            float(confidence) if confidence is not None else None,
            _parse_expires_at(payload.get("expires_at")),
            payload.get("scope"),
        )
    except ValueError as exc: raise HTTPException(status_code=409, detail=str(exc)) from exc
    final = (row.reviewed_content or "").strip() or row.proposed_content
    enrichment = None
    if row.status == "approved":
        try:
            routing_row = await get_system_kv(db, "model_routing")
            routing = routing_row.value if routing_row and isinstance(routing_row.value, dict) else {}
            enrichment = await enrich_correction(db, row, routing)
        except Exception:
            import logging
            logging.getLogger(__name__).exception("failed to enrich correction %s", ticket_id)
        # 只有知识性偏差允许写回图谱（P4）。个人偏好若也入图，等于把"回答要简短"
        # 这类表达诉求固化进领域知识，且撤回粒度是文件级——污染面远大于注入层。
        if write_to_graph and not row.graph_written and row.scope == SCOPE_KB_TRUTH:
            try:
                await enqueue_correction_graph_writeback(row.id, user.uid)
            except Exception:
                import logging
                logging.getLogger(__name__).exception("failed to enqueue graph writeback %s", ticket_id)
    return {"id": row.id, "status": row.status, "final_content": final,
            "confidence": row.confidence,
            "scope": row.scope,
            "scope_source": row.scope_source,
            "expires_at": _iso_utc(row.expires_at),
            "entity_hints": enrichment.get("entity_hints") if enrichment else (row.entity_hints or []),
            "intent_tags": enrichment.get("intent_tags") if enrichment else (row.intent_tags or []),
            "write_to_graph": bool(write_to_graph) and row.status == "approved"}


@self_evolution_router.get("/corrections/metrics")
async def correction_metrics(
    days: int = Query(7, ge=1, le=90),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_admin_user),
):
    """反馈自进化监控指标（P3 §4.2）：触发率、有害率差值、单条修正胜率。

    有害率为"注入组点踩率 − 未注入组点踩率"，是观察性对比而非因果结论，
    仅供阈值校准与人工判断参考。
    """
    del user
    since, until = resolve_window(days)
    summary = await collect_correction_metrics(db, since=since, until=until)
    tickets = await collect_ticket_win_rates(db, since=since, until=until)
    return {"summary": summary, "tickets": {str(k): v for k, v in tickets.items()}}


@self_evolution_router.get("/corrections")
async def list_corrections(
    status: str | None = Query(None, description="状态过滤，支持逗号分隔多值，如 pending,ready_for_review"),
    needs_review: bool | None = Query(None),
    graph_written: bool | None = Query(None, description="只看已写回图谱 / 未写回图谱的工单"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_admin_user),
):
    """管理员纠错工单列表。前端审批页按 status 分区展示，故支持多值过滤与总数统计。"""
    del user
    query = select(CorrectionTicket)
    statuses = [s.strip() for s in (status or "").split(",") if s.strip()]
    if statuses:
        query = query.where(CorrectionTicket.status.in_(statuses))
    if needs_review is not None:
        query = query.where(CorrectionTicket.needs_review.is_(needs_review))
    if graph_written is not None:
        query = query.where(CorrectionTicket.graph_written.is_(graph_written))
    ordered = query.order_by(CorrectionTicket.created_at.desc())
    total = (await db.execute(select(func.count()).select_from(ordered.subquery()))).scalar_one()
    rows = (await db.execute(ordered.offset(offset).limit(limit))).scalars().all()
    items = [{
        "id": r.id, "uid": r.uid, "status": r.status,
        "original_content": r.original_content, "proposed_content": r.proposed_content,
        "reviewed_content": r.reviewed_content,
        "final_content": (r.reviewed_content or "").strip() or r.proposed_content,
        # M1：作用域与判定来源。scope_source 为空 = 规则未命中特征，UI 应提示确认。
        "scope": r.scope or SCOPE_KB_TRUTH,
        "scope_source": r.scope_source,
        "scope_label": SCOPE_LABELS.get(r.scope or SCOPE_KB_TRUTH, ""),
        "kb_id": r.kb_id, "risk_level": r.risk_level,
        "entity_hints": r.entity_hints or [], "intent_tags": r.intent_tags or [],
        "confidence": r.confidence,
        "expires_at": _iso_utc(r.expires_at),
        "needs_review": bool(r.needs_review),
        "needs_review_reason": r.needs_review_reason,
        "needs_review_at": _iso_utc(r.needs_review_at),
        "reviewer_uid": r.reviewer_uid,
        "review_note": r.review_note,
        "applied_at": _iso_utc(r.applied_at),
        "created_at": _iso_utc(r.created_at),
        "updated_at": _iso_utc(r.updated_at),
        # P4：图谱写回状态
        "graph_written": bool(r.graph_written),
        "graph_file_id": r.graph_file_id,
        "graph_chunk_ids": r.graph_chunk_ids or [],
        "graph_written_at": _iso_utc(r.graph_written_at),
        "retired_at": _iso_utc(r.retired_at),
        "rewrite_text": r.rewrite_text,
    } for r in rows]
    return {"items": items, "total": int(total), "limit": limit, "offset": offset}


@self_evolution_router.post("/corrections/{ticket_id}/review-ack")
async def ack_correction_review(
    ticket_id: int,
    payload: dict,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_admin_user),
):
    """管理员复核完成（文档更新后修正内容仍有效）：清除 needs_review 标记。

    若复核结论是修正已失效，应改用撤回流程而不是本接口。
    """
    try:
        row = await clear_review_flag(db, ticket_id, reviewer_uid=user.uid, note=payload.get("note"))
    except NoResultFound as exc:
        raise HTTPException(status_code=404, detail="ticket not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"id": row.id, "needs_review": bool(row.needs_review),
            "needs_review_reason": row.needs_review_reason}


@self_evolution_router.post("/corrections/{ticket_id}/apply")
async def apply_correction(ticket_id: int, db: AsyncSession = Depends(get_db), user: User = Depends(get_admin_user)):
    try:
        row = await CorrectionService.apply_correction(db, ticket_id, user.uid)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"id": row.id, "status": row.status, "applied_at": _iso_utc(row.applied_at)}


@self_evolution_router.post("/corrections/{ticket_id}/graph")
async def write_correction_graph_endpoint(
    ticket_id: int,
    force_rewrite: bool = False,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_admin_user),
):
    """手动触发（或重试）P4 图谱写回：LLM 改写为图块 → 入图。

    审核通过时已自动入队该任务；若当时失败或选择了"暂不写回"，用本接口补做。
    幂等：已入图的工单返回 status=already。

    ``force_rewrite=true`` 丢弃缓存的改写结果重新改写，用于"改写校验未通过"
    之后的重试——否则管理员点重试只会拿同一段不合格文本再失败一次。
    """
    try:
        result = await write_correction_to_graph(
            db, ticket_id, actor_uid=user.uid, force_rewrite=force_rewrite)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"写回图谱失败：{exc}") from exc
    return result


@self_evolution_router.delete("/corrections/{ticket_id}/graph")
async def withdraw_correction_graph(
    ticket_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_admin_user),
):
    """从 Graph RAG 撤回：删除图块与向量，级联清理只属于它的实体/三元组。

    工单本身保留为准源——撤回后仍可再次写回（重新投影）。
    """
    try:
        result = await withdraw_correction_from_graph(db, ticket_id, actor_uid=user.uid)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"撤回图谱失败：{exc}") from exc
    return result
