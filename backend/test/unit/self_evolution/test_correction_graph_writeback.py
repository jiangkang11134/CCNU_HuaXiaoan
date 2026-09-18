"""P4 图谱写回的单测（设计文档 §5）。

覆盖可在本机测到的部分：纯函数（file_id / 分段 / chunk 构造）与写回、撤回的
守卫逻辑。真正的写回链路（LLM 改写 + Milvus 双写 + Neo4j 抽取）需要完整运行时，
本机不具备，只能在集成环境验证。
"""
import asyncio
from types import SimpleNamespace

import pytest

from yuxi.self_evolution import graph_writeback as gw
from yuxi.self_evolution.graph_writeback import (
    build_chunk_dicts,
    build_rewrite_prompt,
    feedback_file_id,
    split_rewrite_paragraphs,
    validate_rewrite_text,
    withdraw_correction_from_graph,
    write_correction_to_graph,
)


class _FakeResult:
    def __init__(self, rows=None, scalar=None):
        self._rows = rows or []
        self._scalar = scalar

    def scalars(self):
        return self

    def all(self):
        return self._rows

    def scalar_one_or_none(self):
        return self._scalar


class _FakeDB:
    """只覆盖本轮用到的 session 行为。

    写回/撤回都先 `scalar_one_or_none()` 取工单、之后还要查知识库的模型 spec，
    因此按 scalars 序列依次返回，缺省为 None（= KB 未配置模型）。
    """

    def __init__(self, scalars=None, row=None):
        self._scalars = list(scalars or [])
        self.row = row
        self.added = []
        self.commits = 0

    async def execute(self, *_args, **_kwargs):
        value = self._scalars.pop(0) if self._scalars else None
        return _FakeResult(rows=[self.row] if self.row else [], scalar=value)

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.commits += 1


def _ticket(**overrides):
    defaults = dict(
        id=7, uid="u-1", status="approved", kb_id="kb-1",
        original_content="浓硫酸可以和乙醇混存吗", proposed_content="不可以混存",
        reviewed_content="强酸与醇类严禁混存，需独立试剂柜",
        rewrite_text=None, graph_written=False, graph_file_id=None,
        graph_chunk_ids=None, graph_written_at=None, retired_at=None,
        needs_review=False, needs_review_reason=None, needs_review_at=None,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


# ---------------------------------------------------------------------------
# 纯函数
# ---------------------------------------------------------------------------

def test_feedback_file_id_derives_from_ticket_id():
    assert feedback_file_id(7) == "file_fbpool_7"


def test_split_rewrite_paragraphs_keeps_every_semantic_block():
    """4 个语义段全部保留——旧实现会把后 3 段并成一个多主题大杂烩。"""
    text = "第一段\n\n第二段\n\n第三段\n\n第四段"
    parts = split_rewrite_paragraphs(text)
    assert len(parts) == 4
    assert parts[0] == "第一段" and parts[3] == "第四段"


def test_split_rewrite_paragraphs_splits_oversized_block_by_token_budget():
    """超长段按 token 预算再切，与文档块同一尺度，不再产生无上限的巨块。"""
    long_block = "浓硫酸" * 100
    parts = split_rewrite_paragraphs(long_block, chunk_token_num=128)
    assert len(parts) > 1
    assert all(len(p) < len(long_block) for p in parts)


def test_split_rewrite_paragraphs_handles_empty_and_single_block():
    assert split_rewrite_paragraphs("") == []
    assert split_rewrite_paragraphs("   \n\n  ") == []
    assert split_rewrite_paragraphs("只有一段") == ["只有一段"]


def test_build_chunk_dicts_keeps_id_equal_to_chunk_id():
    chunks = build_chunk_dicts("file_fbpool_7", ["第一段", "第二段"])
    assert [c["chunk_index"] for c in chunks] == [0, 1]
    assert all(c["id"] == c["chunk_id"] for c in chunks)
    assert chunks[0]["id"] == "file_fbpool_7_chunk_0"
    assert all(c["file_id"] == "file_fbpool_7" for c in chunks)


def test_build_chunk_dicts_attaches_tags_to_every_chunk():
    """tags 必须逐块落盘：M3 判重/提权要按 chunk 粒度读它，不能只挂在文件上。"""
    chunks = build_chunk_dicts("file_fbpool_7", ["第一段", "第二段"],
                               tags=["feedback_pool", "ticket:7"])
    assert all(c["tags"] == ["feedback_pool", "ticket:7"] for c in chunks)
    # 无 tags 时给空列表而不是 None：knowledge_chunks.tags 是 JSONB 数组语义
    assert build_chunk_dicts("file_fbpool_7", ["第一段"])[0]["tags"] == []


def test_feedback_chunk_tags_carry_provenance_ticket_scope_and_entities():
    row = _ticket(scope="kb_truth", entity_hints=["浓硫酸", " 乙醇 ", ""])
    tags = gw.feedback_chunk_tags(row)
    assert "feedback_pool" in tags
    assert "ticket:7" in tags
    assert "scope:kb_truth" in tags
    assert "entity:浓硫酸" in tags and "entity:乙醇" in tags  # 逐项 strip
    assert "entity:" not in tags                              # 空实体不产标签
    assert len(tags) == len(set(tags))                        # 不重复


def test_feedback_chunk_tags_default_scope_is_kb_truth():
    """写回只放行 kb_truth；工单没落 scope 时按 kb_truth 记，别写出 scope:None。"""
    tags = gw.feedback_chunk_tags(_ticket())
    assert "scope:kb_truth" in tags
    assert not any(t.startswith("scope:none") for t in tags)


# ---------------------------------------------------------------------------
# 改写产物的格式校验：必须是"实打实的知识块"，不是问答记录
# ---------------------------------------------------------------------------

def test_validate_accepts_standalone_knowledge_block():
    ok, reason = validate_rewrite_text(
        "浓硫酸属于强酸，严禁与乙醇等醇类物质混存，须存放于独立的耐腐蚀试剂柜中。")
    assert ok and reason == ""


def test_validate_rejects_leftover_qa_markers():
    """残留「用户问/管理员说」说明模型没把 QA 转成知识条目。"""
    ok, reason = validate_rewrite_text("用户问浓硫酸能否与乙醇混存，答案是不能。")
    assert not ok and "元信息" in reason


def test_validate_rejects_fenced_and_heading_output():
    assert not validate_rewrite_text("```\n浓硫酸严禁与醇类混存\n```")[0]
    assert not validate_rewrite_text("# 存放要求\n\n浓硫酸严禁与醇类混存")[0]


def test_validate_rejects_empty_and_too_short():
    assert not validate_rewrite_text("")[0]
    assert not validate_rewrite_text("   \n\n ")[0]
    assert not validate_rewrite_text("应通风")[0]


def test_build_rewrite_prompt_injects_entity_hints():
    """富化算出的实体必须落到正文——图谱抽取是从 chunk 文本抽实体的。"""
    row = SimpleNamespace(
        original_content="浓硫酸可以和乙醇混存吗",
        entity_hints=["浓硫酸", "乙醇"], intent_tags=["storage"],
    )
    prompt = build_rewrite_prompt(row, "严禁混存")
    assert "浓硫酸、乙醇" in prompt
    assert "必须在正文中显式提到" in prompt
    assert "严禁混存" in prompt


def test_build_rewrite_prompt_omits_hint_line_without_enrichment():
    row = SimpleNamespace(original_content="问", entity_hints=[], intent_tags=None)
    prompt = build_rewrite_prompt(row, "答")
    assert "必须在正文中显式提到" not in prompt


def test_write_marks_ticket_for_review_when_rewrite_is_not_a_knowledge_block(monkeypatch):
    """校验不通过 → 挂待复核，绝不静默入图（撤回粒度是文件级，代价高）。"""
    async def fake_rewrite(_db, _row):
        return "用户问浓硫酸能否与乙醇混存，答案是不能。", None

    monkeypatch.setattr(gw, "rewrite_correction_text", fake_rewrite)

    row = _ticket(rewrite_text=None)  # 无缓存 → 走改写 → 拿回一段不合格文本
    db = _FakeDB(scalars=[row])
    result = asyncio.run(write_correction_to_graph(db, 7))

    assert result["status"] == "skipped"
    assert result["needs_review"] is True
    assert row.needs_review is True
    assert "改写校验未通过" in (row.needs_review_reason or "")
    assert db.commits == 1


def test_write_auto_rewrites_when_cached_text_fails_validation():
    """缓存文本不合格时自动重新改写。

    不能只依赖调用方传 force_rewrite：管理员「复核确认」会用备注覆盖
    needs_review_reason，前端就判断不出该不该强制，重试会拿同一段坏文本再失败一次。
    （force_rewrite=true 的显式路径需要完整运行时才能跑到，本机只覆盖兜底逻辑。）
    """
    bad = "用户问浓硫酸能否与乙醇混存，答案是不能。"
    result = asyncio.run(write_correction_to_graph(_FakeDB(scalars=[_ticket(rewrite_text=bad)]), 7))
    # 缓存被丢弃 → 重新走改写 → 该 KB 未配 llm_model_spec，说明确实重新调用了改写
    assert result["status"] == "skipped" and "llm_model_spec" in result["reason"]


# ---------------------------------------------------------------------------
# 写回的守卫逻辑
# ---------------------------------------------------------------------------

def test_write_rejects_ticket_that_is_not_approved():
    with pytest.raises(ValueError, match="approved"):
        asyncio.run(write_correction_to_graph(_FakeDB(scalars=[_ticket(status="pending")]), 1))


def test_write_rejects_global_ticket_without_kb_id():
    with pytest.raises(ValueError, match="kb_id"):
        asyncio.run(write_correction_to_graph(_FakeDB(scalars=[_ticket(kb_id=None)]), 1))


def test_write_is_idempotent_when_already_written():
    row = _ticket(graph_written=True, graph_file_id="file_fbpool_7")
    result = asyncio.run(write_correction_to_graph(_FakeDB(scalars=[row]), 7))
    assert result["status"] == "already"
    assert result["file_id"] == "file_fbpool_7"


def test_write_skips_when_kb_has_no_llm_spec():
    """KB 未配置 llm_model_spec → 跳过（不写库、不报 500），并给出原因。"""
    result = asyncio.run(write_correction_to_graph(_FakeDB(scalars=[_ticket()]), 7))
    assert result["status"] == "skipped"
    assert "llm_model_spec" in (result.get("reason") or "")


def test_write_skips_when_rewrite_produces_no_paragraphs():
    row = _ticket(rewrite_text="   ")
    result = asyncio.run(write_correction_to_graph(_FakeDB(scalars=[row]), 7))
    assert result["status"] == "skipped"


# ---------------------------------------------------------------------------
# 写回时 chunk 必须带 provenance tags（M3 去重/提权的数据基础，设计文档 §5.4）
# ---------------------------------------------------------------------------

class _FakeKB:
    def __init__(self):
        self.stored = None

    async def _get_milvus_collection(self, _kb_id):
        return object()

    def _get_embedding_function(self, _spec):
        return object()

    async def _embed_and_store_chunks(self, kb_id, file_id, collection, chunks, fn):
        self.stored = {"kb_id": kb_id, "file_id": file_id, "chunks": chunks}


class _FakeKBModule:
    def __init__(self, kb):
        self._kb = kb

    async def aget_kb(self, _kb_id):
        return self._kb


def test_write_attaches_provenance_tags_so_retrieval_can_dedupe(monkeypatch):
    """tags 要一路走到向量库那一步：只挂在 knowledge_files 上是没用的。"""
    import sys
    from types import ModuleType

    kb = _FakeKB()
    file_payloads = []

    class _FakeRepo:
        async def upsert(self, file_id, payload):
            file_payloads.append((file_id, payload))

    class _FakeGraphService:
        async def build_pending_chunks(self, _kb_id, batch_size=0):
            return None

    import yuxi.repositories.knowledge_file_repository as kfr
    monkeypatch.setattr(kfr, "KnowledgeFileRepository", _FakeRepo)

    # 用 sys.modules 桩掉 knowledge 层：本机没有 Milvus/Neo4j 运行时
    fake_kb_module = ModuleType("yuxi.knowledge")
    fake_kb_module.knowledge_base = _FakeKBModule(kb)
    monkeypatch.setitem(sys.modules, "yuxi.knowledge", fake_kb_module)
    fake_graph_module = ModuleType("yuxi.knowledge.graphs.milvus_graph_service")
    fake_graph_module.MilvusGraphService = _FakeGraphService
    monkeypatch.setitem(sys.modules, "yuxi.knowledge.graphs.milvus_graph_service", fake_graph_module)

    row = _ticket(rewrite_text="浓硫酸与乙醇严禁混存，二者必须分柜存放并保持通风良好。",
                  kb_id="kb-1", entity_hints=["浓硫酸"])
    result = asyncio.run(write_correction_to_graph(
        _FakeDB(scalars=[row, "llm-spec", "emb-spec"], row=row), 7))

    assert result["status"] == "written"
    assert file_payloads[0][1]["source_type"] == "feedback_pool"
    tags = kb.stored["chunks"][0]["tags"]
    assert "feedback_pool" in tags and "ticket:7" in tags and "entity:浓硫酸" in tags
    assert all(c["tags"] == tags for c in kb.stored["chunks"])
    # 撤回/重投影的抓手：写入的 chunk 集合与工单记录的 id 一致
    assert row.graph_chunk_ids == [c["chunk_id"] for c in kb.stored["chunks"]]


# ---------------------------------------------------------------------------
# 撤回的守卫逻辑
# ---------------------------------------------------------------------------

def test_withdraw_rejects_ticket_that_was_never_written():
    with pytest.raises(ValueError, match="未写回图谱"):
        asyncio.run(withdraw_correction_from_graph(_FakeDB(scalars=[_ticket()]), 7))
