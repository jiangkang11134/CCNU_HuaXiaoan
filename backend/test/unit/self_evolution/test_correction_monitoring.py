"""P3 监控与校准的单元测试（设计文档 §4）。

覆盖：指标聚合、阈值决策（冷启动/收紧/放宽/限幅）、confidence EWMA、
文档重摄入触发的复核标记、注入埋点返回结构。
"""
import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

from yuxi.self_evolution.calibration import (
    CONFIDENCE_MIN_SAMPLES,
    MAX_ADJUST_STEP,
    MIN_CALIBRATION_INJECTIONS,
    _ewma,
    decide_confidence_updates,
    decide_thresholds,
    run_correction_calibration,
    scan_conflicts,
)
from yuxi.self_evolution.metrics import summarize_injections, summarize_ticket_win_rates
from yuxi.self_evolution.review_hook import (
    _matched_hints,
    _normalize,
    mark_conflicts_needing_review,
    mark_corrections_needing_review,
)
from yuxi.self_evolution.service import CorrectionService

from _fake_system_kv import fake_execute


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# metrics：触发率 / 有害率 / 胜率
# ---------------------------------------------------------------------------

def test_summarize_injections_computes_trigger_rate_and_harm_delta():
    summary = summarize_injections([(2, "like"), (0, "dislike"), (0, None), (1, "dislike")])
    assert summary["total_answers"] == 4
    assert summary["injected_answers"] == 2
    assert summary["total_injections"] == 3          # 冷启动判定用条次，不是回答数
    assert summary["trigger_rate"] == 0.5
    assert summary["injected"]["dislike_rate"] == 0.5
    assert summary["baseline"]["dislike_rate"] == 1.0
    assert round(summary["harm_rate_delta"], 4) == -0.5


def test_summarize_injections_without_any_rating_has_no_harm_verdict():
    summary = summarize_injections([(1, None), (0, None)])
    assert summary["trigger_rate"] == 0.5
    assert summary["harm_rate_delta"] is None        # 无评价 → 没有结论，而不是 0
    assert summary["baseline"]["dislike_rate"] is None


def test_summarize_injections_handles_empty_window():
    summary = summarize_injections([])
    assert summary["total_answers"] == 0
    assert summary["trigger_rate"] is None
    assert summary["harm_rate_delta"] is None


def test_summarize_ticket_win_rates_counts_injections_per_ticket():
    stats = summarize_ticket_win_rates([(1, "like"), (1, "like"), (1, "dislike"), (2, "dislike")])
    assert stats[1]["injections"] == 3
    assert stats[1]["like"] == 2 and stats[1]["dislike"] == 1
    assert stats[1]["rated"] == 3
    assert stats[1]["win_rate"] == 2 / 3
    assert stats[2]["rated"] == 1 and stats[2]["win_rate"] == 0.0


# ---------------------------------------------------------------------------
# calibration：阈值决策
# ---------------------------------------------------------------------------

def test_decide_thresholds_holds_during_cold_start():
    summary = {"total_injections": MIN_CALIBRATION_INJECTIONS - 1,
               "trigger_rate": 0.9, "harm_rate_delta": 0.9}
    decision = decide_thresholds({"dense_threshold": 0.65, "sparse_threshold": 0.25}, summary)
    assert decision["action"] == "hold"
    assert decision["changed"] is False
    assert decision["dense_threshold"] == 0.65 and decision["sparse_threshold"] == 0.25
    assert "冷启动保护" in decision["reason"]


def test_decide_thresholds_tightens_and_limits_step_to_10_percent():
    summary = {"total_injections": 500, "trigger_rate": 0.30, "harm_rate_delta": 0.0}
    decision = decide_thresholds({"dense_threshold": 0.65, "sparse_threshold": 0.25}, summary)
    assert decision["action"] == "tighten"
    assert decision["dense_threshold"] == round(0.65 * (1 + MAX_ADJUST_STEP), 4)
    assert decision["sparse_threshold"] == round(0.25 * (1 + MAX_ADJUST_STEP), 4)


def test_decide_thresholds_tightens_on_harm_even_with_healthy_trigger_rate():
    summary = {"total_injections": 500, "trigger_rate": 0.08, "harm_rate_delta": 0.12}
    decision = decide_thresholds({"dense_threshold": 0.65, "sparse_threshold": 0.25}, summary)
    assert decision["action"] == "tighten"
    assert "有害率" in decision["reason"]


def test_decide_thresholds_loosens_only_when_trigger_low_and_harm_not_positive():
    low_trigger = {"total_injections": 500, "trigger_rate": 0.01, "harm_rate_delta": -0.05}
    decision = decide_thresholds({"dense_threshold": 0.65, "sparse_threshold": 0.25}, low_trigger)
    assert decision["action"] == "loosen"
    assert decision["dense_threshold"] == round(0.65 * (1 - MAX_ADJUST_STEP), 4)

    # 低触发率但有伤害证据 → 不放宽（宁缺毋滥）
    harmful = {"total_injections": 500, "trigger_rate": 0.01, "harm_rate_delta": 0.20}
    assert decide_thresholds({}, harmful)["action"] == "tighten"
    # 低触发率但基线无评价 → 信号不足，保持不动
    no_baseline = {"total_injections": 500, "trigger_rate": 0.01, "harm_rate_delta": None}
    assert decide_thresholds({}, no_baseline)["action"] == "hold"


def test_decide_thresholds_holds_when_signal_healthy():
    summary = {"total_injections": 500, "trigger_rate": 0.08, "harm_rate_delta": 0.01}
    decision = decide_thresholds({"dense_threshold": 0.65, "sparse_threshold": 0.25}, summary)
    assert decision["action"] == "hold" and decision["changed"] is False


def test_decide_thresholds_stops_at_bounds():
    at_ceiling = {"total_injections": 500, "trigger_rate": 0.30, "harm_rate_delta": 0.0}
    decision = decide_thresholds({"dense_threshold": 0.90, "sparse_threshold": 0.50}, at_ceiling)
    assert decision["action"] == "hold" and decision["changed"] is False
    assert "边界" in decision["reason"]
    assert decision["dense_threshold"] == 0.90 and decision["sparse_threshold"] == 0.50

    at_floor = {"total_injections": 500, "trigger_rate": 0.01, "harm_rate_delta": -0.1}
    lowered = decide_thresholds({"dense_threshold": 0.40, "sparse_threshold": 0.10}, at_floor)
    assert lowered["changed"] is False and lowered["dense_threshold"] == 0.40


# ---------------------------------------------------------------------------
# calibration：单条 confidence 的 EWMA
# ---------------------------------------------------------------------------

def test_ewma_moves_previous_toward_sample():
    assert _ewma(0.8, 1.0) == 0.7 * 0.8 + 0.3 * 1.0
    assert _ewma(0.8, 0.0) == 0.7 * 0.8


def test_decide_confidence_updates_skips_tickets_with_few_ratings():
    tickets = {1: {"rated": CONFIDENCE_MIN_SAMPLES - 1, "win_rate": 0.0},
               2: {"rated": CONFIDENCE_MIN_SAMPLES, "win_rate": 1.0}}
    updates = decide_confidence_updates(tickets, {1: 0.8, 2: 0.8})
    assert 1 not in updates                       # 样本不足不动，防止一次点踩就降权
    assert updates[2] == round(_ewma(0.8, 1.0), 4)


def test_decide_confidence_updates_uses_default_when_ticket_has_no_confidence():
    updates = decide_confidence_updates({7: {"rated": 4, "win_rate": 1.0}}, {})
    assert updates[7] == round(_ewma(0.8, 1.0), 4)


# ---------------------------------------------------------------------------
# review_hook：文档重摄入触发复核
# ---------------------------------------------------------------------------

def test_normalize_and_matched_hints_are_case_insensitive():
    normalized = _normalize(["浓硫酸", " h2so4 ", "", None])
    assert normalized == {"浓硫酸", "h2so4"}
    assert _matched_hints(["浓硫酸", "H2SO4", "乙醇"], normalized) == ["浓硫酸", "H2SO4"]


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
    """只覆盖本轮用到的 session 行为：execute/get/add/commit。"""

    def __init__(self, rows=None, kv=None):
        self.rows = rows or []
        self.kv = kv
        self.added = []
        self.commits = 0

    async def execute(self, query, *_args, **_kwargs):
        return fake_execute(self, query, lambda: _FakeResult(self.rows))

    async def get(self, _model, _key):
        raise AssertionError("SystemKV 不能按主键取：主键是整型 id，必须 select(...).filter(key == ...)")

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.commits += 1


def _ticket_row(ticket_id, hints, kb_id="kb-1", status="approved", vector=None):
    row = SimpleNamespace(id=ticket_id, uid="u-1", kb_id=kb_id, status=status,
                          entity_hints=hints, intent_tags=[], needs_review=False,
                          needs_review_reason=None, needs_review_at=None)
    row.embedding = {"spec": "test-spec", "vector": vector} if vector else None
    return row


def test_mark_corrections_needing_review_flags_only_overlapping_tickets():
    overlap = _ticket_row(1, ["浓硫酸", "乙醇"])
    unrelated = _ticket_row(2, ["通风柜"])
    db = _FakeDB([overlap, unrelated])

    async def run():
        return await mark_corrections_needing_review(db, kb_id="kb-1", entity_names={"浓硫酸"})

    result = asyncio.run(run())
    assert result["marked"] == 1 and result["ticket_ids"] == [1]
    assert overlap.needs_review is True
    assert "浓硫酸" in overlap.needs_review_reason
    assert unrelated.needs_review is False
    assert overlap.needs_review_at is not None
    assert db.commits == 1
    assert len(db.added) == 1                      # 审计事件已追加


def test_mark_corrections_needing_review_is_noop_without_kb_or_entities():
    db = _FakeDB([_ticket_row(1, ["浓硫酸"])])
    assert asyncio.run(mark_corrections_needing_review(db, kb_id="", entity_names={"浓硫酸"}))["marked"] == 0
    assert asyncio.run(mark_corrections_needing_review(db, kb_id="kb-1", entity_names=set()))["marked"] == 0
    assert db.commits == 0


# ---------------------------------------------------------------------------
# calibration：离线冲突扫描（P2 遗留的热路径告警，P3 落地）
# ---------------------------------------------------------------------------

def test_scan_conflicts_pairs_same_kb_overlapping_hints_with_divergent_content():
    overlap_a = _ticket_row(1, ["浓硫酸", "乙醇"], vector=[1.0, 0.0])
    overlap_b = _ticket_row(2, ["浓硫酸", "乙醇"], vector=[0.0, 1.0])   # 适用范围同、内容正交
    other_kb = _ticket_row(3, ["浓硫酸", "乙醇"], kb_id="kb-2", vector=[1.0, 0.0])
    complementary = _ticket_row(4, ["通风柜"], vector=[1.0, 0.0])        # 适用范围不重叠

    pairs = asyncio.run(scan_conflicts(_FakeDB([overlap_a, overlap_b, other_kb, complementary])))
    assert pairs == [(1, 2)]
    assert all(3 not in pair for pair in pairs)     # 跨 KB 不配对（不会同时注入）


def test_scan_conflicts_skips_pairs_without_vectors():
    no_vector = _ticket_row(5, ["浓硫酸", "乙醇"])
    pair_a = _ticket_row(1, ["浓硫酸", "乙醇"], vector=[1.0, 0.0])
    pairs = asyncio.run(scan_conflicts(_FakeDB([no_vector, pair_a])))
    assert pairs == []                              # 无向量 → 无法判分歧，宁缺毋滥


def test_mark_conflicts_needing_review_flags_both_sides_and_is_idempotent():
    a = _ticket_row(1, ["浓硫酸", "乙醇"])
    b = _ticket_row(2, ["浓硫酸", "乙醇"])
    db = _FakeDB([a, b])

    result = asyncio.run(mark_conflicts_needing_review(db, [(1, 2)]))
    assert result["marked"] == 2 and result["ticket_ids"] == [1, 2]
    assert a.needs_review is True and b.needs_review is True
    assert "#2" in a.needs_review_reason and "#1" in b.needs_review_reason

    # 已标记的一侧不重复告警（避免每周重复写审计事件）
    again = asyncio.run(mark_conflicts_needing_review(db, [(1, 2)]))
    assert again["ticket_ids"] == []


# ---------------------------------------------------------------------------
# calibration：校准任务端到端（多段查询按顺序返回）
# ---------------------------------------------------------------------------

class _SequencedDB:
    """按调用顺序返回预设结果，覆盖一次校准任务里的多段查询。"""

    def __init__(self, results, kv=None):
        self._results = list(results)
        self.kv = kv
        self.added = []
        self.commits = 0

    async def execute(self, query, *_args, **_kwargs):
        return fake_execute(self, query, lambda: _FakeResult(self._results.pop(0) if self._results else []))

    async def get(self, _model, _key):
        raise AssertionError("SystemKV 不能按主键取：主键是整型 id，必须 select(...).filter(key == ...)")

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.commits += 1


def test_run_correction_calibration_holds_and_writes_nothing_when_window_is_empty():
    db = _SequencedDB([[], []])
    result = asyncio.run(run_correction_calibration(db=db))
    assert result["decision"]["action"] == "hold"
    assert "冷启动保护" in result["decision"]["reason"]
    assert result["conflict_flags"]["marked"] == 0
    assert db.added == []                       # 无阈值变更 → 不写审计事件


def test_run_correction_calibration_tightens_and_persists_thresholds():
    # 1000 条回答中 250 条注入修正（触发率 25% > 健康上沿）→ 收紧
    messages = [(i, {"injected_corrections": [{"ticket_id": 1}]}) for i in range(250)]
    messages += [(1000 + i, None) for i in range(750)]
    kv = SimpleNamespace(value={"dense_threshold": 0.65, "sparse_threshold": 0.25})
    db = _SequencedDB([messages, [], [], []], kv=kv)

    result = asyncio.run(run_correction_calibration(db=db))

    assert result["decision"]["action"] == "tighten"
    assert kv.value["dense_threshold"] == round(0.65 * (1 + MAX_ADJUST_STEP), 4)
    assert kv.value["sparse_threshold"] == round(0.25 * (1 + MAX_ADJUST_STEP), 4)
    assert kv.value["last_calibrated_at"]
    assert len(db.added) == 1                   # 一条校准审计事件
    assert db.added[0].payload["metrics"]["total_injections"] == 250


# ---------------------------------------------------------------------------
# 注入埋点（P3 §4.1）
# ---------------------------------------------------------------------------

def _ticket(id, original, final, hints=None, tags=None, confidence=0.8, created=None):
    return SimpleNamespace(
        id=id, original_content=original, proposed_content=final, reviewed_content=None,
        entity_hints=hints or [], intent_tags=tags or [], confidence=confidence,
        expires_at=None, created_at=created or _now(),
    )


def test_build_correction_context_details_returns_injection_records(monkeypatch):
    import yuxi.self_evolution.service as svc

    async def fake_embed(specs, query):
        return {}

    monkeypatch.setattr(svc, "_embed_query_by_specs", fake_embed)
    row = _ticket(1, "浓硫酸和乙醇可以混存吗", "强酸与醇类严禁混存，需独立试剂柜", hints=["浓硫酸"])

    async def run():
        return await CorrectionService.build_correction_context_details(
            _FakeDB([row]), "浓硫酸能和乙醇一起存放吗", kb_ids=["kb-1"])

    text, injections = asyncio.run(run())
    assert "修正#1" in text
    assert len(injections) == 1
    record = injections[0]
    assert record["ticket_id"] == 1
    assert record["score_dense"] is None       # 该行无向量 → 稀疏路降级
    assert record["score_sparse"] > 0
    assert record["score"] > 0


def test_correction_context_states_it_outranks_the_knowledge_base(monkeypatch):
    """第一优先级必须写进注入文本，且要有用例钉住。

    用户口径（三级）：管理员批准并重新入库的修正 > 知识库 > 个人记忆与偏好。
    这条目前**只存在于这段文本里**——没有任何结构化机制能保证模型照做：
    检索结果的拼接顺序、各注入段在 prompt 里的位置都在别的模块决定。
    所以文本本身就是唯一载体，改措辞等于改行为，必须拦住。
    """
    import yuxi.self_evolution.service as svc

    async def fake_embed(specs, query):
        return {}

    monkeypatch.setattr(svc, "_embed_query_by_specs", fake_embed)
    row = _ticket(1, "浓硫酸和乙醇可以混存吗", "强酸与醇类严禁混存，需独立试剂柜", hints=["浓硫酸"])

    async def run():
        return await CorrectionService.build_correction_context_details(
            _FakeDB([row]), "浓硫酸能和乙醇一起存放吗", kb_ids=["kb-1"])

    text, _ = asyncio.run(run())
    assert "以下修正均已由管理员核实批准" in text
    assert "与知识库检索结果冲突，以这些修正为准" in text
    # 声明要在具体修正条目之前，否则模型先读到事实、后读到规则
    assert text.index("以这些修正为准") < text.index("修正#1")


def test_build_correction_context_wrapper_keeps_returning_text(monkeypatch):
    import yuxi.self_evolution.service as svc

    async def fake_embed(specs, query):
        return {}

    monkeypatch.setattr(svc, "_embed_query_by_specs", fake_embed)
    row = _ticket(1, "浓硫酸和乙醇可以混存吗", "强酸与醇类严禁混存", hints=["浓硫酸"])

    async def run():
        return await CorrectionService.build_correction_context(
            _FakeDB([row]), "浓硫酸能和乙醇一起存放吗", kb_ids=["kb-1"])

    assert isinstance(asyncio.run(run()), str)
    # 无关键词命中时返回空文本、无埋点
    assert asyncio.run(CorrectionService.build_correction_context(_FakeDB([row]), "")) == ""
