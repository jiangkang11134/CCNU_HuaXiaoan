"""独立抽取调用的单测（替代原围栏协议的剥离器测试）。

覆盖三件事：
1. 解析/规范化的**服务端裁定**：通道由 key 前缀定，模型的提议只作为埋点；
2. 埋点：``dropped_ops`` / ``parse_failed`` / ``channel_mismatch`` 必须真的计数，
   否则抽取上线之后是盲的（围栏时代的等价信号随剥离器一起消失了）；
3. ``extract_pending_turns`` 的四种短路与幂等推进——尤其**节流不能推进游标**，
   否则被跳过的轮次会永久丢失。
"""
from datetime import timedelta
from types import SimpleNamespace

import pytest

from yuxi.memory import extraction
from yuxi.memory.extraction import (
    MAX_OPS_PER_EXTRACTION,
    ExtractionConfig,
    ExtractionTurn,
    build_existing_facts_block,
    build_transcript,
    normalize_extracted_op,
    parse_extraction_response,
    resolve_config,
    strip_legacy_fence,
)
from yuxi.utils.datetime_utils import utc_now_naive


# --------------------------------------------------------------------------
# 历史消息里残留的旧协议块
# --------------------------------------------------------------------------

def test_strip_legacy_fence_removes_old_protocol_block():
    text = '正文回答\n```yuxi-memory\n{"ops":[]}\n```'
    assert strip_legacy_fence(text) == "正文回答"


def test_strip_legacy_fence_keeps_ordinary_code_blocks():
    text = "示例：\n```python\nprint(1)\n```"
    assert strip_legacy_fence(text) == text


def test_strip_legacy_fence_handles_unterminated_block_and_none():
    assert strip_legacy_fence("正文```yuxi-memory\n{\"ops\":[]}") == "正文"
    assert strip_legacy_fence(None) == ""


# --------------------------------------------------------------------------
# prompt 组装
# --------------------------------------------------------------------------

def _turn(q: str, a: str) -> ExtractionTurn:
    return ExtractionTurn(run=SimpleNamespace(), question=q, answer=a)


def test_build_transcript_numbers_turns():
    text = build_transcript([_turn("问一", "答一"), _turn("问二", "答二")])
    assert "【第 1 轮】" in text and "【第 2 轮】" in text
    assert text.index("问一") < text.index("问二")


def test_build_transcript_keeps_most_recent_when_over_limit():
    """超长时保留**最近**的轮次：记忆关心的是用户当前在做什么。"""
    long_answer = "甲" * 7000
    turns = [_turn("很早的问题", long_answer), _turn("刚刚的问题", long_answer)]
    text = build_transcript(turns)
    assert "刚刚的问题" in text
    assert "很早的问题" not in text


def test_build_transcript_strips_legacy_fence_from_history():
    """改造前落库的消息带着围栏块，不能把协议块本身当内容抽出来。"""
    text = build_transcript([_turn("问", '答\n```yuxi-memory\n{"ops":[]}\n```')])
    assert "yuxi-memory" not in text


def test_build_existing_facts_block_includes_both_sources():
    session_facts = [SimpleNamespace(fact_key="project.phase_scope", content="只做第一步")]
    memories = [SimpleNamespace(fact_key="user.major", content="化学工程", status="confirmed")]
    block = build_existing_facts_block(session_facts, memories)
    assert "当前会话已有事实" in block and "project.phase_scope" in block
    assert "已有的长期记忆" in block and "confirmed" in block


def test_build_existing_facts_block_empty():
    assert build_existing_facts_block([], []) == ""


# --------------------------------------------------------------------------
# 规范化：通道由服务端裁定
# --------------------------------------------------------------------------

def test_normalize_extracted_op_takes_channel_from_key():
    parsed = normalize_extracted_op(
        {"fact_key": "user.major", "content": "化学工程", "confidence": 0.95, "turn": 2}
    )
    assert parsed == ("memory", "user.major", "化学工程", 0.95, 2)

    parsed = normalize_extracted_op(
        {"fact_key": "project.constraints", "content": "只用常温", "confidence": 0.8}
    )
    assert parsed[0] == "session" and parsed[4] is None


def test_normalize_extracted_op_ignores_model_claimed_channel():
    """模型把 project.* 标成 memory 也好，通道仍按 key 前缀定。"""
    parsed = normalize_extracted_op(
        {"fact_key": "project.constraints", "channel": "memory", "content": "只用常温", "confidence": 0.8}
    )
    assert parsed[0] == "session"


def test_normalize_extracted_op_rejects_bad_payloads():
    assert normalize_extracted_op({"fact_key": "user.hobby", "content": "爬山"}) is None
    assert normalize_extracted_op({"fact_key": "user.major", "content": "邮箱 a@b.com"}) is None
    assert normalize_extracted_op("nope") is None
    # turn 非法时降级为 None，不让它污染 request_id 映射
    assert normalize_extracted_op(
        {"fact_key": "user.major", "content": "化学工程", "turn": "x"}
    )[4] is None
    assert normalize_extracted_op(
        {"fact_key": "user.major", "content": "化学工程", "turn": 0}
    )[4] is None


# --------------------------------------------------------------------------
# 解析与埋点
# --------------------------------------------------------------------------

def test_parse_extraction_response_accepts_valid_ops():
    ops, stats = parse_extraction_response(
        '{"ops":[{"turn":1,"fact_key":"user.major","content":"化学工程","confidence":0.95}]}'
    )
    assert ops == [("memory", "user.major", "化学工程", 0.95, 1)]
    assert stats["accepted_ops"] == 1
    assert stats["dropped_ops"] == 0
    assert stats["parse_failed"] == 0


def test_parse_extraction_response_counts_dropped_and_parse_failed():
    payload = (
        '{"ops":[{"fact_key":"user.hobby","content":"爬山","confidence":0.9},'
        '{"fact_key":"user.major","content":"化学工程","confidence":0.95}]}'
    )
    ops, stats = parse_extraction_response(payload)
    assert len(ops) == 1
    assert stats["raw_ops"] == 2
    assert stats["dropped_ops"] == 1  # user.hobby 不在白名单

    ops, stats = parse_extraction_response("这不是 JSON")
    assert ops == []
    assert stats["parse_failed"] == 1
    assert stats["raw_ops"] == 0


def test_parse_extraction_response_counts_channel_mismatch():
    """埋点看的是"模型以为的通道"和"服务端裁定的通道"差多少，而不是让模型说了算。"""
    payload = '{"ops":[{"fact_key":"user.major","channel":"session","content":"化学工程","confidence":0.9}]}'
    ops, stats = parse_extraction_response(payload)
    assert stats["channel_mismatch"] == 1
    assert ops[0][0] == "memory"  # 裁定仍然按 key


def test_parse_extraction_response_caps_ops_and_tolerates_json_code_block():
    many = ",".join(
        '{"fact_key":"user.major","content":"x%d","confidence":0.9}' % i
        for i in range(MAX_OPS_PER_EXTRACTION + 3)
    )
    ops, stats = parse_extraction_response('```json\n{"ops":[%s]}\n```' % many)
    assert len(ops) == MAX_OPS_PER_EXTRACTION
    assert stats["dropped_ops"] == 3  # 超出的部分计入丢弃


# --------------------------------------------------------------------------
# 配置解析
# --------------------------------------------------------------------------

class _KVDB:
    def __init__(self, kv: dict):
        self._kv = kv

    async def get(self, _model, key):
        value = self._kv.get(key)
        return SimpleNamespace(value=value) if value is not None else None


@pytest.mark.asyncio
async def test_resolve_config_defaults_to_enabled_without_model_override():
    config = await resolve_config(_KVDB({}))
    assert config.enabled is True
    assert config.model_spec is None
    assert config.min_interval_seconds == 0


@pytest.mark.asyncio
async def test_resolve_config_reads_switches():
    config = await resolve_config(_KVDB({
        "memory_observe": {"enabled": False},
        "model_routing": {"memory_model_spec": "openai/gpt-4o-mini", "memory_extract_min_interval": "30"},
    }))
    assert config.enabled is False
    assert config.model_spec == "openai/gpt-4o-mini"
    assert config.min_interval_seconds == 30


@pytest.mark.asyncio
async def test_resolve_config_survives_malformed_values():
    config = await resolve_config(_KVDB({
        "memory_observe": "not-a-dict",
        "model_routing": {"memory_model_spec": "  ", "memory_extract_min_interval": "abc"},
    }))
    assert config.enabled is True
    assert config.model_spec is None
    assert config.min_interval_seconds == 0


# --------------------------------------------------------------------------
# 主流程：短路、幂等推进、节流不推进
# --------------------------------------------------------------------------

def _run(run_id="run-1", uid="u1", created_at=None, request_id="req-1"):
    return SimpleNamespace(
        id=run_id, uid=uid, request_id=request_id,
        created_at=created_at or utc_now_naive(),
        input_message_id=1, output_message_id=2, input_payload={},
    )


class _PatchRecorder:
    def __init__(self):
        self.applied = []
        self.advanced = []
        self.model_calls = 0
        self.prompts: list[str] = []
        self.recorded = False


async def _stub_config(_db):
    """默认配置：开、不指定模型、不节流。"""
    return ExtractionConfig(enabled=True, model_spec=None, min_interval_seconds=0)


@pytest.fixture
def patched(monkeypatch):
    """把抽取主流程的外部依赖全部替换成记录器。"""
    rec = _PatchRecorder()

    async def fake_load_cursor(_db, _thread_id):
        return rec.cursor

    async def fake_pending_runs(_db, _thread_id, _cursor, **_kw):
        return rec.runs

    async def fake_advance(_db, thread_id, uid, last_run, **_kw):
        rec.advanced.append(last_run.id)
        return SimpleNamespace()

    async def fake_load_turns(_db, runs):
        return rec.turns

    async def fake_load_existing(_db, _uid, _thread_id):
        return [], []

    async def fake_memory_enabled(_db, _uid):
        return True

    async def fake_call_model(_model_spec, _prompt):
        rec.model_calls += 1
        rec.prompts.append(_prompt)
        return rec.model_reply

    async def fake_apply(_db, uid, thread_id, ops, **_kw):
        rec.applied.append({"uid": uid, "thread_id": thread_id, "ops": ops})
        return {"session": 0, "memory": 0, "confirmed": 0, "rejected": 0, "skipped": 0}

    async def fake_record(_db, *_a, **_kw):
        rec.recorded = True

    def fake_resolve_model_spec(_config, _runs):
        return "stub-model"

    rec.cursor = None
    rec.runs = []
    rec.turns = []
    rec.model_reply = '{"ops": []}'

    monkeypatch.setattr(extraction.cursor_repo, "load_cursor", fake_load_cursor)
    monkeypatch.setattr(extraction.cursor_repo, "pending_runs", fake_pending_runs)
    monkeypatch.setattr(extraction.cursor_repo, "advance_cursor", fake_advance)
    monkeypatch.setattr(extraction, "_load_turns", fake_load_turns)
    monkeypatch.setattr(extraction, "_load_existing_facts", fake_load_existing)
    monkeypatch.setattr(extraction, "_resolve_memory_enabled", fake_memory_enabled)
    monkeypatch.setattr(extraction, "_call_model", fake_call_model)
    monkeypatch.setattr(extraction, "_resolve_model_spec", fake_resolve_model_spec)
    monkeypatch.setattr(extraction, "_record_extraction", fake_record)
    monkeypatch.setattr(extraction.MemoryService, "apply_extracted_ops", fake_apply)
    monkeypatch.setattr(extraction, "resolve_config", _stub_config)
    return rec


class _NullDB:
    async def commit(self):
        pass


@pytest.mark.asyncio
async def test_idle_when_no_pending_runs(patched):
    patched.runs = []
    result = await extraction.extract_pending_turns(_NullDB(), "t1")
    assert result == {"status": "idle"}
    assert patched.model_calls == 0
    assert patched.advanced == []


@pytest.mark.asyncio
async def test_no_content_does_not_advance_cursor(patched):
    """消息还没落库时不能推进游标，否则这一轮永远抽不到。"""
    patched.runs = [_run()]
    patched.turns = []
    result = await extraction.extract_pending_turns(_NullDB(), "t1")
    assert result["status"] == "no_content"
    assert patched.advanced == []


@pytest.mark.asyncio
async def test_throttled_does_not_advance_cursor(patched, monkeypatch):
    """节流只是"这次先不抽"，攒下的轮次下次连批补上；推进游标等于丢数据。"""
    async def throttled(_db):
        return ExtractionConfig(enabled=True, model_spec=None, min_interval_seconds=600)

    monkeypatch.setattr(extraction, "resolve_config", throttled)
    patched.cursor = SimpleNamespace(last_extracted_at=utc_now_naive())
    patched.runs = [_run()]
    result = await extraction.extract_pending_turns(_NullDB(), "t1")
    assert result["status"] == "throttled"
    assert patched.advanced == []
    assert patched.model_calls == 0


@pytest.mark.asyncio
async def test_throttle_expired_runs_normally(patched, monkeypatch):
    async def throttled(_db):
        return ExtractionConfig(enabled=True, model_spec=None, min_interval_seconds=600)

    monkeypatch.setattr(extraction, "resolve_config", throttled)
    patched.cursor = SimpleNamespace(last_extracted_at=utc_now_naive() - timedelta(seconds=900))
    patched.runs = [_run()]
    patched.turns = [_turn("问", "答")]
    result = await extraction.extract_pending_turns(_NullDB(), "t1")
    assert result["status"] == "ok"


@pytest.mark.asyncio
async def test_call_failed_is_recorded_and_cursor_held(patched, monkeypatch):
    """抽取失败必须留痕；游标不推进，交给 ARQ 重试。"""
    async def boom(_model_spec, _prompt):
        raise RuntimeError("model down")

    monkeypatch.setattr(extraction, "_call_model", boom)
    patched.runs = [_run()]
    patched.turns = [_turn("问", "答")]
    result = await extraction.extract_pending_turns(_NullDB(), "t1")
    assert result["status"] == "call_failed"
    assert result["call_failed"] == 1
    assert patched.advanced == []
    assert getattr(patched, "recorded", False) is True


@pytest.mark.asyncio
async def test_empty_ops_still_advances_cursor(patched):
    """模型看了这段问答但认为没什么可记 → 重试没有意义，直接推进。"""
    patched.runs = [_run()]
    patched.turns = [_turn("问", "答")]
    patched.model_reply = '{"ops": []}'
    result = await extraction.extract_pending_turns(_NullDB(), "t1")
    assert result["status"] == "ok"
    assert result["accepted_ops"] == 0
    assert patched.advanced == ["run-1"]


@pytest.mark.asyncio
async def test_blank_model_response_is_flagged_and_advances(patched):
    """空返回要单独计数（``empty_response``），不能和"解析失败"混为一谈。"""
    patched.runs = [_run()]
    patched.turns = [_turn("问", "答")]
    patched.model_reply = ""  # 真实 _call_model 会把纯空白 strip 成空串
    result = await extraction.extract_pending_turns(_NullDB(), "t1")
    assert result["status"] == "empty_response"
    assert result["empty_response"] == 1
    assert patched.advanced == ["run-1"]


@pytest.mark.asyncio
async def test_ok_path_maps_turn_to_request_id_and_advances(patched):
    """``turn`` 用来把 op 挂回它出自哪一轮 run 的 request_id（可追溯性）。"""
    second = _run("run-2", request_id="req-2")
    patched.runs = [_run("run-1", request_id="req-1"), second]
    patched.turns = [_turn("问一", "答一"), _turn("问二", "答二")]
    patched.model_reply = (
        '{"ops":[{"turn":2,"fact_key":"user.major","content":"化学工程","confidence":0.95},'
        '{"turn":1,"fact_key":"project.constraints","content":"只用常温","confidence":0.8}]}'
    )
    result = await extraction.extract_pending_turns(_NullDB(), "t1")
    assert result["status"] == "ok"
    assert patched.advanced == ["run-2"]
    assert len(patched.applied) == 1
    ops = patched.applied[0]["ops"]
    assert ("memory", "user.major", "化学工程", 0.95, "req-2") in ops
    assert ("session", "project.constraints", "只用常温", 0.8, "req-1") in ops


@pytest.mark.asyncio
async def test_disabled_stops_before_calling_model(patched, monkeypatch):
    async def disabled(_db):
        return ExtractionConfig(enabled=False)

    monkeypatch.setattr(extraction, "resolve_config", disabled)
    patched.runs = [_run()]
    result = await extraction.extract_pending_turns(_NullDB(), "t1")
    assert result["status"] == "disabled"
    assert patched.model_calls == 0
    assert patched.advanced == []


@pytest.mark.asyncio
async def test_sent_prompt_carries_skill_guide_and_existing_facts(patched):
    """发出去的 prompt 由三块拼成：抽取口径 + 已有事实快照 + 对话原文。

    口径那一块必须来自技能文件（``build_system_prompt``），而不是只留在常量里——
    否则"改文档就能改口径"这句承诺是假的，改了文档线上依旧按旧口径抽取。
    """
    patched.runs = [_run()]
    patched.turns = [_turn("我是化学工程专业的", "好的")]
    await extraction.extract_pending_turns(_NullDB(), "t1")

    assert len(patched.prompts) == 1
    prompt = patched.prompts[0]
    assert prompt.startswith(extraction.build_system_prompt())
    assert "【对话原文】" in prompt
    assert "我是化学工程专业的" in prompt
    assert "user.major" in prompt
