"""记忆抽取的消费游标：以 ``agent_runs`` 为基准定位"还没抽过的轮次"。

为什么需要它
------------
抽取从"跟随回答流"改成后台独立调用之后，必须有东西回答两个问题：

1. **这次该抽哪几轮** —— 游标之后的 run；
2. **重跑怎么不重复抽** —— 任务被 ARQ 重试时仍从同一游标出发。

没有游标就只能"每次抽最新一轮"，于是被节流跳过的轮次会**永久丢失**，
而重试会重复抽同一轮。

基准为什么选 ``agent_runs``
---------------------------
它已经是"一 run 一行"的轮表：前端每个聊天轮次都会先落一条 AgentRun
（``POST /api/agent/runs`` → ``persist_agent_run_record``），并且带
``input_message_id`` / ``output_message_id``，可以直接取回该轮的提问与回答，
不必绕道 ``messages.request_id``（那一列在老链路 user 行上还是空的）。
``run_type='subagent'`` 的 run 不是用户轮次，一律排除。

排序键为什么是 ``(created_at, id)``
-----------------------------------
``AgentRun.id`` 是 UUID，**本身不可排序**，只有 ``created_at`` 能定序；
但 ``created_at`` 只有微秒精度、同批写入可能并列，所以再用 ``id`` 做 tie-break。
两者必须成对比较，单用 ``id > last_run_id`` 是错的（UUID 比大小没有时间含义）。

幂等策略
--------
游标**在事实写完之后**推进。若中途失败，ARQ 重试会重抽同一轮，此时靠状态机
"内容相同的会话事实/长期记忆视为已存在并跳过"来吸收重复——这是 ``apply_extracted_ops``
（及其 ``_apply_session_fact`` / ``_apply_memory_fact``）本就有的语义，不需要额外去重表。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.storage.postgres.models_business import AgentRun, MemoryExtractCursor
from yuxi.utils.datetime_utils import utc_now_naive

#: 不是用户轮次的 run 类型（子智能体运行）。
SUBAGENT_RUN_TYPE = "subagent"

#: 单次抽取最多消费多少轮，防止长期不活跃的会话被节流攒出一次巨大调用。
MAX_RUNS_PER_EXTRACTION = 20


async def load_cursor(db: AsyncSession, thread_id: str) -> MemoryExtractCursor | None:
    """读取线程游标；从未抽过时返回 None（等价于"游标在最前面"）。"""
    return (await db.execute(
        select(MemoryExtractCursor).where(MemoryExtractCursor.thread_id == thread_id)
    )).scalar_one_or_none()


def _pending_conditions(thread_id: str, cursor: MemoryExtractCursor | None) -> list:
    """拼未消费 run 的过滤条件。

    条件全部用 SQLAlchemy 表达式（不用 ``text()``）：``or_``/``and_`` 会自动加括号，
    而手写文本条件不会——``A OR B`` 直接 AND 进 WHERE 会因优先级把整条作用域门槛绕过。
    """
    conditions = [
        AgentRun.conversation_thread_id == thread_id,
        AgentRun.run_type != SUBAGENT_RUN_TYPE,
    ]
    if cursor is not None and cursor.last_run_created_at is not None:
        created_at = AgentRun.created_at
        conditions.append(
            or_(
                created_at > cursor.last_run_created_at,
                and_(
                    created_at == cursor.last_run_created_at,
                    AgentRun.id > (cursor.last_run_id or ""),
                ),
            )
        )
    return conditions


async def pending_runs(
    db: AsyncSession,
    thread_id: str,
    cursor: MemoryExtractCursor | None,
    *,
    limit: int = MAX_RUNS_PER_EXTRACTION,
) -> list[AgentRun]:
    """按时间序返回游标之后、尚未消费的用户轮次。

    注意这里**显式** ``order_by``：``ConversationRepository.get_messages(limit=N)``
    是升序 + limit，拿到的是最早 N 条而不是最近 N 条，直接照抄会永远只抽会话开头。
    """
    limit = max(1, min(int(limit), MAX_RUNS_PER_EXTRACTION))
    return list((await db.execute(
        select(AgentRun)
        .where(*_pending_conditions(thread_id, cursor))
        .order_by(AgentRun.created_at.asc(), AgentRun.id.asc())
        .limit(limit)
    )).scalars().all())


def is_consumed(cursor: MemoryExtractCursor | None, run: AgentRun) -> bool:
    """该 run 是否已被抽过（用于节流与单轮任务先做一次廉价短路）。"""
    if cursor is None or cursor.last_run_created_at is None:
        return False
    run_created = run.created_at
    if run_created is None:
        return False
    if run_created < cursor.last_run_created_at:
        return True
    if run_created > cursor.last_run_created_at:
        return False
    return run.id <= (cursor.last_run_id or "")


async def advance_cursor(
    db: AsyncSession,
    thread_id: str,
    uid: str,
    last_run: AgentRun,
    *,
    extracted_at: datetime | None = None,
) -> MemoryExtractCursor:
    """把游标推进到 ``last_run``，并记录本次抽取时刻（节流用）。

    ``thread_id`` 上有唯一约束：同一线程并发抽取时，后到的那次会在 flush 时
    撞 IntegrityError 并整体回滚（事实一并回滚），交由 ARQ 重试——重试时
    select 能读到先写入的那一行，于是不会重复落事实。
    """
    row = await load_cursor(db, thread_id)
    now = utc_now_naive()
    if row is None:
        row = MemoryExtractCursor(uid=uid, thread_id=thread_id)
        db.add(row)
    row.uid = uid
    row.last_run_id = last_run.id
    row.last_run_created_at = last_run.created_at
    row.last_extracted_at = extracted_at or now
    row.updated_at = now
    await db.flush()
    return row
