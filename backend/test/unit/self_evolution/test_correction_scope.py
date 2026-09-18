"""M1 作用域分层：只有知识性偏差允许进入全局口径。

硬约束（用户 2026-09-16）：**进入全局口径的，只能是真正知识性的偏差，
而不能是偏好。** 判定遵循"偏好绝对优先"——误判为 user_pref 只影响提反馈的
人自己，误判为 kb_truth 会污染全体用户，两者不对称。
"""
from types import SimpleNamespace

import pytest

from yuxi.self_evolution.scope import (
    SCOPE_DEPT_RULE,
    SCOPE_KB_TRUTH,
    SCOPE_SOURCE_ADMIN,
    SCOPE_SOURCE_RULE,
    SCOPE_USER_PREF,
    classify_scope,
    normalize_scope,
    scope_condition,
    scope_prefix,
)
from yuxi.self_evolution.service import CorrectionService


class _SeqResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value

    def scalar_one(self):
        if self.value is None:
            raise RuntimeError("no row")
        return self.value

    def scalars(self):
        return self

    def all(self):
        return self.value if isinstance(self.value, list) else []


class _SeqDB:
    """按调用顺序喂查询结果的极简 AsyncSession 替身。"""

    def __init__(self, results):
        self._results = list(results)
        self.added: list = []
        self._next_id = 1

    async def execute(self, *_args, **_kwargs):
        return _SeqResult(self._results.pop(0) if self._results else None)

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        for obj in self.added:
            if getattr(obj, "id", None) is None:
                obj.id = self._next_id
                self._next_id += 1

    async def commit(self):
        pass


# --------------------------------------------------------------------------
# 分类器
# --------------------------------------------------------------------------

def test_preference_is_downgraded_to_user_scope():
    """表达偏好绝不进全局——这是本模块存在的唯一理由。"""
    scope, source, conf = classify_scope("回答太长了，简短一点")
    assert (scope, source) == (SCOPE_USER_PREF, SCOPE_SOURCE_RULE)
    assert conf >= 0.8


@pytest.mark.parametrize("text", [
    "能不能用表格列出来",
    "说重点就行，不用展开",
    "我更喜欢分点的形式",
    "以后别写这么详细",
    "通俗一点，太专业了看不懂",
])
def test_various_preference_phrasings(text):
    assert classify_scope(text)[0] == SCOPE_USER_PREF


def test_knowledge_error_stays_global():
    scope, source, _ = classify_scope("废液不能直接倒入下水道，应该分类收集")
    assert (scope, source) == (SCOPE_KB_TRUTH, SCOPE_SOURCE_RULE)


@pytest.mark.parametrize("text", [
    "这个说法错了，浓硫酸应存放于阴凉通风处",
    "不准确，按照 GB 规定应单独存放",
    "丙酮和硝酸存在配伍禁忌，写反了",
])
def test_various_knowledge_phrasings(text):
    assert classify_scope(text)[0] == SCOPE_KB_TRUTH


def test_mixed_text_falls_back_to_preference():
    """"知识错误 + 太长"混合时按偏好处理：偏好误判无害，真理误判有害。

    宁可让管理员人工拆分，也不自动把含个人口味的内容放行到全局。
    """
    assert classify_scope("废液处置说错了，而且回答太长了")[0] == SCOPE_USER_PREF


def test_undecided_marks_source_as_none():
    """未命中任何特征时 source 为 None，UI 必须提示管理员确认，不能静默放行。"""
    scope, source, conf = classify_scope("这个说得不太好")
    assert scope == SCOPE_KB_TRUTH
    assert source is None
    assert conf == 0.0


def test_empty_text_defaults_to_global_without_source():
    assert classify_scope("", "")[:2] == (SCOPE_KB_TRUTH, None)


def test_original_side_also_triggers_preference():
    """偏好特征出现在被质疑的原文里也算命中，避免漏判。"""
    assert classify_scope("同意", "这段回答太长了")[0] == SCOPE_USER_PREF


def test_normalize_scope_rejects_unknown_values():
    assert normalize_scope("kb_truth") == SCOPE_KB_TRUTH
    assert normalize_scope("nonsense") == SCOPE_KB_TRUTH
    assert normalize_scope(None, default=SCOPE_USER_PREF) == SCOPE_USER_PREF


def test_scope_prefix_marks_non_truth_scopes():
    """偏好注入时必须带前缀，模型才能区分"事实"与"某人的表达习惯"。"""
    assert scope_prefix(SCOPE_USER_PREF) == "[表达偏好] "
    assert scope_prefix(SCOPE_DEPT_RULE) == "[本部门规则] "
    assert scope_prefix(SCOPE_KB_TRUTH) == ""


# --------------------------------------------------------------------------
# 注入门槛
# --------------------------------------------------------------------------

def _compile(cond) -> str:
    from sqlalchemy.dialects import postgresql

    return str(cond.compile(dialect=postgresql.dialect(),
                            compile_kwargs={"literal_binds": True}))


def test_scope_condition_always_allows_truth():
    assert "kb_truth" in _compile(scope_condition(uid=None))


def test_scope_condition_hides_preference_when_uid_missing():
    """uid 为空（匿名链路）时偏好完全不注入——宁可不注入也不泄漏。"""
    assert "user_pref" not in _compile(scope_condition(uid=None))


def test_scope_condition_allows_own_preference():
    compiled = _compile(scope_condition(uid="u1"))
    assert "user_pref" in compiled
    assert "u1" in compiled


def test_scope_condition_dept_requires_dept_id():
    compiled = _compile(scope_condition(uid="u1", dept_id=7))
    assert "dept_rule" in compiled
    assert "7" in compiled


# --------------------------------------------------------------------------
# 建单与审核
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_correction_auto_classifies_preference():
    db = _SeqDB([])
    row = await CorrectionService.create_correction(
        db, "u1", "t1", "浓硫酸存放要求原文", "回答太长了，简短一点")
    assert row.scope == SCOPE_USER_PREF
    assert row.scope_source == SCOPE_SOURCE_RULE


@pytest.mark.asyncio
async def test_create_correction_auto_classifies_knowledge():
    db = _SeqDB([])
    row = await CorrectionService.create_correction(
        db, "u1", "t1", "原文", "废液不能倒入下水道，应分类收集")
    assert row.scope == SCOPE_KB_TRUTH
    assert row.scope_source == SCOPE_SOURCE_RULE


@pytest.mark.asyncio
async def test_create_correction_explicit_scope_is_admin_sourced():
    db = _SeqDB([])
    row = await CorrectionService.create_correction(
        db, "u1", "t1", "原文", "内容", scope=SCOPE_DEPT_RULE, dept_id=3)
    assert row.scope == SCOPE_DEPT_RULE
    assert row.scope_source == SCOPE_SOURCE_ADMIN
    assert row.dept_id == 3


@pytest.mark.asyncio
async def test_create_correction_drops_dept_id_for_non_dept_scope():
    """dept_id 只在 dept_rule 下有意义，避免留下误导性的孤儿归属。"""
    db = _SeqDB([])
    row = await CorrectionService.create_correction(
        db, "u1", "t1", "原文", "内容", scope=SCOPE_KB_TRUTH, dept_id=3)
    assert row.dept_id is None


@pytest.mark.asyncio
async def test_review_can_promote_scope_and_audits_it():
    """把偏好升级为全局是高风险动作，必须留下可追溯的审计记录。"""
    ticket = SimpleNamespace(
        id=5, uid="u1", status="pending", scope=SCOPE_USER_PREF,
        scope_source=SCOPE_SOURCE_RULE, proposed_content="回答太长了",
        reviewed_content=None, confidence=0.8, expires_at=None,
        reviewer_uid=None, review_note=None,
    )
    db = _SeqDB([ticket, None])  # 工单查询、来源反馈查询（无）
    row = await CorrectionService.review_correction(
        db, 5, "admin-1", True, note="确认是知识错误", scope=SCOPE_KB_TRUTH)
    assert row.scope == SCOPE_KB_TRUTH
    assert row.scope_source == SCOPE_SOURCE_ADMIN

    event = [o for o in db.added if getattr(o, "event_type", None) == "reviewed"]
    assert event, "作用域升级必须写进审计事件"
    assert event[-1].payload["scope_changed"] == {
        "from": SCOPE_USER_PREF, "to": SCOPE_KB_TRUTH}


@pytest.mark.asyncio
async def test_review_keeps_scope_when_not_provided():
    ticket = SimpleNamespace(
        id=6, uid="u1", status="pending", scope=SCOPE_USER_PREF,
        scope_source=SCOPE_SOURCE_RULE, proposed_content="回答太长了",
        reviewed_content=None, confidence=0.8, expires_at=None,
        reviewer_uid=None, review_note=None,
    )
    db = _SeqDB([ticket, None])
    row = await CorrectionService.review_correction(db, 6, "admin-1", True)
    assert row.scope == SCOPE_USER_PREF
    assert row.scope_source == SCOPE_SOURCE_RULE


# --------------------------------------------------------------------------
# 图谱写回守卫：偏好绝不能入图
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_write_to_graph_rejects_preference():
    """守卫放在 service 层，手动重试接口与任务队列两条路径都绕不过去。"""
    from yuxi.self_evolution.graph_writeback import write_correction_to_graph

    ticket = SimpleNamespace(
        id=9, status="approved", scope=SCOPE_USER_PREF, kb_id="kb-1",
        graph_written=False, graph_file_id=None, rewrite_text="回答要简短",
        proposed_content="回答要简短", reviewed_content=None,
    )
    db = _SeqDB([ticket])
    with pytest.raises(ValueError, match="知识性偏差"):
        await write_correction_to_graph(db, 9, actor_uid="admin-1")


@pytest.mark.asyncio
async def test_write_to_graph_does_not_block_knowledge_truth():
    """守卫不能误伤正常链路：知识性偏差不应被作用域检查拦下。"""
    from yuxi.self_evolution.graph_writeback import write_correction_to_graph

    ticket = SimpleNamespace(
        id=10, status="approved", scope=SCOPE_KB_TRUTH, kb_id="kb-1",
        graph_written=False, graph_file_id=None, rewrite_text="",
        proposed_content="废液应分类收集", reviewed_content=None,
    )
    db = _SeqDB([ticket, None])
    try:
        await write_correction_to_graph(db, 10, actor_uid="admin-1")
    except ValueError as exc:
        assert "知识性偏差" not in str(exc)
    except Exception:
        pass  # 后续依赖（LLM 改写、Milvus、图谱配置）本机不可用，与守卫无关
