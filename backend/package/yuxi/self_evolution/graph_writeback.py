"""P4：审核通过的反馈写回知识图谱（设计文档 §5）。

设计取舍：**一 ticket 一合成文件**（`file_id = file_fbpool_{ticket_id}`），而不是设计文档
初稿里"每个 KB 一个 feedback_pool"。原因：图谱侧唯一的删除粒度是 file_id
（`KnowledgeGraphRepository.delete_file_references` 只有 file 级，没有单 chunk 级），
一人一文件让撤回 = 一次级联删除，且天然按票隔离；代价只是文件行数等于入图票数。

流程：

1. LLM 把「用户提问 + 管理员终裁的修正内容」改写成 1~3 段**陈述性、规范性**文本
   （去问答语气、保留约束条件与适用实体、不新增原文没有的信息）
2. 建立该 ticket 专属合成文件（`source_type='feedback_pool'`，界面默认隐藏）
3. 图块双写 PostgreSQL(`knowledge_chunks`) + Milvus——**只写 PG 检索不到**，向量必须一起写
4. 复用 `MilvusGraphService.build_pending_chunks` 走现有抽取管线入图（零特殊代码）

撤回 = 按 file_id 级联删除（chunks + 向量 + mentions + 无其他引用的孤儿实体/三元组），
**工单本身保留为准源**，撤回后仍可重新投影。

幂等与失败处理：任何一步失败都回滚已写入的 chunk，保证"要么全写、要么全不写"；
`graph_written` 只在抽取入图成功后置位。所有变更写审计事件。
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.storage.postgres.models_business import CorrectionTicket
from yuxi.storage.postgres.models_knowledge import KnowledgeBase

from .scope import SCOPE_KB_TRUTH

from .service import CorrectionService

logger = logging.getLogger(__name__)

FEEDBACK_FILE_PREFIX = "file_fbpool_"
# 合成文件的 source_type，同时也是 chunk 的 provenance tag（见 feedback_chunk_tags）。
FEEDBACK_FILE_SOURCE_TYPE = "feedback_pool"
# 图块的 token 预算，与文档侧 general preset 的默认值保持一致。
# 反馈块和文档块进的是同一个向量空间，粒度必须对齐，否则检索时长度差异会主导相似度。
REWRITE_CHUNK_TOKEN_NUM = 512
# 安全阀：切分器按 token 预算切，正常改写只会有 1~3 段；这个上限只用来挡住失控输出。
MAX_REWRITE_CHUNKS = 20
REJECT_PREFIX = "改写校验未通过"
MIN_PARAGRAPH_CHARS = 20

REWRITE_PROMPT = """把下面的问答原文与管理员已确认的正确内容，改写成知识库文档中的正文段落。

你输出的是**知识条目本身**——不是问答记录、不是回复、不是批注。读者只会看到你输出的这段文字，看不到任何上下文，因此每一段都必须能独立成立。

要求：
- 用陈述句直接陈述事实与要求，语气如教科书或安全规程
- 每段自包含：首句就点明主体（化学品、设备或规程名称），脱离原问题也能看懂
- 保留全部约束条件、适用范围与否定表述（严禁、不得、必须）
- 不得出现"用户""提问者""管理员""反馈""工单""修正""原回答"这类元信息，也不得出现问句
- 不添加输入里没有的信息，不做推测
- 用空行分段，最多 3 段
- 只输出正文，不要标题、不要 Markdown 代码块标记、不要任何解释"""


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def feedback_file_id(ticket_id: int) -> str:
    """票 → 合成文件 id。撤回粒度与之一致。"""
    return f"{FEEDBACK_FILE_PREFIX}{int(ticket_id)}"


def split_rewrite_paragraphs(text: str, chunk_token_num: int = REWRITE_CHUNK_TOKEN_NUM) -> list[str]:
    """切分改写产物：**复用文档侧同一套切分器**，不再另起炉灶。

    此前是"按空行 split，超过 3 段就把余下全并进第 3 段"——段数失控时会把多个主题
    硬拼成一个超长块，图谱抽取会把这些主题的实体全挂到同一个 chunk 上，PPR 也被带偏。
    现在用 `separator` preset（语义正是"命中分隔符即切，仅超长片段内部按长度再切"），
    delimiter 设为空行，既保留 LLM 的语义段落边界，又拿到和文档块一致的 token 预算。
    """
    # 延迟 import：knowledge 层不进入 memory 模块的导入期（该 parser 本身只依赖纯正则的 nlp）
    from yuxi.knowledge.chunking.ragflow_like.parsers import separator

    if not (text or "").strip():
        return []
    paragraphs = separator.chunk_markdown(text, {
        "delimiter": "\n\n",
        "chunk_token_num": max(int(chunk_token_num or 0), 0),
        "overlapped_percent": 0,
    })
    kept = [p.strip() for p in paragraphs if p and p.strip()]
    if len(kept) <= MAX_REWRITE_CHUNKS:
        return kept
    # 安全阀只在输出失控时生效；正常改写远不到这个量级
    logger.warning("rewrite produced %s chunks, truncating to %s", len(kept), MAX_REWRITE_CHUNKS)
    return kept[:MAX_REWRITE_CHUNKS]


_META_LEAK_PATTERN = re.compile(
    r"【用户提问】|【助手回答】|【问答原文】|用户问|提问者|管理员|工单|反馈|原回答|修正#")
_FENCE_PATTERN = re.compile(r"```|^#{1,6}\s", re.MULTILINE)


def validate_rewrite_text(text: str, *, min_chars: int = MIN_PARAGRAPH_CHARS) -> tuple[bool, str]:
    """校验改写产物是不是"实打实的知识块"。

    LLM 改写是把 QA 形态转成知识条目的关键一步，改歪了（残留问答语气、元信息、
    代码块标记）就会把噪声固化进图谱——而撤回粒度是文件级，代价很高。
    这里不通过就交由管理员复核，绝不静默入图。
    """
    if not (text or "").strip():
        return False, "改写结果为空"
    if _FENCE_PATTERN.search(text):
        return False, "改写结果含 Markdown 代码块标记或标题"
    leaked = _META_LEAK_PATTERN.search(text)
    if leaked:
        return False, f"改写结果仍残留问答元信息（{leaked.group(0)}）"

    paragraphs = split_rewrite_paragraphs(text)
    if not paragraphs:
        return False, "改写结果切分后为空"
    if all(len(p) < min_chars for p in paragraphs):
        return False, f"改写结果过短，每段均不足 {min_chars} 字"
    return True, ""


def build_chunk_dicts(file_id: str, paragraphs: list[str],
                      tags: list[str] | None = None) -> list[dict]:
    """构造 chunk dict：Milvus 主键 id 与 chunk_id 同值，沿用 kb_utils 的命名惯例。

    ``tags`` 会随 chunk 落到 PostgreSQL ``knowledge_chunks.tags``（JSONB）。此前
    该列一直空着，因为只写了 chunk 实体而没带 tags；补齐它是 M3 的数据基础。
    """
    chunk_tags = [str(t) for t in (tags or []) if str(t).strip()]
    chunks = []
    for index, paragraph in enumerate(paragraphs):
        chunk_id = f"{file_id}_chunk_{index}"
        chunks.append({
            "id": chunk_id,          # Milvus 主键
            "chunk_id": chunk_id,
            "file_id": file_id,
            "chunk_index": index,
            "content": paragraph,
            "tags": list(chunk_tags),
        })
    return chunks


def feedback_chunk_tags(row) -> list[str]:
    """反馈改写块的 provenance tags（设计文档 §5.4，M3 去重/提权的数据基础）。

    文档块目前不打 tags，这套约定是给"反馈池"单独立的：

    - ``feedback_pool``：来源标记，与 ``knowledge_files.source_type`` 对齐，
      检索侧据此识别"这块不是原始文档，是反馈派生出来的"
    - ``ticket:<id>``：回指准源工单。同一主题若后来有人工修订的权威修正，
      M3 可据此判重，让人工修正压过反馈派生块（后者可能已过时）
    - ``entity:<name>``：P1 富化的实体线索，便于按实体收敛与冲突排查
    - ``scope:<scope>``：作用域。写回只放行 kb_truth，显式落盘便于审计

    打标签不影响检索（tags 不参与向量/词面打分），只是给后续提权留出抓手。
    """
    tags = [FEEDBACK_FILE_SOURCE_TYPE]
    if getattr(row, "id", None) is not None:
        tags.append(f"ticket:{row.id}")
    tags.append(f"scope:{(getattr(row, 'scope', None) or SCOPE_KB_TRUTH)}")
    for hint in (getattr(row, "entity_hints", None) or []):
        name = str(hint).strip()
        tag = f"entity:{name}"
        if name and tag not in tags:
            tags.append(tag)
    return tags


async def _resolve_llm_spec(db: AsyncSession, kb_id: str) -> str | None:
    """改写用的 LLM：工单所属 KB 的 llm_model_spec。"""
    if not kb_id:
        return None
    return (await db.execute(
        select(KnowledgeBase.llm_model_spec).where(KnowledgeBase.kb_id == kb_id)
    )).scalar_one_or_none()


def build_rewrite_prompt(row, final: str) -> str:
    """组装改写 prompt：把 P1 富化算出的实体线索一并喂给模型。

    图谱抽取是从 chunk 文本里抽实体的——正文里没显式出现实体名就抽不出来，
    PPR 也就连不上原文档的节点。所以富化算出的 entity_hints 必须在这里落到正文。
    """
    hints: list[str] = []
    for attr in ("entity_hints", "intent_tags"):
        for item in (getattr(row, attr, None) or []):
            name = str(item).strip()
            if name and name not in hints:
                hints.append(name)

    prompt = REWRITE_PROMPT
    if hints:
        joined = "、".join(hints[:12])
        prompt += f"\n- 必须在正文中显式提到：{joined}"
    return (f"{prompt}\n\n【问答原文】\n{(row.original_content or '').strip()}\n\n"
            f"【管理员已确认的正确内容】\n{final}")


async def rewrite_correction_text(db: AsyncSession, row) -> tuple[str | None, str | None]:
    """LLM 改写成图块文本。返回 (文本, 跳过原因)；失败抛异常由调用方处理。"""
    final = (getattr(row, "reviewed_content", None) or "").strip() or (row.proposed_content or "").strip()
    prompt = build_rewrite_prompt(row, final)
    spec = await _resolve_llm_spec(db, row.kb_id)
    if not spec:
        return None, "该知识库未配置 llm_model_spec，无法改写"
    # 延迟 import 放在 spec 校验之后：缺模型配置时不必拉起 LLM 依赖链
    from yuxi.models.chat import select_model

    model = select_model(model_spec=spec, timeout=60.0)
    response = await model.call(prompt, stream=False)
    text = (response.content if response else "").strip()
    if not text:
        return None, "LLM 改写返回空内容"
    return text, None


async def write_correction_to_graph(db: AsyncSession, ticket_id: int, *,
                                    actor_uid: str | None = None,
                                    force_rewrite: bool = False) -> dict:
    """把已审核通过的工单改写入图。幂等：已入图的工单直接返回 already。

    ``force_rewrite`` 用于改写校验未通过后的重试：丢弃缓存的 ``rewrite_text``
    让模型重新改写。否则管理员点「写回」只会拿同一段坏文本再校验失败一次，
    形成没有出口的死循环。
    """
    row = (await db.execute(select(CorrectionTicket).where(
        CorrectionTicket.id == ticket_id))).scalar_one_or_none()
    if row is None:
        raise ValueError("ticket not found")
    if row.status not in ("approved", "applied"):
        raise ValueError("only approved correction can be written to graph")
    # M1：个人偏好与部门规则不得入图。图谱是领域知识的准源，把"回答要简短"
    # 这类表达诉求固化进去，撤回粒度还是文件级，污染面远大于注入层。
    # 守卫放在这里而不是 router，手动重试接口（POST /corrections/{id}/graph）
    # 与任务队列两条路径就都绕不过去。
    if (getattr(row, "scope", None) or SCOPE_KB_TRUTH) != SCOPE_KB_TRUTH:
        raise ValueError(
            f"只有知识性偏差（{SCOPE_KB_TRUTH}）可写回图谱，当前作用域为 {row.scope}")
    if not row.kb_id:
        raise ValueError("工单未绑定知识库（kb_id 为空），无法写回图谱")
    if row.graph_written:
        return {"status": "already", "ticket_id": ticket_id, "file_id": row.graph_file_id}

    text = "" if force_rewrite else (row.rewrite_text or "").strip()
    if text:
        # 缓存文本已不合格则直接重新改写。不能只靠调用方传 force_rewrite：管理员
        # 「复核确认」会用备注覆盖 needs_review_reason，前端再也判断不出该不该强制，
        # 于是重试永远拿同一段坏文本再失败一次。这里做兜底，杜绝死循环。
        cached_ok, cached_reason = validate_rewrite_text(text)
        if not cached_ok:
            logger.info("cached rewrite invalid for correction %s, re-rewriting: %s",
                        ticket_id, cached_reason)
            text = ""
    if not text:
        text, reason = await rewrite_correction_text(db, row)
        if not text:
            return {"status": "skipped", "ticket_id": ticket_id, "reason": reason}
        row.rewrite_text = text

    ok, reason = validate_rewrite_text(text)
    if not ok:
        # 改写产物不是合格的知识块 → 挂待复核交管理员处理，绝不静默入图。
        # 撤回粒度是文件级，把"用户问/管理员说"这类噪声固化进图谱的代价，
        # 远高于让管理员在审批页看一眼。
        row.needs_review = True
        row.needs_review_reason = f"{REJECT_PREFIX}：{reason}"
        row.needs_review_at = _now()
        await db.commit()
        logger.warning("correction %s rewrite rejected: %s", ticket_id, reason)
        return {"status": "skipped", "ticket_id": ticket_id, "reason": reason,
                "needs_review": True}

    paragraphs = split_rewrite_paragraphs(text)
    if not paragraphs:
        return {"status": "skipped", "ticket_id": ticket_id, "reason": "改写结果为空"}

    # 延迟 import：knowledge 层依赖 Milvus/Neo4j/LLM，不能进入 memory 模块的导入期
    from yuxi.knowledge import knowledge_base
    from yuxi.knowledge.graphs.milvus_graph_service import MilvusGraphService
    from yuxi.repositories.knowledge_file_repository import KnowledgeFileRepository

    file_id = feedback_file_id(ticket_id)
    chunks = build_chunk_dicts(file_id, paragraphs, tags=feedback_chunk_tags(row))
    await KnowledgeFileRepository().upsert(file_id, {
        "kb_id": row.kb_id,
        "filename": f"反馈修正#{ticket_id}.md",
        "original_filename": f"反馈修正#{ticket_id}.md",
        "file_type": "md",
        "status": "indexed",
        "content_type": "system",
        "source_type": FEEDBACK_FILE_SOURCE_TYPE,
        "chunk_count": len(chunks),
    })

    kb = await knowledge_base.aget_kb(row.kb_id)
    collection = await kb._get_milvus_collection(row.kb_id)
    embedding_spec = (await db.execute(
        select(KnowledgeBase.embedding_model_spec).where(
            KnowledgeBase.kb_id == row.kb_id))).scalar_one_or_none()
    if not embedding_spec:
        return {"status": "skipped", "ticket_id": ticket_id,
                "reason": "该知识库未配置 embedding_model_spec，无法写入向量"}
    embedding_function = kb._get_embedding_function(embedding_spec)
    await kb._embed_and_store_chunks(row.kb_id, file_id, collection, chunks, embedding_function)

    try:
        await MilvusGraphService().build_pending_chunks(row.kb_id, batch_size=len(chunks))
    except Exception:
        # 抽取入图失败 → 回滚已写入的 chunk，保证幂等（下次可整体重试）
        logger.exception("graph build failed for correction %s, rolling back chunks", ticket_id)
        await kb.delete_file_chunks_only(row.kb_id, file_id)
        raise

    now = _now()
    row.graph_written = True
    row.graph_file_id = file_id
    row.graph_chunk_ids = [c["chunk_id"] for c in chunks]
    row.graph_written_at = now
    row.retired_at = None
    # 入图成功即清掉"改写校验未通过"的待复核标记；其他来源的复核标记（文档重摄入）不动。
    if (row.needs_review_reason or "").startswith(REJECT_PREFIX):
        row.needs_review = False
        row.needs_review_reason = None
        row.needs_review_at = None
    await CorrectionService._record_event(
        db, row.uid, "correction", row.id, "graph_written", None, row.status,
        {"file_id": file_id, "chunk_ids": row.graph_chunk_ids, "kb_id": row.kb_id},
        actor_type="admin" if actor_uid else "system", actor_id=actor_uid,
    )
    await db.commit()
    logger.info("correction %s written to graph (file_id=%s, chunks=%s)", ticket_id, file_id, len(chunks))
    return {"status": "written", "ticket_id": ticket_id, "file_id": file_id,
            "chunk_ids": row.graph_chunk_ids}


async def withdraw_correction_from_graph(db: AsyncSession, ticket_id: int, *,
                                         actor_uid: str | None = None) -> dict:
    """从 Graph RAG 撤回：删图块 + 向量 + mentions + 孤儿实体/三元组；工单保留为准源。"""
    row = (await db.execute(select(CorrectionTicket).where(
        CorrectionTicket.id == ticket_id))).scalar_one_or_none()
    if row is None:
        raise ValueError("ticket not found")
    if not row.graph_written:
        raise ValueError("该工单未写回图谱")
    if not row.kb_id:
        raise ValueError("工单未绑定知识库，无法撤回")

    from yuxi.knowledge import knowledge_base

    file_id = row.graph_file_id or feedback_file_id(ticket_id)
    kb = await knowledge_base.aget_kb(row.kb_id)
    await kb.delete_file_chunks_only(row.kb_id, file_id)

    row.graph_written = False
    row.retired_at = _now()
    await CorrectionService._record_event(
        db, row.uid, "correction", row.id, "graph_withdrawn", row.status, row.status,
        {"file_id": file_id, "chunk_ids": row.graph_chunk_ids or [], "kb_id": row.kb_id},
        actor_type="admin" if actor_uid else "system", actor_id=actor_uid,
    )
    await db.commit()
    logger.info("correction %s withdrawn from graph (file_id=%s)", ticket_id, file_id)
    return {"status": "withdrawn", "ticket_id": ticket_id, "file_id": file_id,
            "retired_at": row.retired_at.isoformat()}
