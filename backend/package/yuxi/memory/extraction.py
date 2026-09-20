"""独立的记忆抽取调用：把「哪一轮该记什么」从回答链路里彻底拿出来。

为什么不再用围栏
----------------
旧协议让主模型在回答正文末尾附一个 ```` ```yuxi-memory ```` 块。代价是：
- 流式层必须自带一个跨 chunk 的剥离状态机（围栏标记会被切成两片）；
- 回答 prompt 里塞进二十多行抽取纪律，模型在"答题"和"填表"之间分心；
- 落库必须内联在流结束处，失败只能吞掉——不能因为记忆写失败毁掉一次已生成的回答；
- 模型看不到事实状态机的现状，判断不了"这条已经在里面了 / 用户改口了"。

现在改成：回答照常流式返回，**流结束后入队一个后台任务**，由它单独调一次模型。
接受"事实晚一轮生效"的延迟（用户明确选择）。

通道归属为什么由服务端裁定
--------------------------
需求是"这次调用决定哪些进事实状态机、哪些进长期记忆"。但通道**不能**真的交给模型定：
`MEMORY_FACT_KEYS` 的白名单前缀已经编码了归属（`user.*` 是跨会话画像、
`project.*` 是会话内约束），让模型自由填就会重现"方向对穿"——
偏好被写进会话事实表、会话事实被写进长期记忆表。
所以模型可以提议 `channel`，服务端一律按 key 的前缀**改判**，并把不一致计入
``channel_mismatch`` 埋点。这与项目既有的"LLM 只能提议，不能决定"边界一致。

埋点
----
围栏时代靠 `dropped_ops` / `parse_failures` 判断"模型有没有为凑格式编造偏好"。
结构化调用后这套信号消失了，必须换成新的一组，否则上线之后是盲的：
``call_failed`` / ``empty_response`` / ``parse_failed`` / ``rejected`` / ``channel_mismatch``。

抽取口径为什么放在技能文件里
----------------------------
"该记什么、各类键的边界、正反例、置信度怎么给"这类**口径**写在技能文件
``agents/skills/buildin/memory-extraction/SKILL.md`` 里，本模块运行时去读它，
而不是硬编码在下面的常量里。理由是口径要反复调，而调口径不该等于改代码 + 发版。
文件按 (mtime, size) 缓存，编辑后下一次抽取即生效，无需重启。

``EXTRACTION_SYSTEM_PROMPT`` 因此退化为**兜底**：技能文件缺失或未按约定夹标记时
仍然能抽取（宁可口径旧一点，也不能因为一个文档被删掉就让记忆功能整体失效）。
两者的对齐由 ``test/unit/memory/test_memory_skill_guide.py`` 的漂移守卫保证——
白名单加了键而文档没加，测试会红。
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.memory import cursor as cursor_repo
from yuxi.memory.observation import (
    MAX_CONTENT_LEN,
    MEMORY_FACT_KEYS,
    SENSITIVE_PATTERN,
    arbitrate,
    canonical_channel,
    normalize_op,
)
from yuxi.memory.service import MemoryService
from yuxi.storage.postgres.event_log import record_event
from yuxi.storage.postgres.models_business import (
    AgentRun,
    Message,
    SessionFact,
    UserMemoryFact,
)
from yuxi.storage.postgres.system_kv import get_system_kv
from yuxi.utils.datetime_utils import utc_now_naive

logger = logging.getLogger(__name__)

ROUTING_KV = "model_routing"
MEMORY_OBSERVE_KV = "memory_observe"

#: 单次抽取最多接受多少条 op（比围栏时代的 5 条宽，因为现在不占用回答预算）。
MAX_OPS_PER_EXTRACTION = 8
#: 送进 prompt 的问答正文上限，按字符粗切，防止一次塞进几十轮。
MAX_TRANSCRIPT_CHARS = 12000
#: 单条正文上限，和观察协议保持一致。
_CONTENT_LIMIT = MAX_CONTENT_LEN
#: 历史消息里可能残留旧协议的围栏块，抽取前剥掉，避免把协议块本身当内容抽出来。
_LEGACY_FENCE_RE = re.compile(r"```yuxi-memory\b.*?(?:```|\Z)", re.S)

#: **兜底** prompt。正常路径下不生效——口径以技能文件为准（见模块 docstring）。
#: 保留它是为了"技能文件被删/写坏"时不至于让记忆抽取整体失效。
EXTRACTION_SYSTEM_PROMPT = """你是对话记忆抽取器。给你一段用户与助手的多轮问答，\
以及系统当前已记录的事实，你的任务是判断这段对话里出现了哪些**值得长期留存**的信息。

只输出 JSON，不要任何解释、不要 markdown 代码块：
{"ops": [{"turn": 2, "channel": "memory", "fact_key": "user.major", "content": "化学工程", "confidence": 0.95}]}

字段约束：
- `fact_key` 只能取以下之一（不在其中的一律不要输出）：
  user.major、user.education_stage、user.research_direction、user.work_environment、
  user.lab_role、user.response_preference、project.current_goal、project.phase_scope、
  project.constraints、project.acceptance_criteria、project.rejected_approach
- `channel`：`memory` 表示跨会话的长期画像，`session` 表示只在当前会话有效的约束。
  （系统会按 fact_key 复核这一项，填错不会出错，但填对更好。）
- `content` 用一句话客观陈述，不超过 {limit} 字，不要带"用户说"之类前缀。
- `confidence` 是 0~1；用户明确说出的给 0.85 以上，推断的给更低。
- `turn` 是这句话出现在第几轮（从 1 开始），不确定就省略。
- 不要输出已记录事实的重复项；用户改口时输出新值。
- 不要记录实验室安全规范、化学品性质、操作步骤——那是知识库的职责。
- 不要记录手机号、邮箱、身份证、密码、住址等隐私信息。
- 没有值得留存的信息就输出 {"ops": []}，不要为了凑格式编造。
""".replace("{limit}", str(_CONTENT_LIMIT))


#: 抽取口径的技能文件。改口径改这里，不用改代码。
GUIDE_SKILL_PATH = (
    Path(__file__).resolve().parents[1]
    / "agents" / "skills" / "buildin" / "memory-extraction" / "SKILL.md"
)
#: 技能文件里只给模型看的那一段（人和 Agent 读的是整篇）。
GUIDE_PROMPT_START = "<!-- prompt:start -->"
GUIDE_PROMPT_END = "<!-- prompt:end -->"

#: (mtime_ns, size) -> 已解析的口径文本。按 mtime 失效，编辑文件后下次抽取即生效，
#: 不必重启进程；不缓存的话每轮都要读盘，而每轮只抽一次、读的是本地小文件，
#: 缓存只是省掉重复解析，不是为了省 IO。
_guide_cache: dict[str, tuple[int, int, str]] = {}


def _split_skill_frontmatter(content: str) -> str:
    """剥掉 SKILL.md 的 YAML frontmatter，返回正文。

    刻意不复用 ``agents.skills.service._split_frontmatter``：那是私有函数，且那个模块
    会拉起 yaml + 数据库依赖链，而这里是每轮问答都要走一遍的热路径旁路。
    """
    if not content.startswith("---"):
        return content
    lines = content.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return content
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            return "".join(lines[index + 1:])
    return content


def _extract_prompt_section(content: str) -> str:
    """取正文里夹在 ``<!-- prompt:start/end -->`` 之间的段落；格式不符则返回空串。

    返回空串而不是整篇正文是刻意的：整篇含机制说明、排查步骤等给人和 Agent 看的内容，
    塞进模型 prompt 只会烧 token 并干扰它。

    **标记必须恰好各出现一次**，否则一律判为格式损坏、退回兜底 prompt。这条不是洁癖：
    文档里若在说明段中顺带引用了标记字面量（本文件初版就踩了这个坑），
    ``find`` 会命中更靠前的那一处，于是截出两个标记之间的散文垃圾当 prompt，
    而且**不报任何错**——模型只是开始按一段废话抽取。宁可用旧口径，也不能静默跑偏。
    """
    body = _split_skill_frontmatter(content)
    if body.count(GUIDE_PROMPT_START) != 1 or body.count(GUIDE_PROMPT_END) != 1:
        return ""
    start = body.index(GUIDE_PROMPT_START)
    end = body.index(GUIDE_PROMPT_END)
    if end < start:
        return ""
    return body[start + len(GUIDE_PROMPT_START):end].strip()


def load_extraction_guide(path: Path | str | None = None) -> str:
    """读技能文件里的抽取口径段；读不到/没标记就返回空串（调用方回落到兜底 prompt）。"""
    target = Path(path) if path is not None else GUIDE_SKILL_PATH
    try:
        stat = target.stat()
    except OSError:
        return ""
    key = str(target)
    cached = _guide_cache.get(key)
    if cached and cached[0] == stat.st_mtime_ns and cached[1] == stat.st_size:
        return cached[2]
    try:
        content = target.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        logger.exception("failed to read memory extraction skill: %s", target)
        return ""
    guide = _extract_prompt_section(content)
    if not guide:
        # 文件在、但标记缺失或出现多次——这是文档被改坏，要能在日志里看见，
        # 而不是静默回落到旧口径（那时"改了口径却没生效"会变成查不出来的悬案）。
        logger.warning(
            "memory extraction skill has no usable prompt section "
            "(need exactly one start/end marker): %s", target)
    _guide_cache[key] = (stat.st_mtime_ns, stat.st_size, guide)
    return guide


def build_system_prompt() -> str:
    """组装抽取调用的 system prompt：技能文件优先，缺失则用内置兜底。

    ``{limit}`` 在最后统一替换：技能文件与兜底 prompt 都用同一个占位符，
    谁生效都不会漏掉替换。（注意：这里不能用 ``str.format``，两者正文里都有 JSON
    示例的花括号。）
    """
    guide = load_extraction_guide()
    base = guide or EXTRACTION_SYSTEM_PROMPT
    return base.strip().replace("{limit}", str(_CONTENT_LIMIT))


@dataclass(frozen=True)
class ExtractionConfig:
    """抽取开关与模型选择。默认跟随系统，出问题可只关抽取而不发版。"""

    enabled: bool = True
    model_spec: str | None = None
    min_interval_seconds: int = 0


@dataclass(frozen=True)
class ExtractionTurn:
    """一轮问答，附带它来自哪条 run（用于回填来源）。"""

    run: AgentRun
    question: str
    answer: str


async def resolve_config(db: AsyncSession) -> ExtractionConfig:
    """读抽取配置：开关沿用既有的 ``memory_observe``，模型与节流在新字段上。

    读失败一律回落到"开 + 每轮抽"，与 ``_resolve_bool_switch`` 的语义一致：
    配置读不到不该等于功能被悄悄关掉。
    """
    enabled = True
    model_spec: str | None = None
    min_interval = 0
    try:
        kv = await get_system_kv(db, MEMORY_OBSERVE_KV)
        if kv and isinstance(kv.value, dict) and "enabled" in kv.value:
            enabled = bool(kv.value["enabled"])
    except Exception:
        logger.exception("failed to read memory_observe kv")
    try:
        kv = await get_system_kv(db, ROUTING_KV)
        value = kv.value if kv and isinstance(kv.value, dict) else {}
        spec = value.get("memory_model_spec")
        model_spec = str(spec).strip() if isinstance(spec, str) and spec.strip() else None
        raw_interval = value.get("memory_extract_min_interval", 0)
        try:
            min_interval = max(0, int(raw_interval))
        except (TypeError, ValueError):
            min_interval = 0
    except Exception:
        logger.exception("failed to read model_routing kv")
    return ExtractionConfig(enabled=enabled, model_spec=model_spec, min_interval_seconds=min_interval)


def strip_legacy_fence(text: str | None) -> str:
    """剥掉历史消息里残留的旧协议围栏块（改造前落库的消息带着它）。"""
    if not text:
        return ""
    without_fence = _LEGACY_FENCE_RE.sub("", text)
    return without_fence.strip()


def build_transcript(turns: list[ExtractionTurn]) -> str:
    """把轮次拼成带序号的多轮文本；超出上限时**保留最近**的轮次。"""
    blocks: list[str] = []
    for index, turn in enumerate(turns, start=1):
        blocks.append(
            f"【第 {index} 轮】\n用户：{strip_legacy_fence(turn.question)}\n"
            f"助手：{strip_legacy_fence(turn.answer)}"
        )
    kept: list[str] = []
    total = 0
    for block in reversed(blocks):
        if total + len(block) > MAX_TRANSCRIPT_CHARS and kept:
            break
        kept.append(block)
        total += len(block)
    kept.reverse()
    return "\n\n".join(kept)


def build_existing_facts_block(session_facts, memories) -> str:
    """当前事实快照。

    这是独立调用**独有**的能力：内联的围栏协议里模型只看得到自己这一轮，
    判断不了"这条已经在状态机里""用户改口了"，而状态机恰恰需要这个输入。
    """
    lines: list[str] = []
    if session_facts:
        lines.append("【当前会话已有事实】")
        lines.extend(f"- {row.fact_key}：{row.content}" for row in session_facts)
    if memories:
        lines.append("【已有的长期记忆】")
        lines.extend(f"- {row.fact_key}：{row.content}（{row.status}）" for row in memories)
    return "\n".join(lines)


def normalize_extracted_op(op: object) -> tuple[str, str, str, float, int | None] | None:
    """把一条模型输出规范成 ``(channel, fact_key, content, confidence, turn)``。

    ``channel`` 以 **fact_key 的前缀**为准（服务端裁定），模型给的只在相同时也算数。
    返回 None 表示这条不可用（白名单外 / 敏感 / 非法结构）——宁可漏存。
    """
    normalized = normalize_op(op)
    if normalized is None:
        return None
    fact_key, content, confidence = normalized
    channel = canonical_channel(fact_key)
    if channel is None:  # 白名单里没有的 key，理论上不可达（normalize_op 已挡）
        return None
    turn: int | None = None
    if isinstance(op, dict):
        try:
            raw_turn = op.get("turn")
            turn = int(raw_turn) if raw_turn is not None else None
        except (TypeError, ValueError):
            turn = None
        if turn is not None and turn < 1:
            turn = None
    return channel, fact_key, content, confidence, turn


def parse_extraction_response(text: str) -> tuple[list[tuple[str, str, str, float, int | None]], dict]:
    """解析模型返回的 JSON，并统计"提议通道 vs 裁定通道"的不一致数。

    宽容解析：整段解析失败时退化为取第一个花括号片段。模型偶尔会多写一句说明
    或套一层 ```json——这一层容错留在这里，但不再需要跨 chunk 的状态机。
    """
    stats = {"raw_ops": 0, "accepted_ops": 0, "dropped_ops": 0, "channel_mismatch": 0, "parse_failed": 0}
    payload = _loads_loose(text)
    if payload is None:
        stats["parse_failed"] = 1
        return [], stats
    raw_ops = payload.get("ops")
    if not isinstance(raw_ops, list):
        stats["parse_failed"] = 1
        return [], stats
    stats["raw_ops"] = len(raw_ops)
    accepted: list[tuple[str, str, str, float, int | None]] = []
    for raw in raw_ops[:MAX_OPS_PER_EXTRACTION]:
        parsed = normalize_extracted_op(raw)
        if parsed is None:
            stats["dropped_ops"] += 1
            continue
        channel, fact_key, _content, _confidence, _turn = parsed
        claimed = ""
        if isinstance(raw, dict):
            claimed = str(raw.get("channel") or "").strip().lower()
        if claimed and claimed != channel:
            stats["channel_mismatch"] += 1
        accepted.append(parsed)
    stats["dropped_ops"] += max(0, len(raw_ops) - MAX_OPS_PER_EXTRACTION)
    stats["accepted_ops"] = len(accepted)
    return accepted, stats


def _loads_loose(text: str) -> dict | None:
    try:
        data = json.loads(text)
    except Exception:
        match = re.search(r"\{.*\}", text, re.S)
        if not match:
            return None
        try:
            data = json.loads(match.group(0))
        except Exception:
            return None
    return data if isinstance(data, dict) else None


async def _load_turns(db: AsyncSession, runs: list[AgentRun]) -> list[ExtractionTurn]:
    """按 ``input_message_id`` / ``output_message_id`` 取回每轮的提问与回答。

    刻意不绕 ``messages.request_id``：那一列在老链路的 user 行上是空的
    （只塞进了 ``extra_metadata``），join 过去只能命中 assistant 行。
    """
    turns: list[ExtractionTurn] = []
    for run in runs:
        messages = {}
        wanted = [mid for mid in (run.input_message_id, run.output_message_id) if mid]
        if wanted:
            rows = (await db.execute(select(Message).where(Message.id.in_(wanted)))).scalars().all()
            messages = {row.id: row for row in rows}
        question = getattr(messages.get(run.input_message_id), "content", None)
        answer = getattr(messages.get(run.output_message_id), "content", None)
        if not question and not answer:
            continue
        turns.append(ExtractionTurn(run=run, question=question or "", answer=answer or ""))
    return turns


async def _load_existing_facts(db: AsyncSession, uid: str, thread_id: str):
    session_facts = (await db.execute(select(SessionFact).where(
        SessionFact.uid == uid, SessionFact.thread_id == thread_id, SessionFact.status == "active",
    ))).scalars().all()
    memories = (await db.execute(select(UserMemoryFact).where(
        UserMemoryFact.uid == uid,
        UserMemoryFact.status.in_(("candidate", "pending_confirmation", "confirmed")),
    ))).scalars().all()
    return session_facts, memories


async def _resolve_memory_enabled(db: AsyncSession, uid: str) -> bool:
    """用户级长期记忆开关。

    会话事实不受它管（设计文档 §2.3），所以它**只**决定 ``user.*`` 那一半是否落库。
    与热路径同源同默认：无记录回落 ``DEFAULT_ENABLE_MEMORY``，而不是当成关闭——
    否则"默认开启"的策略会在后台任务里被悄悄关掉（本项目已有同类坑）。
    """
    from yuxi.config.user import DEFAULT_ENABLE_MEMORY
    from yuxi.storage.postgres.models_business import UserConfig

    try:
        stored = (await db.execute(
            select(UserConfig.enable_memory).where(UserConfig.uid == uid))).scalar()
    except Exception:
        logger.exception("failed to resolve enable_memory")
        return DEFAULT_ENABLE_MEMORY
    return DEFAULT_ENABLE_MEMORY if stored is None else bool(stored)


async def _call_model(model_spec: str, prompt: str, *, timeout: float = 60.0) -> str | None:
    """独立模型调用；失败返回 None，由调用方原样上报而不是抛出去。"""
    from yuxi.models.chat import select_model  # 延迟 import：缺配置时不必拉起 LLM 依赖链

    model = select_model(model_spec=model_spec, timeout=timeout)
    response = await model.call(prompt, stream=False)
    content = getattr(response, "content", None)
    return content.strip() if isinstance(content, str) else None


def _resolve_model_spec(config: ExtractionConfig, runs: list[AgentRun]) -> str:
    """模型优先级：SystemKV 显式配置 > 本批最后一轮 agent 用的模型 > 系统默认。

    中间那档是刻意的：没单独配抽取模型时，跟随该会话正在用的模型，比硬编码系统默认
    更可能支持中文抽取，也避免与主链路模型能力差太多。
    """
    from yuxi.agents.models import resolve_chat_model_spec

    for run in reversed(runs):
        payload = run.input_payload if isinstance(run.input_payload, dict) else {}
        spec = payload.get("model_spec")
        if isinstance(spec, str) and spec.strip():
            return resolve_chat_model_spec(config.model_spec, fallback=spec.strip())
    return resolve_chat_model_spec(config.model_spec)


async def extract_pending_turns(db: AsyncSession, thread_id: str) -> dict:
    """消费游标之后的轮次并落库。幂等，可被 ARQ 安全重试。

    返回值是给日志和审计用的计数器，形如
    ``{"status": "ok", "runs": 2, "session": 1, "memory": 1, ...}``。
    ``status`` 取值：``idle``（没有新轮次）/ ``disabled`` / ``throttled`` /
    ``no_content``（消息还没落库，**不推进游标**，等下次）/ ``ok``。
    """
    cursor_row = await cursor_repo.load_cursor(db, thread_id)
    runs = await cursor_repo.pending_runs(db, thread_id, cursor_row)
    if not runs:
        return {"status": "idle"}

    config = await resolve_config(db)
    if not config.enabled:
        return {"status": "disabled", "runs": len(runs)}

    if cursor_row is not None and cursor_row.last_extracted_at and config.min_interval_seconds > 0:
        elapsed = (utc_now_naive() - cursor_row.last_extracted_at).total_seconds()
        if elapsed < config.min_interval_seconds:
            # 节流只能"这次先不抽"，**不能**推进游标：攒下的轮次下一轮一起抽。
            return {"status": "throttled", "runs": len(runs), "elapsed": int(elapsed)}

    turns = await _load_turns(db, runs)
    if not turns:
        return {"status": "no_content", "runs": len(runs)}

    uid = runs[-1].uid
    session_facts, memories = await _load_existing_facts(db, uid, thread_id)
    prompt = "\n\n".join(part for part in (
        build_system_prompt(),
        build_existing_facts_block(session_facts, memories),
        "【对话原文】\n" + build_transcript(turns),
    ) if part)

    counters: dict = {"status": "ok", "runs": len(runs), "turns": len(turns)}
    try:
        model_spec = _resolve_model_spec(config, runs)
        raw = await _call_model(model_spec, prompt)
    except Exception:
        logger.exception("memory extraction call failed")
        counters.update({"status": "call_failed", "call_failed": 1})
        await _record_extraction(db, uid, cursor_row, runs, counters)
        return counters

    if not raw:
        counters.update({"status": "empty_response", "empty_response": 1})
        await _record_extraction(db, uid, cursor_row, runs, counters)
        # 空返回也推进游标：模型看了但认为这轮没有可留存的信息，重试没有意义。
        await cursor_repo.advance_cursor(db, thread_id, uid, runs[-1])
        await db.commit()
        return counters

    ops, parse_stats = parse_extraction_response(raw)
    counters.update(parse_stats)

    request_ids = {index: run.request_id for index, run in enumerate(runs, start=1)}
    located = [
        (channel, fact_key, content, confidence, request_ids.get(turn) if turn else None)
        for channel, fact_key, content, confidence, turn in ops
    ]
    applied = await MemoryService.apply_extracted_ops(
        db, uid, thread_id, located, source="async_extraction",
    )
    counters.update(applied)

    await cursor_repo.advance_cursor(db, thread_id, uid, runs[-1])
    await db.commit()
    await _record_extraction(db, uid, cursor_row, runs, counters)
    await db.commit()
    logger.info("memory extraction done thread=%s %s", thread_id, counters)
    return counters


async def _record_extraction(db: AsyncSession, uid: str, cursor_row, runs: list[AgentRun], counters: dict) -> None:
    """把一次抽取写进 ``memory_events``（与两个模块共用的审计表）。

    不再有围栏之后，"模型在编造"的唯一可观测信号就是这里的计数，
    因此这一步**不能**省：静默丢弃等于抽取上线了也是盲的。
    """
    try:
        target_id = cursor_row.id if cursor_row is not None else 0
        await record_event(
            db, uid, "extraction", int(target_id), "extracted",
            payload={**counters, "first_run_id": runs[0].id, "last_run_id": runs[-1].id},
            actor_type="llm",
        )
    except Exception:
        logger.exception("failed to record extraction event")


__all__ = [
    "EXTRACTION_SYSTEM_PROMPT",
    "ExtractionConfig",
    "ExtractionTurn",
    "GUIDE_SKILL_PATH",
    "MAX_OPS_PER_EXTRACTION",
    "build_existing_facts_block",
    "build_system_prompt",
    "build_transcript",
    "extract_pending_turns",
    "load_extraction_guide",
    "normalize_extracted_op",
    "parse_extraction_response",
    "resolve_config",
    "strip_legacy_fence",
]

# 白名单与敏感词的唯一来源仍在 observation.py；这里只做存在性自检，
# 防止将来有人改了白名单前缀而忘了通道裁定。
assert all(canonical_channel(key) for key in MEMORY_FACT_KEYS), "白名单里存在无法裁定通道的 key"
assert SENSITIVE_PATTERN is not None and arbitrate is not None
