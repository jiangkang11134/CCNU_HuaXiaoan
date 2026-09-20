import asyncio
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from yuxi.services.langfuse_service import submit_user_feedback_score
from yuxi.storage.postgres.models_business import (
    Conversation,
    Message,
    MessageFeedback,
    User,
)
from yuxi.storage.postgres.system_kv import get_system_kv
from yuxi.utils.datetime_utils import format_naive_utc_datetime
from yuxi.utils.logging_config import logger

FEEDBACK_TO_TICKET_KV = "feedback_to_ticket"
_ORIGINAL_CONTENT_LIMIT = 12000

# 反馈是收集层（所有人可提），工单是处置层：只有教师与管理员的反馈自动进入
# P1–P4 自进化管道。学生的反馈停在 message_feedbacks，由管理员在反馈仪表盘
# 判断是否值得转工单——避免"答得太长"这类个人口味被批量送进权威修正。
TICKET_ELIGIBLE_ROLES = frozenset({"faculty", "system_admin"})

# 可被批量任务推入「待人工复核」的状态。backlog 必须在内：反馈是收集层，
# 学生的反馈一进来就是 backlog，若只包含 admin_pending/faculty_pending，
# 所有学生反馈会永远停在初始态，管理员永远看不到它们排队。
BATCH_PROCESSABLE_STATUSES = ("admin_pending", "faculty_pending", "backlog")

# 管理员可手动设置的处置状态全集。
#
# 前四个是引入点/排队态：学生点踩落 backlog，教师落 faculty_pending，管理员落
# admin_pending，批处理再把它们推入 ready_for_review。
# `ticketed` 是"管理员已转成纠错工单、等审核"；工单终审后会被
# `CorrectionService._sync_feedback_status` 改写成 resolved / dismissed。
# 终态只有 resolved / dismissed 两个——其余都还"没处置完"。
FEEDBACK_STATUSES = (
    "backlog",
    "faculty_pending",
    "admin_pending",
    "ready_for_review",
    "ticketed",
    "resolved",
    "dismissed",
)
FEEDBACK_TERMINAL_STATUSES = frozenset({"resolved", "dismissed"})
FEEDBACK_STATUS_LABELS = {
    "backlog": "待处理",
    "faculty_pending": "待教师复核",
    "admin_pending": "待管理员复核",
    "ready_for_review": "已进入复核队列",
    "ticketed": "已转工单",
    "resolved": "已处理",
    "dismissed": "已驳回",
}
_PRIORITY_MIN, _PRIORITY_MAX = 0, 100


async def _feedback_to_ticket_enabled(db: AsyncSession) -> bool:
    """自动建单总开关（SystemKV `{"enabled": false}` 可关）。默认开启。

    建单失败不该影响点踩本身，所以这里读配置失败也按开启处理——真正兜底的是
    调用方的 try/except，而不是把开关关掉。
    """
    try:
        kv = await get_system_kv(db, FEEDBACK_TO_TICKET_KV)
        if kv and isinstance(kv.value, dict) and "enabled" in kv.value:
            return bool(kv.value["enabled"])
    except Exception:
        logger.exception("failed to read %s config", FEEDBACK_TO_TICKET_KV)
    return True


def _extract_kb_id(metadata: dict) -> str | None:
    """从助手消息的证据里取知识库 id；取不到就返回 None（全局工单）。

    全局工单是设计内的降级路径：enrichment 会跳过实体链接、检索走稀疏路，
    管理员审核时再决定是否绑定具体知识库（决策点 1：允许为空=全局）。
    """
    evidence = metadata.get("knowledge_evidence") or metadata.get("sources")
    if isinstance(evidence, list):
        for item in evidence:
            if isinstance(item, dict):
                kb_id = item.get("kb_id") or item.get("knowledge_id")
                if kb_id:
                    return str(kb_id)
    return None


async def _resolve_ticket_kb_id(db: AsyncSession, metadata: dict, conversation) -> str | None:
    """确定工单所属知识库；定不下来就返回 None（全局工单）。

    两条来源，按可靠性排序：

    1. 消息元数据里的检索证据——最准确，但 ``knowledge_evidence`` 目前**全仓没有
       写入方**（只有本模块在读），所以实际总是取不到，留作将来接通。
    2. 该会话所用 Agent 绑定的知识库。唯一绑定时可直接归属；绑定多个时无法判断
       这条修正针对哪个库，宁可留空走全局降级，也不猜一个错的库——
       绑错库会让修正只在那个库里生效，是静默的错配。
    """
    kb_id = _extract_kb_id(metadata)
    if kb_id:
        return kb_id

    slug = str(getattr(conversation, "agent_id", "") or "").strip()
    if not slug:
        return None
    try:
        from yuxi.storage.postgres.models_business import Agent

        config = (await db.execute(
            select(Agent.config_json).where(Agent.slug == slug))).scalar_one_or_none()
        context = (config or {}).get("context") if isinstance(config, dict) else None
        knowledges = context.get("knowledges") if isinstance(context, dict) else None
        if not isinstance(knowledges, list):
            return None
        kb_ids = [str(item).strip() for item in knowledges if str(item or "").strip()]
        if len(kb_ids) == 1:
            return kb_ids[0]
        if len(kb_ids) > 1:
            logger.debug("agent %s binds %s knowledge bases, keep ticket global", slug, len(kb_ids))
    except Exception:
        logger.exception("failed to resolve kb_id for conversation agent %s", slug)
    return None


async def _preceding_user_query(db: AsyncSession, message: Message) -> str:
    """被点踩回答对应的用户提问（同一会话中该消息之前最近的一条 user 消息）。"""
    return (await db.execute(
        select(Message.content)
        .where(Message.conversation_id == message.conversation_id,
               Message.role == "user",
               Message.id < message.id)
        .order_by(Message.id.desc())
        .limit(1)
    )).scalar() or ""


async def create_correction_from_dislike(*, db: AsyncSession, message: Message,
                                         conversation: Conversation, uid: str,
                                         reason: str, reporter_role: str) -> int | None:
    """点踩 → 待审核纠错工单，把 P1–P4 自进化管道的入口接上。

    在此之前 ``create_correction`` 只有管理端手动 POST 一个入口，用户点踩写的
    ``message_feedbacks`` 与 ``correction_tickets`` 完全不打通，管理员必须手工
    重录一遍，闭环等于没接。

    角色门槛：只有教师/管理员（``TICKET_ELIGIBLE_ROLES``）的反馈自动建单。
    学生的反馈同样写进反馈表（收集层），但不进工单（处置层），
    避免个人口味类点踩被当作权威修正喂给全体用户。

    工单停在 pending，不影响任何线上回答；是否采纳由管理员审核决定。
    失败只记日志，绝不回滚点踩本身。
    """
    proposed = (reason or "").strip()
    if not proposed:
        return None
    if reporter_role not in TICKET_ELIGIBLE_ROLES:
        logger.debug("skip auto ticket for non-privileged reporter role %s", reporter_role)
        return None
    if not await _feedback_to_ticket_enabled(db):
        return None
    try:
        from yuxi.self_evolution.service import CorrectionService

        metadata = message.extra_metadata if isinstance(message.extra_metadata, dict) else {}
        query_text = (await _preceding_user_query(db, message)).strip()
        original = f"【用户提问】\n{query_text}\n\n【助手回答】\n{(message.content or '').strip()}"
        row = await CorrectionService.create_correction(
            db,
            uid=str(uid),
            thread_id=str(getattr(conversation, "thread_id", "") or ""),
            original=original[:_ORIGINAL_CONTENT_LIMIT],
            proposed=proposed,
            request_id=str(message.request_id) if message.request_id else None,
            target_type="answer",
            target_id=str(message.id),
            kb_id=await _resolve_ticket_kb_id(db, metadata, conversation),
        )
        logger.info("correction ticket %s auto-created from dislike on message %s (kb_id=%s)",
                    row.id, message.id, getattr(row, "kb_id", None))
        # 预富化 entity_hints：审批页要展示"这条修正会关联到哪些实体"，否则管理员
        # 只能盲批。实体链接不依赖终裁内容，建单时就能算，但是 5 万条实体表的
        # 词面匹配，所以放异步。失败不影响工单本身。
        try:
            from yuxi.self_evolution.tasks import enqueue_correction_preenrich

            await enqueue_correction_preenrich(row.id)
        except Exception:
            logger.exception("failed to enqueue pre-enrichment for ticket %s", row.id)
        return row.id
    except Exception:
        logger.exception("failed to auto-create correction ticket from message %s", message.id)
        return None


async def submit_message_feedback_view(*, message_id: int, rating: str, reason: str | None, db: AsyncSession, current_uid: str) -> dict:
    if rating not in {"like", "dislike"}:
        raise HTTPException(status_code=422, detail="Rating must be 'like' or 'dislike'")
    cleaned_reason = (reason or "").strip()[:5000]
    if rating == "dislike" and not cleaned_reason:
        raise HTTPException(status_code=422, detail="Feedback reason is required")
    try:
        message = (await db.execute(select(Message).filter_by(id=message_id))).scalar_one_or_none()
        if not message:
            raise HTTPException(status_code=404, detail="Message not found")
        conversation = (await db.execute(select(Conversation).filter_by(id=message.conversation_id))).scalar_one_or_none()
        if not conversation or conversation.uid != str(current_uid):
            raise HTTPException(status_code=403, detail="Access denied")
        existing = (await db.execute(select(MessageFeedback).filter_by(message_id=message_id, uid=str(current_uid)))).scalar_one_or_none()
        if existing:
            raise HTTPException(status_code=409, detail="Feedback already submitted for this message")
        user = (await db.execute(select(User).where(User.uid == str(current_uid)))).scalar_one_or_none()
        reporter_role = str(getattr(user, "business_role", None) or "student").lower()
        if getattr(user, "role", None) in {"system_admin", "admin", "superadmin"}:
            reporter_role = "system_admin"
        priority, processing_status = {"system_admin": (100, "admin_pending"), "faculty": (80, "faculty_pending")}.get(reporter_role, (10, "backlog"))
        now = datetime.now(UTC).replace(tzinfo=None)
        scheduled_at = now if reporter_role == "system_admin" else (now + timedelta(days=7) if reporter_role == "faculty" else None)
        row = MessageFeedback(message_id=message_id, uid=str(current_uid), rating=rating, reason=cleaned_reason or None, reporter_role=reporter_role, priority=priority, processing_status=processing_status, scheduled_at=scheduled_at)
        db.add(row)
        await db.commit()
        await db.refresh(row)
        # 点踩自动建工单：这是自进化管道唯一的自动入口，失败不影响点踩本身。
        # 只有教师/管理员的反馈进工单；学生的留在反馈表等管理员人工转单。
        if rating == "dislike":
            ticket_id = await create_correction_from_dislike(
                db=db, message=message, conversation=conversation,
                uid=str(current_uid), reason=cleaned_reason,
                reporter_role=reporter_role,
            )
            if ticket_id:
                row.ticket_id = ticket_id
                row.processing_note = f"已自动生成纠错工单 #{ticket_id}，待管理员审核"
                await db.commit()
        trace_id = (message.extra_metadata or {}).get("langfuse_trace_id")
        if trace_id:
            await asyncio.to_thread(submit_user_feedback_score, trace_id=trace_id, feedback_id=row.id, message_id=row.message_id, conversation_id=message.conversation_id, uid=str(current_uid), rating=rating, reason=cleaned_reason or None)
        return {"id": row.id, "message_id": row.message_id, "rating": row.rating, "reason": row.reason, "created_at": format_naive_utc_datetime(row.created_at)}
    except HTTPException:
        raise
    except Exception as exc:
        await db.rollback()
        logger.exception("Error submitting message feedback: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to submit feedback") from exc


async def get_message_feedback_view(*, message_id: int, db: AsyncSession, current_uid: str) -> dict:
    feedback = (await db.execute(select(MessageFeedback).filter_by(message_id=message_id, uid=str(current_uid)))).scalar_one_or_none()
    if not feedback:
        return {"has_feedback": False, "feedback": None}
    return {"has_feedback": True, "feedback": {"id": feedback.id, "rating": feedback.rating, "reason": feedback.reason, "processing_status": feedback.processing_status, "created_at": format_naive_utc_datetime(feedback.created_at)}}


async def _load_feedback(db: AsyncSession, feedback_id: int) -> MessageFeedback:
    feedback = (await db.execute(
        select(MessageFeedback).filter_by(id=feedback_id))).scalar_one_or_none()
    if not feedback:
        raise HTTPException(status_code=404, detail="Feedback not found")
    return feedback


async def update_feedback_processing(
    *,
    db: AsyncSession,
    feedback_id: int,
    actor_uid: str,
    processing_status: str | None = None,
    priority: int | None = None,
    processing_note: str | None = None,
) -> MessageFeedback:
    """管理员处置一条反馈：改处置状态 / 优先级 / 备注。

    反馈是收集层、工单是处置层。点踩只保证"被记下来"，是否值得进权威修正
    由管理员在这里判断——这是学生反馈唯一的人工出口（学生的点踩不会自动建单）。

    只允许改这三个字段，外加记账：
    - ``processed_by`` 每次写（最近处置人）
    - ``processed_at`` 只在**进入终态**（resolved / dismissed）时写，
      中间态（排队、调优先级）不算"处置完成"

    ``ticket_id`` **不能**通过本函数写——它只能由自动建单或
    :func:`convert_feedback_to_ticket` 写入，否则会改出一个指向不存在工单的 id。
    """
    feedback = await _load_feedback(db, feedback_id)

    if processing_status is not None:
        status = str(processing_status).strip()
        if status not in FEEDBACK_STATUSES:
            raise HTTPException(
                status_code=422,
                detail=f"processing_status must be one of {list(FEEDBACK_STATUSES)}",
            )
        feedback.processing_status = status
        if status in FEEDBACK_TERMINAL_STATUSES:
            feedback.processed_at = datetime.now(UTC).replace(tzinfo=None)

    if priority is not None:
        feedback.priority = max(_PRIORITY_MIN, min(_PRIORITY_MAX, int(priority)))

    if processing_note is not None:
        feedback.processing_note = str(processing_note).strip()[:2000] or None

    feedback.processed_by = str(actor_uid)
    await db.commit()
    return feedback


async def convert_feedback_to_ticket(
    *,
    db: AsyncSession,
    feedback_id: int,
    actor_uid: str,
    scope: str | None = None,
    note: str | None = None,
) -> tuple[MessageFeedback, int]:
    """管理员把一条反馈（通常是学生的）转成待审核纠错工单。

    与自动路径 ``create_correction_from_dislike`` 有三点区别，都是刻意的：

    1. **不走角色门槛**：那个门槛是为了让"所有人的点踩"都只写进收集层，
       而这里是管理员亲自过目后的显式动作。
    2. **不走 ``feedback_to_ticket`` 总开关**：该开关管的是"点踩自动建单"，
       关掉它不该连人工转单一起挡掉。
    3. **允许指定 ``scope``**：自动路径只能靠 ``classify_scope`` 猜；
       管理员看过内容后可直接定稿（``create_correction`` 会把来源记成 ``admin``）。
       但**非法 scope 直接 422，绝不静默回落到 kb_truth**——
       ``normalize_scope`` 的缺省是全局作用域，静默回落等于把偏好放行到全局，
       触碰 M1 红线。

    幂等：已有 ``ticket_id`` 的反馈直接返回原工单，不重复建单
    （同一个点踩被点两次不该产出两张工单）。
    """
    feedback = await _load_feedback(db, feedback_id)
    if feedback.ticket_id:
        return feedback, feedback.ticket_id

    proposed = (feedback.reason or "").strip()
    if not proposed:
        raise HTTPException(
            status_code=422,
            detail="该反馈没有可审核的内容（reason 为空），无法转为纠错工单",
        )

    from yuxi.self_evolution.scope import VALID_SCOPES

    if scope is not None:
        scope = str(scope).strip()
        if scope not in VALID_SCOPES:
            raise HTTPException(
                status_code=422,
                detail=f"scope must be one of {list(VALID_SCOPES)}",
            )

    message = (await db.execute(
        select(Message).filter_by(id=feedback.message_id))).scalar_one_or_none()
    if not message:
        raise HTTPException(status_code=404, detail="Feedback message not found")
    conversation = (await db.execute(
        select(Conversation).filter_by(id=message.conversation_id))).scalar_one_or_none()

    from yuxi.self_evolution.service import CorrectionService

    metadata = message.extra_metadata if isinstance(message.extra_metadata, dict) else {}
    query_text = (await _preceding_user_query(db, message)).strip()
    original = f"【用户提问】\n{query_text}\n\n【助手回答】\n{(message.content or '').strip()}"
    row = await CorrectionService.create_correction(
        db,
        uid=str(feedback.uid),
        thread_id=str(getattr(conversation, "thread_id", "") or ""),
        original=original[:_ORIGINAL_CONTENT_LIMIT],
        proposed=proposed,
        request_id=str(message.request_id) if message.request_id else None,
        target_type="answer",
        target_id=str(message.id),
        kb_id=await _resolve_ticket_kb_id(db, metadata, conversation),
        scope=scope,
    )
    feedback.ticket_id = row.id
    feedback.processing_status = "ticketed"
    feedback.processed_by = str(actor_uid)
    feedback.processing_note = (
        (note or "").strip()[:2000] or f"已由管理员转为纠错工单 #{row.id}，待审核"
    )
    await db.commit()
    # 预富化：审批页靠它显示实体关联。失败不影响工单本身，与自动路径一致。
    try:
        from yuxi.self_evolution.tasks import enqueue_correction_preenrich

        await enqueue_correction_preenrich(row.id)
    except Exception:
        logger.exception("failed to enqueue pre-enrichment for ticket %s", row.id)
    logger.info("correction ticket %s created from feedback %s by %s",
                row.id, feedback.id, actor_uid)
    return feedback, row.id


async def process_feedback_batch(*, limit: int = 1000) -> int:
    from yuxi.storage.postgres.manager import pg_manager
    now = datetime.now(UTC).replace(tzinfo=None)
    async with pg_manager.get_async_session_context() as db:
        # 反馈是收集层，所有人的反馈都要能流转到待复核（backlog 主要来自学生）。
        rows = (await db.execute(select(MessageFeedback).where(MessageFeedback.processing_status.in_(BATCH_PROCESSABLE_STATUSES), (MessageFeedback.scheduled_at.is_(None) | (MessageFeedback.scheduled_at <= now))).order_by(MessageFeedback.priority.desc(), MessageFeedback.created_at.asc()).limit(limit))).scalars().all()
        for row in rows:
            row.processing_status = "ready_for_review"
            row.processing_note = "Queued for human review; no automatic graph mutation"
        await db.commit()
        return len(rows)


def build_feedback_audit_context(*, feedback: MessageFeedback, message: Message, conversation: Conversation | None = None) -> dict:
    """构造审核 LLM 的受控上下文，明确包含用户填写的专业错误原因。"""
    metadata = message.extra_metadata if isinstance(message.extra_metadata, dict) else {}
    return {
        "feedback_id": feedback.id,
        "reporter_role": feedback.reporter_role,
        "priority": feedback.priority,
        "user_error_reason": (feedback.reason or "")[:5000],
        "original_answer": (message.content or "")[:20000],
        "request_id": metadata.get("request_id"),
        "evidence": metadata.get("knowledge_evidence") or metadata.get("sources") or [],
        "conversation_id": getattr(conversation, "id", None),
        "thread_id": getattr(conversation, "thread_id", None),
        "instruction": "仅依据专业反馈和可追溯证据判断，不得把用户记忆当作领域知识，不得直接修改正式知识图谱。",
    }
