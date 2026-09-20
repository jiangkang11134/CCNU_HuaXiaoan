"""纠错工单的检索、门控与冲突判定（自进化模块）。

2026-09-17 从 ``test/unit/memory/test_memory_lifecycle.py`` 拆出：纠错工单属于
自进化闭环，与记忆模块没有任何关系，测试也不该放在一起。
"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from yuxi.self_evolution.service import (
    MAX_CORRECTION_CONTEXT,
    CorrectionService,
    _composite_score,
    _confidence_tier,
    _cosine,
    _detect_conflicts,
    _entity_gate,
    _final_content,
    _hint_jaccard,
    _intent_gate,
    _rank_corrections,
    _scope_label,
)

from _fake_system_kv import fake_execute


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def test_correction_ranking_matches_relevant_ticket_only():
    acid = SimpleNamespace(
        id=1, original_content="浓硫酸和乙醇可以混存吗", proposed_content="强酸与醇类必须分开存放",
        created_at=_now(),
    )
    unrelated = SimpleNamespace(
        id=2, original_content="通风柜的检查频率", proposed_content="每月检查通风柜面风速",
        created_at=_now(),
    )
    ranked = _rank_corrections([acid, unrelated], "浓硫酸能和乙醇一起存放吗")
    assert [row.id for row in ranked] == [1]


def test_correction_ranking_prefers_higher_overlap_then_recency():
    older = SimpleNamespace(
        id=1, original_content="浓硫酸 乙醇 混存", proposed_content="必须分开存放",
        created_at=_now() - timedelta(days=1),
    )
    newer = SimpleNamespace(
        id=2, original_content="浓硫酸 乙醇 混存 试剂柜", proposed_content="必须分开存放且用独立试剂柜",
        created_at=_now(),
    )
    ranked = _rank_corrections([older, newer], "浓硫酸和乙醇怎么混存，放哪个试剂柜")
    assert [row.id for row in ranked] == [2, 1]
    assert _rank_corrections([older], "") == []


def test_final_content_prefers_admin_override_over_teacher_proposal():
    overridden = SimpleNamespace(
        id=1, original_content="浓硫酸可以和乙醇混存吗",
        proposed_content="必须分开存放", reviewed_content="强酸与醇类严禁混存，需独立试剂柜",
        created_at=_now(),
    )
    default = SimpleNamespace(
        id=2, original_content="通风柜检查频率", proposed_content="每月检查面风速",
        reviewed_content=None, created_at=_now(),
    )
    assert _final_content(overridden) == "强酸与醇类严禁混存，需独立试剂柜"
    assert _final_content(default) == "每月检查面风速"
    # 空白修正内容视为未修正，回退老师反馈
    blank = SimpleNamespace(id=3, original_content="x", proposed_content="老师内容", reviewed_content="   ")
    assert _final_content(blank) == "老师内容"


def test_correction_matching_rejects_superficial_overlap():
    """单字级别的表面重叠（硝酸 vs 硫酸、共用的"存放"）不算类似问题。"""
    acid_ticket = SimpleNamespace(
        id=1, original_content="浓硫酸和乙醇可以混存吗",
        proposed_content="强酸与醇类必须分开存放", reviewed_content=None,
        created_at=_now(),
    )
    # "硝酸"不匹配"硫酸"；仅共享"存放"一个 bigram，低于阈值
    assert _rank_corrections([acid_ticket], "硝酸的存放要求") == []
    assert _rank_corrections([acid_ticket], "通风柜多久检查一次") == []
    # 表面近似但主题不同也不注入
    assert _rank_corrections([acid_ticket], "硫酸铵怎么配制溶液") == []

# ---------------------------------------------------------------------------
# P1：反馈记忆库 v3 —— 硬门槛 / 条件化注入 / Top3
# ---------------------------------------------------------------------------

def test_correction_context_top3_tightened_from_5():
    assert MAX_CORRECTION_CONTEXT == 3


def test_entity_gate_degrades_when_hints_missing_and_requires_query_mention():
    # 未绑定实体 → 降级通过（不误杀纯流程类修正）
    assert _entity_gate("通风柜多久检查一次", []) is True
    assert _entity_gate("通风柜多久检查一次", None) is True
    # 绑定实体且查询提及 → 通过
    assert _entity_gate("浓硫酸能和乙醇一起存放吗", ["浓硫酸", "乙醇"]) is True
    # 绑定实体但查询只提其他化学品 → 排除（宁缺毋滥）
    assert _entity_gate("硝酸的存放要求是什么", ["浓硫酸", "乙醇"]) is False
    # 大小写不敏感
    assert _entity_gate("how to store H2SO4 safely", ["h2so4"]) is True


def test_intent_gate_degrades_when_tags_or_query_intent_missing():
    assert _intent_gate(None, []) is True
    assert _intent_gate("graph_rag_query", []) is True
    assert _intent_gate(None, ["graph_rag_query"]) is True
    assert _intent_gate("graph_rag_query", ["graph_rag_query"]) is True
    assert _intent_gate("simple_task", ["graph_rag_query"]) is False


def test_confidence_tiers_follow_design_thresholds():
    assert _confidence_tier(None) == "中"       # 默认 0.8
    assert _confidence_tier(0.95) == "高"
    assert _confidence_tier(0.75) == "中"
    assert _confidence_tier(0.5) == "低"


def test_scope_label_renders_entities_and_intent():
    row = SimpleNamespace(entity_hints=["浓硫酸", "乙醇"], intent_tags=["graph_rag_query"])
    assert _scope_label(row) == "浓硫酸、乙醇，意图：知识查询"
    empty = SimpleNamespace(entity_hints=[], intent_tags=[])
    assert _scope_label(empty) == ""


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None


class _FakeDB:
    """仅覆盖 build_correction_context 用到的 execute().scalars().all() 与可选 get()。

    注意：SQL 层过滤（状态/作用域/过期）不在 mock 范围内，这里的用例
    验证的是行级门槛与注入格式。kv 用于 SystemKV correction_retrieval 覆盖测试。
    """

    def __init__(self, rows, kv=None):
        self._rows = rows
        self._kv = kv

    async def execute(self, query, *_args, **_kwargs):
        return fake_execute(self, query, lambda: _FakeResult(self._rows))

    async def get(self, _model, _key):
        raise AssertionError("SystemKV 不能按主键取：主键是整型 id，必须 select(...).filter(key == ...)")


def _ticket(id, original, final, hints=None, tags=None, confidence=0.8, created=None):
    return SimpleNamespace(
        id=id, original_content=original, proposed_content=final, reviewed_content=None,
        entity_hints=hints or [], intent_tags=tags or [], confidence=confidence,
        expires_at=None, created_at=created or _now(),
    )


def test_build_correction_context_gates_and_conditional_format():
    acid = _ticket(1, "浓硫酸和乙醇可以混存吗", "强酸与醇类严禁混存，需独立试剂柜",
                   hints=["浓硫酸", "乙醇"], tags=["graph_rag_query"], confidence=0.95)
    # 实体门拦截：查询完全没提"废液"，绑定了废液实体的修正不注入
    waste = _ticket(2, "废液怎么处理", "废液需中和后回收", hints=["废液"])
    # 意图门拦截：查询意图是 graph_rag_query，修正只对 simple_task 生效
    wrong_intent = _ticket(3, "浓硫酸试剂瓶标签要求", "标签需注明配制日期与责任人",
                           hints=["浓硫酸"], tags=["simple_task"])

    async def run():
        return await CorrectionService.build_correction_context(
            _FakeDB([acid, waste, wrong_intent]),
            "浓硫酸能和乙醇一起存放吗",
            kb_ids=["kb-1"], intent="graph_rag_query",
        )

    text = asyncio.run(run())
    assert "修正#1" in text
    assert "修正#2" not in text
    assert "修正#3" not in text
    assert "置信度：高" in text
    assert "适用于：浓硫酸、乙醇，意图：知识查询" in text


def test_build_correction_context_caps_at_top3():
    rows = [
        _ticket(i, f"化学试剂 {name} 的存放要求", f"{name} 需要专门存放", hints=[name],
                created=_now() - timedelta(minutes=i))
        for i, name in enumerate(["硫酸", "盐酸", "硝酸", "磷酸", "氢氟酸"])
    ]

    async def run():
        return await CorrectionService.build_correction_context(
            _FakeDB(rows), "化学试剂 硫酸 盐酸 硝酸 磷酸 氢氟酸 的存放要求")

    text = asyncio.run(run())
    assert text.count("修正#") == 3  # Top3 收紧（v1.2），5 条相关也只取 3


def test_build_correction_context_entity_gate_blocks_unmentioned_entity():
    """绑定实体的修正，查询完全没提该实体 → 一个都不注入（宁缺毋滥）。"""
    ticket = _ticket(1, "浓硫酸和乙醇可以混存吗", "强酸与醇类严禁混存",
                     hints=["浓硫酸"], tags=[])

    async def run():
        return await CorrectionService.build_correction_context(
            _FakeDB([ticket]), "通风柜多久检查一次", kb_ids=["kb-1"])

    assert asyncio.run(run()) == ""


# ---------------------------------------------------------------------------
# 候选扫描：实体/意图门槛下推 SQL + 失控保护上限（替代原先的静默 limit(500)）
# ---------------------------------------------------------------------------

class _FakeBind:
    def __init__(self, name):
        self.dialect = SimpleNamespace(name=name)


class _DialectDB(_FakeDB):
    """带方言的 _FakeDB，用于校验下推条件的生成（SQL 不真的执行）。"""

    def __init__(self, rows, dialect="postgresql", kv=None):
        super().__init__(rows, kv=kv)
        self._bind = _FakeBind(dialect)

    def get_bind(self):
        return self._bind


def test_prefilter_skips_non_postgres_dialects():
    """非 PostgreSQL（含测试替身）不下推 → 完全退回 Python 判定，语义不变。"""
    from yuxi.self_evolution.service import _correction_prefilter_conditions

    assert _correction_prefilter_conditions(_FakeDB([]), "浓硫酸", "graph_rag_query") == []
    assert _correction_prefilter_conditions(
        _DialectDB([], dialect="sqlite"), "浓硫酸", "graph_rag_query") == []


def _top_level_or_count(expr: str) -> int:
    """统计括号外（深度 0）的 OR 个数。

    下推条件是直接 AND 进 WHERE 的，一旦顶层出现裸 OR，`A AND B OR C` 会因
    AND 优先级更高而变成 `(A AND B) OR C`——C 成立就绕过状态/作用域/知识库门槛。
    这正是 sqlglot 校验时抓到过的问题，用这条断言钉死。
    """
    depth = 0
    count = 0
    i = 0
    while i < len(expr):
        ch = expr[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif depth == 0 and expr[i:i + 2].upper() == "OR":
            before_ok = i == 0 or not (expr[i - 1].isalnum() or expr[i - 1] == "_")
            after_ok = i + 2 >= len(expr) or not (expr[i + 2].isalnum() or expr[i + 2] == "_")
            if before_ok and after_ok:
                count += 1
                i += 2
                continue
        i += 1
    return count


def test_prefilter_pushes_entity_and_intent_gates_into_sql():
    from sqlalchemy import select
    from sqlalchemy.dialects import postgresql

    from yuxi.self_evolution.service import _correction_prefilter_conditions
    from yuxi.storage.postgres.models_business import CorrectionTicket

    conds = _correction_prefilter_conditions(_DialectDB([]), "浓硫酸能和乙醇一起存放吗",
                                             "graph_rag_query")
    assert len(conds) == 2
    sql = str(select(CorrectionTicket).where(*conds).compile(dialect=postgresql.dialect()))
    assert "jsonb_array_elements_text" in sql  # 实体门槛
    assert "strpos" in sql
    assert "jsonb_exists" in sql               # 意图门槛
    # 查询意图未知时不下推意图条件（Python 侧未知意图一律放行）
    assert len(_correction_prefilter_conditions(_DialectDB([]), "浓硫酸", None)) == 1
    # 每条条件必须自成一个括号单元，否则 AND 进 WHERE 会改变语义
    for cond in conds:
        raw = cond.text.strip()
        assert raw.startswith("(") and raw.endswith(")")
        assert _top_level_or_count(raw) == 0


def test_prefilter_casts_json_columns_to_jsonb():
    """entity_hints / intent_tags 在库里是 json 而不是 jsonb。

    PG 的 jsonb_* 系列函数只收 jsonb：直接写 jsonb_typeof(json 列) 会抛
    UndefinedFunctionError，并且会**污染整个事务**——同一次问答里后续所有查询
    都跟着报 InFailedSQLTransactionError，表现为前台"流式处理失败"。
    """
    from sqlalchemy.dialects import postgresql

    from yuxi.self_evolution.service import _jsonb_array_expr

    expr = _jsonb_array_expr("correction_tickets.entity_hints")
    assert "CAST(correction_tickets.entity_hints AS jsonb)" in expr
    # 不能出现裸列直接喂给 jsonb_* 的形态
    assert "jsonb_typeof(correction_tickets.entity_hints)" not in expr
    # 下推后的条件里同样必须带转换
    from sqlalchemy import select

    from yuxi.self_evolution.service import _correction_prefilter_conditions
    from yuxi.storage.postgres.models_business import CorrectionTicket

    conds = _correction_prefilter_conditions(_DialectDB([]), "浓硫酸", "graph_rag_query")
    sql = str(select(CorrectionTicket).where(*conds).compile(dialect=postgresql.dialect()))
    assert "CAST(correction_tickets.entity_hints AS jsonb)" in sql
    assert "CAST(correction_tickets.intent_tags AS jsonb)" in sql


def test_scan_cap_hit_emits_warning(caplog):
    """触顶必须 WARNING——静默截断正是本次修掉的正确性隐患。"""
    import logging

    rows = [_ticket(i, f"浓硫酸存放要求 {i}", f"浓硫酸需独立试剂柜存放 {i}", hints=["浓硫酸"])
            for i in range(2)]
    kv = SimpleNamespace(value={"scan_limit": 2})

    async def run():
        return await CorrectionService.build_correction_context(
            _FakeDB(rows, kv=kv), "浓硫酸怎么存放", kb_ids=["kb-1"])

    with caplog.at_level(logging.WARNING, logger="yuxi.self_evolution.service"):
        asyncio.run(run())
    assert any("safety cap" in record.getMessage() for record in caplog.records)


# ---------------------------------------------------------------------------
# P1：enrichment —— entity_hints / intent_tags 全自动生成
# ---------------------------------------------------------------------------

class _FakeEnrichDB:
    def __init__(self, names, kv=None, kb_spec=None):
        self._names = names
        self._kv = kv
        self._kb_spec = kb_spec
        self.committed = False

    async def execute(self, query, *_args, **_kwargs):
        # kb_spec 设置时所有查询都回它（实体名查询自然匹配不到，无副作用）
        return fake_execute(
            self, query,
            lambda: _FakeResult([self._kb_spec] if self._kb_spec is not None else self._names),
        )

    async def get(self, _model, _key):
        raise AssertionError("SystemKV 不能按主键取：主键是整型 id，必须 select(...).filter(key == ...)")

    async def commit(self):
        self.committed = True


def _enrich_row():
    return SimpleNamespace(
        id=7, kb_id="kb-1",
        original_content="浓硫酸和乙醇可以混存吗", proposed_content="严禁混存",
        reviewed_content=None, entity_hints=[], intent_tags=[],
    )


def test_enrich_correction_links_entities_preferring_longer_names(monkeypatch):
    from yuxi.self_evolution import enrichment
    import yuxi.models.embed as embed_mod

    class _FakeEmbedModel:
        async def aencode(self, texts):
            return [[0.5]]

    def fake_select(_spec):
        return _FakeEmbedModel()

    async def fake_classify(text, routing=None):
        return None  # 分类失败 → 意图留空降级

    monkeypatch.setattr(embed_mod, "select_embedding_model", fake_select)
    monkeypatch.setattr("yuxi.services.intent_classifier.classify_with_api", fake_classify)
    row = _enrich_row()
    db = _FakeEnrichDB(["硫酸", "浓硫酸", "通风柜"])

    result = asyncio.run(enrichment.enrich_correction(db, row))

    # 长名优先命中"浓硫酸"；"硫酸"是其子串被跳过；"通风柜"未出现在工单文本
    assert result["entity_hints"] == ["浓硫酸"]
    assert result["intent_tags"] == []
    assert row.entity_hints == ["浓硫酸"]
    assert db.committed


def test_enrich_correction_drops_non_query_intent_and_low_confidence(monkeypatch):
    from yuxi.self_evolution import enrichment

    async def correction_intent(text, routing=None):
        return {"intent": "correction", "confidence": 0.95}

    monkeypatch.setattr("yuxi.services.intent_classifier.classify_with_api", correction_intent)
    row = _enrich_row()
    result = asyncio.run(enrichment.enrich_correction(_FakeEnrichDB([]), row))
    # "correction" 不是提问意图，丢弃以免工单被锁死在永不命中的意图门槛上
    assert result["intent_tags"] == []

    async def low_confidence(text, routing=None):
        return {"intent": "graph_rag_query", "confidence": 0.5}

    monkeypatch.setattr("yuxi.services.intent_classifier.classify_with_api", low_confidence)
    row = _enrich_row()
    result = asyncio.run(enrichment.enrich_correction(_FakeEnrichDB([]), row))
    assert result["intent_tags"] == []

    async def good(text, routing=None):
        return {"intent": "graph_rag_query", "confidence": 0.9}

    monkeypatch.setattr("yuxi.services.intent_classifier.classify_with_api", good)
    row = _enrich_row()
    result = asyncio.run(enrichment.enrich_correction(_FakeEnrichDB([]), row))
    assert result["intent_tags"] == ["graph_rag_query"]


def test_enrich_correction_global_ticket_skips_entity_linking(monkeypatch):
    """kb_id 为空的全局工单：不做实体链接（无作用域可查），元数据留空降级。"""
    from yuxi.self_evolution import enrichment

    async def fake_classify(text, routing=None):
        return None

    monkeypatch.setattr("yuxi.services.intent_classifier.classify_with_api", fake_classify)
    row = _enrich_row()
    row.kb_id = None
    db = _FakeEnrichDB(["浓硫酸"])

    result = asyncio.run(enrichment.enrich_correction(db, row))
    assert result == {"entity_hints": [], "intent_tags": [], "embedding_spec": None}


def test_enrich_correction_generates_embedding_from_kb_spec(monkeypatch):
    from yuxi.self_evolution import enrichment
    import yuxi.models.embed as embed_mod

    class _FakeEmbedModel:
        async def aencode(self, texts):
            return [[0.1, 0.2, 0.3]]

    def fake_select(_spec):
        return _FakeEmbedModel()

    async def fake_classify(text, routing=None):
        return None

    monkeypatch.setattr(embed_mod, "select_embedding_model", fake_select)
    monkeypatch.setattr("yuxi.services.intent_classifier.classify_with_api", fake_classify)

    row = _enrich_row()
    db = _FakeEnrichDB([], kb_spec="emb/spec-1")
    result = asyncio.run(enrichment.enrich_correction(db, row))

    assert result["embedding_spec"] == "emb/spec-1"
    assert row.embedding == {"spec": "emb/spec-1", "vector": [0.1, 0.2, 0.3]}
    assert db.committed


def test_enrich_correction_embedding_spec_prefers_kv_override(monkeypatch):
    from yuxi.self_evolution import enrichment
    import yuxi.models.embed as embed_mod

    picked = []

    class _FakeEmbedModel:
        async def aencode(self, texts):
            return [[1.0]]

    def fake_select(spec):
        picked.append(spec)
        return _FakeEmbedModel()

    async def fake_classify(text, routing=None):
        return None

    monkeypatch.setattr(embed_mod, "select_embedding_model", fake_select)
    monkeypatch.setattr("yuxi.services.intent_classifier.classify_with_api", fake_classify)

    row = _enrich_row()
    kv = SimpleNamespace(value={"embedding_spec": "ov/spec-9"})
    asyncio.run(enrichment.enrich_correction(_FakeEnrichDB([], kv=kv, kb_spec="kb/spec-1"), row))
    assert picked == ["ov/spec-9"]  # SystemKV 覆盖优先于 KB spec


# ---------------------------------------------------------------------------
# P2：双阈值混合检索 + 冲突检测
# ---------------------------------------------------------------------------

def test_cosine_basics():
    assert _cosine([1.0, 0.0], [1.0, 0.0]) == 1.0
    assert _cosine([1.0, 0.0], [0.0, 1.0]) == 0.0
    assert _cosine([], [1.0]) == 0.0
    assert _cosine([1.0], [1.0, 2.0]) == 0.0  # 维度不一致按 0 处理


def test_composite_score_renormalizes_without_dense():
    with_dense = _composite_score(1.0, 0.5, 0.8)
    assert with_dense == 0.6 * 1.0 + 0.3 * 0.5 + 0.1 * 0.8
    without_dense = _composite_score(None, 0.5, 0.8)
    assert without_dense == 0.75 * 0.5 + 0.25 * 0.8


def test_hint_jaccard():
    a = SimpleNamespace(entity_hints=["浓硫酸"], intent_tags=["graph_rag_query"])
    b = SimpleNamespace(entity_hints=["浓硫酸"], intent_tags=["graph_rag_query"])
    c = SimpleNamespace(entity_hints=["通风柜"], intent_tags=["graph_rag_query"])
    empty = SimpleNamespace(entity_hints=[], intent_tags=[])
    assert _hint_jaccard(a, b) == 1.0
    assert _hint_jaccard(a, c) < 0.8
    assert _hint_jaccard(a, empty) == 0.0


def test_detect_conflicts_withdraws_divergent_pair_but_not_complementary():
    clash_a = SimpleNamespace(id=1, entity_hints=["浓硫酸"], intent_tags=["graph_rag_query"])
    clash_b = SimpleNamespace(id=2, entity_hints=["浓硫酸"], intent_tags=["graph_rag_query"])
    other = SimpleNamespace(id=3, entity_hints=["通风柜"], intent_tags=["graph_rag_query"])
    embeddings = {1: [1.0, 0.0], 2: [0.0, 1.0], 3: [1.0, 0.0]}
    # 适用范围相同但内容向量正交（矛盾嫌疑）→ 双双撤下；#3 范围不同不牵连
    withdrawn = _detect_conflicts([clash_a, clash_b, other], embeddings, floor=0.6)
    assert withdrawn == {1, 2}

    # 任一方无向量 → 无法判定，不撤
    withdrawn = _detect_conflicts([clash_a, clash_b], {1: [1.0, 0.0]}, floor=0.6)
    assert withdrawn == set()


def _ticket_with_embedding(id, original, final, vector, hints=None, tags=None,
                           confidence=0.8, spec="m1"):
    row = _ticket(id, original, final, hints=hints, tags=tags, confidence=confidence)
    row.embedding = {"spec": spec, "vector": vector} if vector else None
    return row


def test_build_correction_context_dense_threshold_blocks_orthogonal_vector(monkeypatch):
    """稀疏路通过但稠密余弦 0 < θ_d=0.65 → 不注入（双阈值 AND 语义）。"""
    import yuxi.self_evolution.service as svc

    async def fake_embed(specs, query):
        return {spec: [1.0, 0.0] for spec in specs}

    monkeypatch.setattr(svc, "_embed_query_by_specs", fake_embed)
    row = _ticket_with_embedding(1, "浓硫酸和乙醇可以混存吗", "强酸与醇类严禁混存",
                                 vector=[0.0, 1.0], hints=["浓硫酸"])

    async def run():
        return await CorrectionService.build_correction_context(
            _FakeDB([row]), "浓硫酸能和乙醇一起存放吗", kb_ids=["kb-1"])

    assert asyncio.run(run()) == ""


def test_build_correction_context_dense_pass_injects_and_ranks(monkeypatch):
    """稠密对齐（cos=1）+ 稀疏达标 → 注入；无向量行靠稀疏路也能进。"""
    import yuxi.self_evolution.service as svc

    async def fake_embed(specs, query):
        return {spec: [1.0, 0.0] for spec in specs}

    monkeypatch.setattr(svc, "_embed_query_by_specs", fake_embed)
    aligned = _ticket_with_embedding(1, "浓硫酸和乙醇可以混存吗", "强酸与醇类严禁混存，需独立试剂柜",
                                     vector=[1.0, 0.0], hints=["浓硫酸"], confidence=0.95)
    plain = _ticket(9, "浓硫酸的存放要求", "浓硫酸需独立试剂柜存放", hints=["浓硫酸"])

    async def run():
        return await CorrectionService.build_correction_context(
            _FakeDB([aligned, plain]), "浓硫酸能和乙醇一起存放吗", kb_ids=["kb-1"])

    text = asyncio.run(run())
    assert "修正#1" in text and "修正#9" in text
    # 稠密对齐行综合分更高，排在前面
    assert text.index("修正#1") < text.index("修正#9")


def test_build_correction_context_sparse_threshold_override(monkeypatch):
    """SystemKV correction_retrieval.sparse_threshold=0.9 → 归一稀疏分不够的行被拦。"""
    import yuxi.self_evolution.service as svc

    async def fake_embed(specs, query):
        return {}

    monkeypatch.setattr(svc, "_embed_query_by_specs", fake_embed)
    row = _ticket(1, "浓硫酸和乙醇可以混存吗", "强酸与醇类严禁混存", hints=["浓硫酸"])
    kv = SimpleNamespace(value={"sparse_threshold": 0.9})

    async def run():
        return await CorrectionService.build_correction_context(
            _FakeDB([row], kv=kv), "浓硫酸能和乙醇一起存放吗", kb_ids=["kb-1"])

    assert asyncio.run(run()) == ""


def test_build_correction_context_query_encode_failure_degrades_to_sparse(monkeypatch):
    """查询向量编码失败 → 有向量的行降级稀疏路，而不是被误杀。"""
    import yuxi.self_evolution.service as svc

    async def fake_embed(specs, query):
        return {}  # 模拟编码失败

    monkeypatch.setattr(svc, "_embed_query_by_specs", fake_embed)
    row = _ticket_with_embedding(1, "浓硫酸和乙醇可以混存吗", "强酸与醇类严禁混存，需独立试剂柜",
                                 vector=[1.0, 0.0], hints=["浓硫酸"])

    async def run():
        return await CorrectionService.build_correction_context(
            _FakeDB([row]), "浓硫酸能和乙醇一起存放吗", kb_ids=["kb-1"])

    assert "修正#1" in asyncio.run(run())
