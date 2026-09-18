"""纠错工单服务（自进化模块）。

"问题反馈 → 纠错工单 → 管理员审核 → 图谱写回"是本平台的自进化闭环。

它与记忆模块（会话事实 / 长期记忆）**没有任何关系**：记忆是 per-user 的个性化
元数据、只作用于表达层与检索层，且不是安全证据；纠错工单是经管理员核实后被当作
**权威结论**注入的东西，作用域分层（kb_truth / dept_rule / user_pref）、审核流、
图谱撤回粒度都完全独立。

2026-09-17 从记忆模块的 service.py 拆出，现在两边互不 import。
``MemoryEvent`` 只是两者共用的 append-only 审计表（表名 ``memory_events`` 是历史
遗留），要改名得走一次 DB 迁移，不在本次范围。
"""
from __future__ import annotations

import logging
import math
import re
from datetime import datetime, timezone

from sqlalchemy import or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.storage.postgres.event_log import record_event
from yuxi.storage.postgres.models_business import (
    CorrectionTicket,
    MessageFeedback,
    SystemKV,
)

from .scope import (
    SCOPE_DEPT_RULE,
    SCOPE_KB_TRUTH,
    SCOPE_SOURCE_ADMIN,
    classify_scope,
    normalize_scope,
    scope_condition,
    scope_prefix,
)

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# v1.2：按"宁缺毋滥"从 5 收紧到 3（设计文档 §3.3）。
MAX_CORRECTION_CONTEXT = 3
DEFAULT_CORRECTION_CONFIDENCE = 0.8
# P2 双阈值与冲突判定（SystemKV correction_retrieval 可覆盖；v1.2 定稿调高，宁缺毋滥）。
CORRECTION_RETRIEVAL_KV = "correction_retrieval"
DEFAULT_DENSE_THRESHOLD = 0.65
DEFAULT_SPARSE_THRESHOLD = 0.25
DEFAULT_CONFLICT_DENSE_FLOOR = 0.60
# 适用范围重叠到这个程度才可能是"真矛盾"（否则是互补修正），见 §3.3 冲突检测。
CONFLICT_HINT_JACCARD_FLOOR = 0.8
# 候选扫描上限（SystemKV correction_retrieval.scan_limit 可覆盖）。
# 这里只是**失控保护**，不是筛选手段：实体/意图门槛已下推到 SQL，命中集合很小。
# 旧实现是"按 created_at 取最新 500 条再在 Python 侧过滤"——一旦审核通过的工单
# 超过 500 条，更老但更匹配的那条会被静默丢弃（点踩已自动建单，工单量增长很快）。
# 现在触顶会 WARNING，「沉默截断」这个正确性隐患才算真正消掉。
DEFAULT_CORRECTION_SCAN_LIMIT = 2000
_INTENT_LABELS = {
    "graph_rag_query": "知识查询",
    "memory_candidate": "个性化记忆",
    "correction": "纠错",
    "simple_task": "简单任务",
}


def _final_content(row) -> str:
    """终裁内容：管理员修正过则用修正值，否则用老师提交的反馈原文。"""
    reviewed = getattr(row, "reviewed_content", None)
    return (reviewed or "").strip() or (row.proposed_content or "").strip()


MIN_CORRECTION_MATCH_SCORE = 2
_CORRECTION_STOPCHARS = set("的了吗呢吧啊嘛是有在和与能会被对请问怎么什么如何如果可以及还是这个些不没我要对于")


def _match_bigrams(text: str) -> set[str]:
    """中文按字符二元组（bigram）匹配，英文/数字按整词匹配。

    bigram 保证"硫酸"不会匹配"硝酸"、"存放"不会匹配"保存"——
    权威修正层的匹配必须宁缺毋滥，错误注入比漏注入危害更大。
    """
    text = (text or "").lower()
    words = set(re.findall(r"[a-z0-9_]{2,}", text))
    chars = [ch for ch in text if "\u4e00" <= ch <= "\u9fff" and ch not in _CORRECTION_STOPCHARS]
    bigrams = {chars[i] + chars[i + 1] for i in range(len(chars) - 1)}
    return words | bigrams


def _sparse_score(row, qtokens: set[str]) -> tuple[int, float]:
    """词面重叠：返回 (原始重叠数, 归一分 overlap/len(qtokens))。"""
    hay = _match_bigrams(f"{row.original_content} {_final_content(row)}")
    raw = len(qtokens & hay)
    return raw, raw / max(1, len(qtokens))


def _rank_corrections(rows: list, query: str) -> list:
    """Rank correction tickets by bigram/word overlap with the query.

    单个 bigram 的重叠视为噪音丢弃（达到 MIN_CORRECTION_MATCH_SCORE 才算命中）；
    重叠更多优先，同分取更新的工单。
    """
    qtokens = _match_bigrams(query or "")
    if not qtokens:
        return []
    ranked = []
    for row in rows:
        raw, _norm = _sparse_score(row, qtokens)
        if raw >= MIN_CORRECTION_MATCH_SCORE:
            ranked.append((raw, row.created_at or _now(), row))
    ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [row for _, _, row in ranked]


def _cosine(a, b) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na <= 0 or nb <= 0:
        return 0.0
    return dot / (na * nb)


def _composite_score(dense: float | None, sparse: float, confidence) -> float:
    """综合分：稠密 0.6 + 稀疏 0.3 + 置信 0.1；无向量行按稀疏+置信重归一。"""
    conf = float(confidence) if confidence is not None else DEFAULT_CORRECTION_CONFIDENCE
    if dense is None:
        return 0.75 * sparse + 0.25 * conf
    return 0.6 * dense + 0.3 * sparse + 0.1 * conf


def _hint_jaccard(row_a, row_b) -> float:
    """适用范围（实体+意图标签）的 Jaccard 重叠度，用于冲突配对。"""
    a = {str(x) for x in (row_a.entity_hints or [])} | {str(x) for x in (row_a.intent_tags or [])}
    b = {str(x) for x in (row_b.entity_hints or [])} | {str(x) for x in (row_b.intent_tags or [])}
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _find_conflict_pairs(candidates: list, embeddings: dict[int, list], floor: float) -> list[tuple]:
    """适用范围高度重叠（hints Jaccard ≥ 阈值）但终裁内容稠密相似度低于 floor 的候选对。

    任一方无向量时无法可靠判矛盾，跳过该配对（宁缺毋滥只作用于"可判定"的冲突）。
    热路径用它撤下候选（``_detect_conflicts``）；P3 周期任务用它做离线冲突告警。
    """
    pairs: list[tuple] = []
    for i in range(len(candidates)):
        for j in range(i + 1, len(candidates)):
            a, b = candidates[i], candidates[j]
            if _hint_jaccard(a, b) < CONFLICT_HINT_JACCARD_FLOOR:
                continue
            va, vb = embeddings.get(a.id), embeddings.get(b.id)
            if not va or not vb:
                continue
            if _cosine(va, vb) < floor:
                pairs.append((a, b))
    return pairs


def _detect_conflicts(candidates: list, embeddings: dict[int, list], floor: float) -> set[int]:
    """冲突对双双撤下待管理员裁决（本查询不注入，热路径只读不改状态）。

    管理员侧的告警由 P3 周期任务负责（``calibration.scan_conflicts`` →
    ``review_hook.mark_conflicts_needing_review``），热路径只留日志。
    """
    withdrawn: set[int] = set()
    for a, b in _find_conflict_pairs(candidates, embeddings, floor):
        if a.id in withdrawn or b.id in withdrawn:
            continue
        withdrawn.update({a.id, b.id})
        logger.warning(
            "correction conflict: tickets %s/%s share applicability but contents "
            "diverge (dense sim < %.2f); both withheld pending admin review",
            a.id, b.id, floor,
        )
    return withdrawn


async def _embed_query_by_specs(specs: set[str], query: str) -> dict[str, list]:
    """查询侧按候选工单用到的 spec 逐个编码（通常只有 1 个）；失败跳过该 spec。"""
    vectors: dict[str, list] = {}
    for spec in specs:
        try:
            from yuxi.models.embed import select_embedding_model
            model = select_embedding_model(spec)
            encoded = await model.aencode_queries([query[:4000]])
            if encoded and encoded[0]:
                vectors[spec] = [float(x) for x in encoded[0]]
        except Exception:
            logger.exception("failed to embed query with spec %s", spec)
    return vectors


async def _load_correction_config(db: AsyncSession) -> dict:
    """SystemKV correction_retrieval：阈值/冲突底线/向量 spec 覆盖，读失败回默认。"""
    try:
        kv = await db.get(SystemKV, CORRECTION_RETRIEVAL_KV)
        if kv and isinstance(kv.value, dict):
            return kv.value
    except Exception:
        logger.exception("failed to load correction_retrieval config")
    return {}


def _entity_gate(query: str, entity_hints) -> bool:
    """实体重叠门槛（降级式）：修正没绑实体 → 通过（降级）；绑了实体 →
    查询文本必须提及其中至少一个实体名，否则排除。

    这是"查询链接实体 ∩ 反馈 entity_hints ≠ ∅"的词面简化实现：
    实体名直接子串命中查询，等价且不依赖图谱检索调用。
    """
    hints = [h for h in (entity_hints or []) if h and str(h).strip()]
    if not hints:
        return True
    text = (query or "").lower()
    return any(str(h).strip().lower() in text for h in hints)


def _intent_gate(query_intent: str | None, intent_tags) -> bool:
    """意图匹配门槛（降级式）：修正没打意图标签 → 通过；打了标签 →
    查询意图已知且不在标签内才排除（查询意图未知时不排除）。
    """
    tags = [t for t in (intent_tags or []) if t and str(t).strip()]
    if not tags or not query_intent:
        return True
    return query_intent in tags


def _dialect_name(db) -> str:
    """取数据库方言名；取不到（如测试替身/未绑定连接）返回空串 = 不做 SQL 下推。"""
    try:
        bind = db.get_bind()
    except Exception:
        return ""
    return getattr(getattr(bind, "dialect", None), "name", "") or ""


def _jsonb_array_expr(column: str) -> str:
    """把 JSONB 列归一成"一定是数组"的表达式。

    列里理论上只会有数组，但 jsonb_array_elements_text 收到非数组会直接报错；
    用 CASE 兜住可以让下面的 EXISTS 无论 OR 的求值顺序如何都不会炸。
    """
    return f"CASE WHEN jsonb_typeof({column}) = 'array' THEN {column} ELSE '[]'::jsonb END"


def _correction_prefilter_conditions(db, query: str, intent: str | None) -> list:
    """把**实体门槛/意图门槛**下推成 SQL 条件（仅 PostgreSQL）。

    为什么值得下推：这两个门槛是精确谓词（不是启发式打分），下推后候选集从
    "全表最新 N 条"缩到"本次查询确实命中的工单"，扫描上限于是退化为纯保护，
    「老工单超过上限后静默失效」的正确性隐患才真正消失；顺带省掉每轮问答把
    几百行完整 ORM 对象拉进 Python 做 bigram 的开销。

    实现约束：SQL 条件必须是 Python 门槛的**超集**——SQL 绝不允许排除任何
    Python 会放行的行，否则就是换了个地方丢候选。所以：
    - 实体：数组为空 / 全是空白项 → 放行（对应 Python 侧 hints 过滤后为空即放行）
    - 意图：标签为空 或 查询意图未知 → 放行
    非 PostgreSQL 方言直接返回空列表，完全退回 Python 侧判定（行为与下推前一致）。
    """
    if _dialect_name(db) != "postgresql":
        return []
    table = CorrectionTicket.__tablename__
    hints = _jsonb_array_expr(f"{table}.entity_hints")
    tags = _jsonb_array_expr(f"{table}.intent_tags")
    conditions = [
        # 整条必须自成括号单元。text() 条件不会被 SQLAlchemy 自动加括号，
        # 裸 OR 进 AND 链会因优先级被拆成 `(状态 AND 作用域 AND A) OR B`——
        # B 成立即绕过状态/作用域/知识库门槛，属于红线级事故。
        text(
            "(NOT EXISTS (SELECT 1 FROM jsonb_array_elements_text(" + hints + ") AS eh"
            "             WHERE btrim(eh) <> '')"
            " OR EXISTS (SELECT 1 FROM jsonb_array_elements_text(" + hints + ") AS eh"
            "            WHERE btrim(eh) <> ''"
            "              AND strpos(lower(CAST(:corr_query_text AS text)),"
            "                         lower(btrim(eh))) > 0))"
        ).bindparams(corr_query_text=query or ""),
    ]
    if intent:
        # jsonb_exists 是 jsonb ? text 的函数形式：避免 '?' 与驱动参数占位符混淆。
        # 外层括号必须保留：缺了它，整个 WHERE 会因 AND 优先级高于 OR 而变成
        # `(... AND 状态/作用域门槛 AND 标签为空) OR jsonb_exists(...)`——只要意图
        # 标签命中就绕过状态/作用域/知识库门槛，那是红线级事故。
        conditions.append(text(
            "(jsonb_array_length(" + tags + ") = 0"
            " OR jsonb_exists(" + tags + ", CAST(:corr_intent AS text)))"
        ).bindparams(corr_intent=str(intent)))
    return conditions


def _confidence_tier(confidence) -> str:
    value = float(confidence) if confidence is not None else DEFAULT_CORRECTION_CONFIDENCE
    if value >= 0.9:
        return "高"
    if value >= 0.75:
        return "中"
    return "低"


def _scope_label(row) -> str:
    """适用条件：实体 + 意图的可读标签，用于条件化注入格式。"""
    parts = []
    hints = [str(h).strip() for h in (row.entity_hints or []) if str(h).strip()]
    if hints:
        parts.append("、".join(hints[:5]))
    intents = [_INTENT_LABELS.get(str(t), str(t)) for t in (row.intent_tags or []) if str(t).strip()]
    if intents:
        parts.append("意图：" + "、".join(intents[:3]))
    return "，".join(parts)



class CorrectionService:
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
        """追加审计事件。实现在 :mod:`yuxi.storage.postgres.event_log`（与记忆模块共用）。"""
        await record_event(db, uid, target_type, target_id, event_type, before, after,
                          payload, actor_type, actor_id, request_id)

    @staticmethod
    async def create_correction(db: AsyncSession, uid: str, thread_id: str, original: str,
                                proposed: str, request_id: str | None = None,
                                target_type: str = "answer", target_id: str | None = None,
                                kb_id: str | None = None, risk_level: str = "medium",
                                scope: str | None = None, dept_id: int | None = None):
        """建单。``scope`` 为空时按 :func:`scope.classify_scope` 自动判定。

        M1 硬约束：进入全局口径（``kb_truth``）的只能是知识性偏差。偏好特征的
        判定是"绝对优先"的——命中即降为 ``user_pref``，只对该 uid 注入。
        """
        if not original or not original.strip() or not proposed or not proposed.strip():
            raise ValueError("correction content cannot be empty")
        if request_id:
            existing = (await db.execute(select(CorrectionTicket).where(
                CorrectionTicket.uid == uid,
                CorrectionTicket.source_request_id == request_id,
            ))).scalar_one_or_none()
            if existing:
                return existing
        if scope is None:
            auto_scope, scope_source, _conf = classify_scope(proposed, original)
            scope, scope_source = auto_scope, scope_source
        else:
            scope, scope_source = normalize_scope(scope), SCOPE_SOURCE_ADMIN
        row = CorrectionTicket(uid=uid, thread_id=thread_id, source_request_id=request_id,
                               original_content=original[:20000], proposed_content=proposed[:20000],
                               target_type=target_type, target_id=target_id, kb_id=kb_id,
                               scope=scope, scope_source=scope_source,
                               dept_id=dept_id if scope == SCOPE_DEPT_RULE else None,
                               risk_level=risk_level if risk_level in {"low", "medium", "high", "critical"} else "medium")
        db.add(row)
        await db.flush()
        await CorrectionService._record_event(
            db, uid, "correction", row.id, "created", None, "pending",
            {"target_type": row.target_type, "target_id": row.target_id, "kb_id": row.kb_id,
             "risk_level": row.risk_level, "scope": row.scope,
             "scope_source": row.scope_source},
            actor_type="user", actor_id=uid, request_id=request_id,
        )
        await db.commit()
        return row

    @staticmethod
    async def review_correction(db: AsyncSession, ticket_id: int, reviewer_uid: str,
                                approved: bool, note: str | None = None,
                                override_content: str | None = None,
                                confidence: float | None = None,
                                expires_at: datetime | None = None,
                                scope: str | None = None):
        """审核纠错工单。

        - 拒绝（approved=False）：什么都不修改，仅驳回本次反馈。
        - 同意-默认（approved=True，无 override_content）：老师的反馈内容视为正确结果。
        - 同意-修正（approved=True，带 override_content）：管理员终裁内容替换老师的反馈，
          作为后续注入上下文的权威内容。
        - confidence：终裁置信度，缺省 0.8；expires_at：有效期，缺省 None=永不过期
          （仅已知过渡性规则时手填，v1.2 定稿）。
        """
        row = (await db.execute(select(CorrectionTicket).where(CorrectionTicket.id == ticket_id))).scalar_one()
        if row.status not in {"pending", "ready_for_review"}:
            raise ValueError("ticket already reviewed")
        before = row.status
        override = (override_content or "").strip()
        if not approved:
            if override:
                raise ValueError("override_content is only allowed when approving")
            row.status = "rejected"
            payload = {"note": (note or "")[:2000], "mode": "reject"}
        else:
            row.status = "approved"
            row.confidence = max(0.0, min(float(confidence if confidence is not None
                                                else DEFAULT_CORRECTION_CONFIDENCE), 1.0))
            row.expires_at = expires_at
            payload = {"note": (note or "")[:2000], "mode": "default",
                       "confidence": row.confidence}
            if expires_at:
                payload["expires_at"] = expires_at.isoformat()
            if override:
                payload = {"note": (note or "")[:2000], "mode": "override",
                           "teacher_proposed": row.proposed_content[:20000],
                           "admin_final": override[:20000],
                           "confidence": row.confidence}
                if expires_at:
                    payload["expires_at"] = expires_at.isoformat()
                row.reviewed_content = override[:20000]
        # 管理员可改定作用域（M1）：把偏好升级为全局真理是高风险动作，
        # 变更必须写进审计事件，事后能追溯是谁放行的。
        if scope:
            new_scope = normalize_scope(scope, default=row.scope)
            if new_scope != row.scope:
                payload["scope_changed"] = {"from": row.scope, "to": new_scope}
                row.scope = new_scope
                row.scope_source = SCOPE_SOURCE_ADMIN
        row.reviewer_uid, row.review_note = reviewer_uid, (note or "")[:2000]
        await CorrectionService._record_event(db, row.uid, "correction", row.id, "reviewed",
                                          before, row.status, payload,
                                          actor_type="admin", actor_id=reviewer_uid)
        await CorrectionService._sync_feedback_status(db, row, reviewer_uid, note)
        await db.commit()
        return row

    @staticmethod
    async def _sync_feedback_status(db: AsyncSession, ticket, reviewer_uid: str,
                                    note: str | None) -> None:
        """把工单审核结果回写到来源反馈，让两张表说同一件事。

        反馈是收集层（所有人可提），工单是处置层（教师/管理员的反馈才自动进）。
        两者靠 ``message_feedbacks.ticket_id`` 关联；手动创建的工单没有来源反馈，
        跳过即可。同步失败只记日志——不能因为状态同步失败让审核结果丢掉。
        """
        try:
            feedback = (await db.execute(select(MessageFeedback).where(
                MessageFeedback.ticket_id == ticket.id,
            ))).scalar_one_or_none()
            if feedback is None:
                return
            approved = ticket.status == "approved"
            feedback.processing_status = "resolved" if approved else "dismissed"
            feedback.processed_at = _now()
            feedback.processed_by = reviewer_uid
            feedback.processing_note = ((note or "").strip()[:2000]
                                        or ("纠错工单已通过，修正已生效" if approved
                                            else "纠错工单已驳回，原回答维持"))
        except Exception:
            logger.exception("failed to sync feedback status for ticket %s", ticket.id)

    @staticmethod
    async def apply_correction(db: AsyncSession, ticket_id: int, actor_uid: str):
        row = (await db.execute(select(CorrectionTicket).where(
            CorrectionTicket.id == ticket_id,
        ))).scalar_one()
        if row.status != "approved":
            raise ValueError("only approved correction can be marked as applied")
        row.status = "applied"
        row.applied_at = _now()
        await CorrectionService._record_event(
            db, row.uid, "correction", row.id, "applied", "approved", "applied",
            {"target_type": row.target_type, "target_id": row.target_id, "kb_id": row.kb_id},
            actor_type="admin", actor_id=actor_uid,
        )
        await db.commit()
        return row

    @staticmethod
    async def build_correction_context(db: AsyncSession, query: str, kb_ids=None,
                                       intent: str | None = None,
                                       limit: int = MAX_CORRECTION_CONTEXT,
                                       uid: str | None = None,
                                       dept_id: int | None = None) -> str:
        """注入层兼容入口：只要注入文本（P3 之前的调用方沿用此签名）。"""
        text, _injections = await CorrectionService.build_correction_context_details(
            db, query, kb_ids=kb_ids, intent=intent, limit=limit,
            uid=uid, dept_id=dept_id)
        return text

    @staticmethod
    async def build_correction_context_details(db: AsyncSession, query: str, kb_ids=None,
                                               intent: str | None = None,
                                               limit: int = MAX_CORRECTION_CONTEXT,
                                               uid: str | None = None,
                                               dept_id: int | None = None,
                                               ) -> tuple[str, list[dict]]:
        """Approved corrections relevant to the query; injected as authoritative overrides.

        P3：除注入文本外，同时返回本次命中的注入记录（§4.1 埋点），由调用方随回答
        消息落库，供触发率/有害率/胜率统计与阈值校准使用：
        ``[{"ticket_id": int, "score_dense": float|None, "score_sparse": float,
        "score": float}, ...]``。返回为空列表表示未注入。

        Unlike user memory (which must not serve as safety evidence), these entries
        were verified by an admin, so they take precedence over retrieved chunks.

        硬门槛（v4，设计文档 §3.2~3.3，任一不过即排除）：
        - 作用域：工单 kb_id 为空 → 全局生效；有值 → 仅当本次对话可访问该知识库。
        - 状态：approved / applied。
        - 未过期：expires_at 为空或晚于当前时间（空 = 永不过期，v1.2 定稿）。
        - 实体重叠（降级式）：entity_hints 非空时，查询必须提及其中至少一个实体。
        - 意图匹配（降级式）：intent_tags 非空且查询意图已知时，意图须在标签内。
        - 双阈值（P2）：稀疏归一分 ≥ θ_s；有向量的行还须稠密余弦 ≥ θ_d。
          无向量行（生成失败/全局工单无 spec）降级为稀疏路单门槛。
        - 冲突检测（P2）：适用范围 Jaccard ≥ 0.8 且内容稠密相似度低于底线 →
          双双撤下待裁决。

        候选集：作用域/状态/过期 + 实体/意图门槛（PostgreSQL 下推）在 SQL 侧筛出，
        再按 created_at DESC 取至多 ``scan_limit``（默认 2000，SystemKV 可调）。
        该上限是失控保护而非筛选手段；触顶会 WARNING，不再静默丢弃老工单。

        综合分（0.6 稠密 + 0.3 稀疏 + 0.1 置信；无向量行重归一）排序取 Top N
        （默认 3，宁缺毋滥）。阈值存 SystemKV correction_retrieval 可调。
        """
        qtokens = _match_bigrams(query or "")
        if not qtokens:
            return "", []
        cfg = await _load_correction_config(db)
        theta_d = float(cfg.get("dense_threshold", DEFAULT_DENSE_THRESHOLD))
        theta_s = float(cfg.get("sparse_threshold", DEFAULT_SPARSE_THRESHOLD))
        conflict_floor = float(cfg.get("conflict_dense_floor", DEFAULT_CONFLICT_DENSE_FLOOR))

        conditions = [CorrectionTicket.status.in_(("approved", "applied")),
                      or_(CorrectionTicket.expires_at.is_(None),
                          CorrectionTicket.expires_at > _now()),
                      scope_condition(uid=uid, dept_id=dept_id)]
        if kb_ids:
            conditions.append(or_(CorrectionTicket.kb_id.is_(None),
                                  CorrectionTicket.kb_id.in_(kb_ids)))
        else:
            conditions.append(CorrectionTicket.kb_id.is_(None))
        # 实体/意图门槛能下推就下推，让 limit 退化成失控保护而不是筛选手段；
        # Python 侧的门槛仍然执行（下推只是缩小候选，不改变语义）。
        scan_limit = int(cfg.get("scan_limit") or DEFAULT_CORRECTION_SCAN_LIMIT)
        rows = (await db.execute(
            select(CorrectionTicket)
            .where(*conditions, *_correction_prefilter_conditions(db, query, intent))
            .order_by(CorrectionTicket.created_at.desc()).limit(scan_limit))
        ).scalars().all()
        if len(rows) >= scan_limit:
            logger.warning(
                "correction scan hit the safety cap (%s rows) for this query: older "
                "approved corrections may be skipped. Raise correction_retrieval.scan_limit "
                "or add an index on correction_tickets(status, kb_id, created_at)",
                scan_limit)
        gated = [row for row in rows
                 if _entity_gate(query, getattr(row, "entity_hints", None))
                 and _intent_gate(intent, getattr(row, "intent_tags", None))]
        if not gated:
            return "", []

        # 查询向量：只对候选里实际用到的 spec 编码（通常 0 或 1 个）。
        specs = set()
        for row in gated:
            emb = getattr(row, "embedding", None)
            if isinstance(emb, dict) and emb.get("spec") and emb.get("vector"):
                specs.add(str(emb["spec"]))
        query_vectors = await _embed_query_by_specs(specs, query) if specs else {}

        candidates: list = []
        dense_scores: dict[int, float | None] = {}
        sparse_scores: dict[int, float] = {}
        embeddings: dict[int, list] = {}
        for row in gated:
            raw, sparse = _sparse_score(row, qtokens)
            if raw < MIN_CORRECTION_MATCH_SCORE or sparse < theta_s:
                continue
            emb = getattr(row, "embedding", None)
            dense: float | None = None
            if isinstance(emb, dict) and emb.get("spec") and emb.get("vector"):
                qvec = query_vectors.get(str(emb["spec"]))
                if qvec is None:
                    # 该 spec 查询编码失败：无法判稠密，降级稀疏路而不是直接淘汰
                    dense = None
                else:
                    dense = _cosine(qvec, emb["vector"])
                    embeddings[row.id] = list(emb["vector"])
                    if dense < theta_d:
                        continue
            dense_scores[row.id] = dense
            sparse_scores[row.id] = sparse
            candidates.append(row)
        if not candidates:
            return "", []

        withdrawn = _detect_conflicts(candidates, embeddings, conflict_floor)
        scored = []
        for row in candidates:
            if row.id in withdrawn:
                continue
            composite = _composite_score(dense_scores.get(row.id), sparse_scores[row.id],
                                         getattr(row, "confidence", None))
            scored.append((composite, row.created_at or _now(), row))
        scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
        top = scored[:max(1, min(limit, 20))]
        if not top:
            return "", []
        lines = []
        injections: list[dict] = []
        for composite, _created, row in top:
            applies = _scope_label(row)
            cond = f"置信度：{_confidence_tier(getattr(row, 'confidence', None))}"
            if applies:
                cond += f"，适用于：{applies}"
            row_scope = getattr(row, "scope", SCOPE_KB_TRUTH) or SCOPE_KB_TRUTH
            # 偏好/部门规则必须带前缀：模型要能区分"这是事实"与"这是某人的表达习惯"，
            # 否则一条"回答要简短"会被当成结论层依据——那是禁止个性化的红线。
            lines.append(f"- 修正#{row.id}（{cond}）："
                         f"{scope_prefix(row_scope)}{_final_content(row)[:2000]}")
            injections.append({
                "ticket_id": row.id,
                "scope": row_scope,
                "score_dense": None if dense_scores.get(row.id) is None else round(dense_scores[row.id], 4),
                "score_sparse": round(sparse_scores[row.id], 4),
                "score": round(composite, 4),
            })
        text = (
            "以下修正均已由管理员核实批准。回答时若与知识库检索结果冲突，以这些修正为准；"
            "多条修正之间冲突时，优先采信排列在前的修正：\n"
            + "\n".join(lines)
        )
        return text, injections

