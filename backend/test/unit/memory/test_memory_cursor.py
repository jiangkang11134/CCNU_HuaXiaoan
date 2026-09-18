"""抽取消费游标的单测。

重点在**结构断言**而不是行为断言：游标的过滤条件决定了"这一段该抽哪几轮"，
一旦条件被写成 ``A OR B`` 平铺进 WHERE，就会因为运算符优先级把其它门槛全部绕过
（本项目在纠错检索上踩过同类坑，见 ``memory/cursor.py`` 的说明）。
本机没有 PostgreSQL，``pending_runs`` 的 SQL 不会被真正编译，所以只能靠离线结构检查。
"""
from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.sql import operators
from sqlalchemy.sql.elements import BooleanClauseList

from yuxi.memory import cursor as cursor_repo
from yuxi.memory.cursor import (
    MAX_RUNS_PER_EXTRACTION,
    SUBAGENT_RUN_TYPE,
    _pending_conditions,
    is_consumed,
)
from yuxi.storage.postgres.models_business import AgentRun
from yuxi.utils.datetime_utils import utc_now_naive


def _cursor(last_run_id="run-x", created_at=None):
    return cursor_repo.MemoryExtractCursor(
        uid="u1", thread_id="t1",
        last_run_id=last_run_id,
        last_run_created_at=created_at or utc_now_naive(),
    )


def _where(thread_id: str, cursor):
    return select(AgentRun).where(*_pending_conditions(thread_id, cursor)).whereclause


# --------------------------------------------------------------------------
# 条件结构
# --------------------------------------------------------------------------

def test_no_cursor_yields_two_scalar_conditions():
    """没有游标时也必须排除 subagent run，否则子智能体运行会被当用户轮次抽。"""
    conds = _pending_conditions("t1", None)
    assert len(conds) == 2
    for cond in conds:
        assert not isinstance(cond, BooleanClauseList), "单条件不应被折叠成复合子句"


def _render(sql_stmt) -> str:
    """把语句编译成带字面量的 PostgreSQL SQL —— 本机没有 PG，只能这样离线看真实形态。"""
    from sqlalchemy.dialects import postgresql

    return str(sql_stmt.compile(
        dialect=postgresql.dialect(),
        compile_kwargs={"literal_binds": True},
    ))


def test_subagent_run_type_is_excluded():
    sql = _render(select(AgentRun).where(*_pending_conditions("t1", None)))
    assert "run_type" in sql
    assert f"'{SUBAGENT_RUN_TYPE}'" in sql


def test_top_level_operator_is_and_not_or():
    """顶层必须是 AND；OR 只能作为其中一个子条件（同 created_at 的 tie-break）。

    这是本文件的核心断言：顶层若是 OR，thread_id 与 run_type 两道门槛会被短路。
    """
    clause = _where("t1", _cursor())
    assert isinstance(clause, BooleanClauseList)
    assert clause.operator is operators.and_, "顶层出现 OR 会绕过 thread_id / run_type 门槛"


def test_tie_break_or_is_nested_inside_and():
    clause = _where("t1", _cursor())
    nested_ops = [getattr(c, "operator", None) for c in clause.clauses]
    assert operators.or_ in nested_ops, "同一 created_at 的 id tie-break 应该被包在 AND 内部"


def test_compiled_sql_keeps_thresholds_and_nests_the_or():
    """用 sqlglot 离线解析编译后的 SQL，确认 WHERE 顶层是 AND、OR 被括号包住。

    只做结构断言不够：SQLAlchemy 的 ``str()`` 不带字面量，也没有括号；
    真正会跑在 PG 上的是这段编译结果，必须对它做一次语法级校验。
    """
    sqlglot = pytest.importorskip("sqlglot")
    import sqlglot.expressions as exp

    sql = _render(select(AgentRun).where(*_pending_conditions("t1", _cursor())))
    parsed = sqlglot.parse_one(sql, dialect="postgres")
    where = parsed.find(exp.Where)
    assert where is not None
    # 顶层是 AND：三个门槛（thread_id / run_type / 游标）全在合取里
    assert isinstance(where.this, exp.And), f"顶层不是 AND：{where.this}"
    and_chain = str(where.this)
    assert "conversation_thread_id" in and_chain
    assert "run_type" in and_chain
    # OR 存在于某处，但被包成子表达式而不是顶层的连接符
    assert where.find(exp.Or) is not None


# --------------------------------------------------------------------------
# is_consumed：排序键必须是 (created_at, id) 成对比较
# --------------------------------------------------------------------------

class _FakeRun:
    def __init__(self, created_at, run_id):
        self.created_at = created_at
        self.id = run_id


def test_is_consumed_without_cursor_is_false():
    assert is_consumed(None, _FakeRun(utc_now_naive(), "run-a")) is False


def test_is_consumed_compares_created_at_then_id():
    base = utc_now_naive()
    cursor = _cursor(last_run_id="run-m", created_at=base)
    assert is_consumed(cursor, _FakeRun(base - timedelta(seconds=1), "run-z")) is True
    assert is_consumed(cursor, _FakeRun(base + timedelta(seconds=1), "run-a")) is False
    # 同一时刻只能用 id 比：单看 created_at 会把并列的两个 run 判错
    assert is_consumed(cursor, _FakeRun(base, "run-a")) is True
    assert is_consumed(cursor, _FakeRun(base, "run-z")) is False


def test_is_consumed_ignores_run_without_created_at():
    assert is_consumed(_cursor(), _FakeRun(None, "run-z")) is False


def test_max_runs_per_extraction_is_bounded():
    """上限存在是为了防止长期不活跃的会话被节流攒出一次巨大调用。"""
    assert 1 <= MAX_RUNS_PER_EXTRACTION <= 50


# --------------------------------------------------------------------------
# advance_cursor：新建 vs 更新
# --------------------------------------------------------------------------

class _CursorDB:
    def __init__(self, existing=None):
        self.existing = existing
        self.added = []
        self.flushed = 0

    async def execute(self, *_a, **_kw):
        row = self.existing

        class _R:
            def scalar_one_or_none(self_inner):
                return row

        return _R()

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        self.flushed += 1


@pytest.mark.asyncio
async def test_advance_cursor_creates_row_when_absent():
    db = _CursorDB(existing=None)
    run = _FakeRun(utc_now_naive(), "run-9")
    row = await cursor_repo.advance_cursor(db, "t1", "u1", run)
    assert row.thread_id == "t1"
    assert row.last_run_id == "run-9"
    assert row.last_extracted_at is not None
    assert db.added == [row]
    assert db.flushed == 1


@pytest.mark.asyncio
async def test_advance_cursor_updates_existing_row():
    existing = cursor_repo.MemoryExtractCursor(uid="u1", thread_id="t1")
    db = _CursorDB(existing=existing)
    row = await cursor_repo.advance_cursor(db, "t1", "u1", _FakeRun(utc_now_naive(), "run-10"))
    assert row is existing
    assert row.last_run_id == "run-10"
    assert db.added == []
