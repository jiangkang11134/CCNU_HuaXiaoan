"""审核通过后的反馈元数据自动生成（P1 实体/意图，P2 稠密向量）。

设计文档 v1.2 §3.1：entity_hints 与 intent_tags 全自动生成，无需人工编辑。
本模块尽力而为（best-effort）——任何失败都只留下空元数据，对应硬门槛自动
降级，绝不阻塞审核主流程。

- entity_hints：图谱实体"精确/别名匹配"的 DB 词面实现——把工单 KB 的实体名
  逐个与工单文本做子串匹配，命中即绑定。不做开放式 NER。
- intent_tags：classify_with_api 对 original_content 分类（confidence ≥ 0.7
  才采信）。"correction" 不是提问意图，出现时丢弃以免把工单锁死在永不命中的
  意图门槛上。
- embedding（P2）：终裁内容的稠密向量，spec 解析顺序 = SystemKV
  correction_retrieval.embedding_spec > 工单 KB 的 embedding_model_spec > 跳过。
  查询侧用同一 spec 编码查询后算余弦，保证向量空间一致。
"""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

MAX_ENTITY_HINTS = 10          # 单条修正绑定的实体上限，防大而全的模糊适用范围
MIN_ENTITY_NAME_LEN = 2        # 单字实体名（如"酸"）子串噪音过大，不参与链接
INTENT_CONFIDENCE_FLOOR = 0.7  # 与 chat 主链路 classify_with_api 采信线一致
_INJECTABLE_INTENTS = {"graph_rag_query", "memory_candidate", "simple_task"}
CORRECTION_RETRIEVAL_KV = "correction_retrieval"


async def _resolve_embedding_spec(db: AsyncSession, row) -> str | None:
    """向量模型 spec：SystemKV 显式覆盖 > 工单 KB 的 embedding 模型。

    全局工单（kb_id 为空）且未配置覆盖时无向量可用——检索侧自动降级稀疏路。
    """
    try:
        from yuxi.storage.postgres.models_business import SystemKV
        kv = await db.get(SystemKV, CORRECTION_RETRIEVAL_KV)
        if kv and isinstance(kv.value, dict) and kv.value.get("embedding_spec"):
            return str(kv.value["embedding_spec"])
    except Exception:
        logger.exception("failed to read correction_retrieval kv")
    if row.kb_id:
        try:
            from yuxi.storage.postgres.models_knowledge import KnowledgeBase
            spec = (await db.execute(
                select(KnowledgeBase.embedding_model_spec)
                .where(KnowledgeBase.kb_id == row.kb_id))).scalar_one_or_none()
            if spec:
                return str(spec)
        except Exception:
            logger.exception("failed to read kb embedding spec for %s", row.kb_id)
    return None


async def _generate_embedding(db: AsyncSession, row, final_text: str) -> dict | None:
    """终裁内容向量；失败返回 None，检索侧降级稀疏路。"""
    spec = await _resolve_embedding_spec(db, row)
    if not spec or not final_text.strip():
        return None
    try:
        from yuxi.models.embed import select_embedding_model
        model = select_embedding_model(spec)
        vectors = await model.aencode([final_text[:4000]])
        if vectors and vectors[0]:
            return {"spec": spec, "vector": [float(x) for x in vectors[0]]}
    except Exception:
        logger.exception("failed to embed correction %s", row.id)
    return None


async def link_entity_hints(db: AsyncSession, kb_id: str | None, text: str) -> list[str]:
    """图谱实体"精确/别名匹配"的 DB 词面实现——不做开放式 NER。

    只依赖工单文本与 KB 的实体表，**不依赖终裁内容**，所以建单时就能先算一遍，
    让管理员在审批前看到"这条修正会关联到哪些实体"，而不是点了批准才知道。
    """
    if not kb_id or not (text or "").strip():
        return []
    from yuxi.storage.postgres.models_knowledge import KnowledgeGraphEntity

    entity_hints: list[str] = []
    try:
        names = (await db.execute(
            select(KnowledgeGraphEntity.name).where(KnowledgeGraphEntity.kb_id == kb_id)
            .limit(50000))).scalars().all()
        # 长名优先：先命中"浓硫酸"，再考虑"硫酸"，且短名若是已接受
        # 实体的子串则跳过（信息量被长名覆盖），避免 hints 冗余。
        candidates = sorted({(n or "").strip() for n in names}, key=len, reverse=True)
        text_lower = text.lower()
        for name in candidates:
            if len(name) < MIN_ENTITY_NAME_LEN:
                continue
            if name.lower() in text_lower:
                if any(name in accepted for accepted in entity_hints):
                    continue
                entity_hints.append(name)
                if len(entity_hints) >= MAX_ENTITY_HINTS:
                    break
    except Exception:
        logger.exception("failed to link entities for kb %s", kb_id)
    return entity_hints


async def preenrich_entity_hints(db: AsyncSession, row) -> list[str]:
    """建单时的预富化：只填 entity_hints，供审批页在审核前展示。

    intent_tags / embedding 依赖终裁内容，仍留在审核时算。此处只写 entity_hints
    且不覆盖已有值——审核时 `enrich_correction` 会带终裁内容重算并覆盖。
    """
    text = f"{row.original_content or ''}\n{row.proposed_content or ''}"
    hints = await link_entity_hints(db, row.kb_id, text)
    if hints:
        row.entity_hints = hints
        await db.commit()
    return hints


async def enrich_correction(db: AsyncSession, row, routing: dict | None = None) -> dict:
    """审核通过后自动填充 entity_hints / intent_tags / embedding；幂等（覆盖旧值）。"""
    from yuxi.services.intent_classifier import classify_with_api

    text = f"{row.original_content or ''}\n{row.proposed_content or ''}"
    if getattr(row, "reviewed_content", None):
        text += f"\n{row.reviewed_content}"

    # --- entity_hints：DB 词面链接（仅限工单所属 KB；全局工单 kb_id 为空 → 留空降级）---
    entity_hints = await link_entity_hints(db, row.kb_id, text)

    # --- intent_tags：LLM 意图分类（尽力而为）---
    intent_tags: list[str] = []
    try:
        classified = await classify_with_api(text[:2000], routing)
        if classified and float(classified.get("confidence", 0)) >= INTENT_CONFIDENCE_FLOOR:
            intent = classified.get("intent")
            if intent in _INJECTABLE_INTENTS:
                intent_tags = [intent]
    except Exception:
        logger.exception("failed to classify intent for correction %s", row.id)

    # --- embedding（P2）：终裁内容稠密向量 ---
    final_text = (getattr(row, "reviewed_content", None) or "").strip() \
        or (row.proposed_content or "").strip()
    embedding = await _generate_embedding(db, row, final_text)

    row.entity_hints = entity_hints
    row.intent_tags = intent_tags
    row.embedding = embedding
    await db.commit()
    return {"entity_hints": entity_hints, "intent_tags": intent_tags,
            "embedding_spec": (embedding or {}).get("spec")}
