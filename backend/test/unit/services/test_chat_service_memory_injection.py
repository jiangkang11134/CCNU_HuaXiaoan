from __future__ import annotations

import asyncio
from types import SimpleNamespace

from yuxi.agents.context import BaseContext
from yuxi.services import chat_service as svc
from yuxi.services.user_profile import PROFILE_PRECEDENCE_NOTE


class _Res:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows

    def first(self):
        return self._rows[0] if self._rows else None

    def scalar(self):
        return self._rows[0] if self._rows else None

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None


class _SeqDB:
    """按调用顺序依次返回预置结果的假 DB。"""

    def __init__(self, batches):
        self._batches = list(batches)

    async def execute(self, *_args, **_kwargs):
        return _Res(self._batches.pop(0) if self._batches else [])

    async def get(self, _model, _key):
        return None


def test_build_agent_context_keeps_request_intent():
    """意图必须真正落到 context 上——这是 U1（意图驱动回答口径）的前置条件。"""
    agent = SimpleNamespace(context_schema=lambda: BaseContext())
    context = svc._build_agent_context(
        agent, {"request_intent": "correction", "request_intent_confidence": 0.8}
    )
    assert context.request_intent == "correction"
    assert context.request_intent_confidence == 0.8


class _KvDB:
    """只覆盖 SystemKV.get 的假 DB；value=None 表示没有这条记录。"""

    def __init__(self, value=None, boom=False):
        self._value = value
        self._boom = boom

    async def get(self, _model, _key):
        if self._boom:
            raise RuntimeError("db down")
        if self._value is None:
            return None
        return SimpleNamespace(value=self._value)


def test_intent_caliber_switch_defaults_on_and_is_respected():
    assert asyncio.run(svc._resolve_intent_caliber(_KvDB())) is True
    assert asyncio.run(svc._resolve_intent_caliber(_KvDB({"enabled": False}))) is False
    assert asyncio.run(svc._resolve_intent_caliber(_KvDB({"enabled": True}))) is True


def test_bool_switch_read_failure_never_silently_flips_behavior():
    """读开关失败必须回默认（开）。

    这些开关存在的意义就是"出问题能立刻关掉而不发版"；如果读失败被当成"关掉"，
    一次数据库抖动就会悄悄改变系统行为——那比没有开关更危险。
    """
    for db in (_KvDB(boom=True), _KvDB("yes"), _KvDB({"enabled": "true"}),
               _KvDB({"other": 1})):
        assert asyncio.run(svc._resolve_intent_caliber(db)) is True
        assert asyncio.run(svc._resolve_memory_observe(db)) is True


def test_build_agent_context_keeps_intent_caliber_switch():
    agent = SimpleNamespace(context_schema=lambda: BaseContext())
    context = svc._build_agent_context(agent, {"intent_caliber": False})
    assert context.intent_caliber is False
    # 缺省为开：口径是新的默认行为，只有系统开关显式关掉才回到旧行为
    assert BaseContext().intent_caliber is True


def test_last_user_query_returns_latest_user_message():
    conv = SimpleNamespace(id=7)
    db = _SeqDB([[conv], ["浓硫酸怎么存放"]])
    assert asyncio.run(svc._last_user_query(db, "t1")) == "浓硫酸怎么存放"


def test_last_user_query_returns_empty_without_conversation():
    assert asyncio.run(svc._last_user_query(_SeqDB([[]]), "t1")) == ""


def test_last_user_query_swallows_db_errors():
    class _BoomDB:
        async def execute(self, *_args, **_kwargs):
            raise RuntimeError("db down")

    assert asyncio.run(svc._last_user_query(_BoomDB(), "t1")) == ""


def test_inject_memory_and_corrections_appends_both_blocks(monkeypatch):
    """注入顺序必须是 事实 → 个人资料 → 修正。

    个人资料排在事实之后，是因为它的风格说明要写"若上文 [当前可用事实] 里…"；
    修正压最后，因为它是结论依据的最高层。顺序写反了，优先级说明就指向错误的位置。
    """
    async def fake_context(*_args, **_kwargs):
        return "记忆块"

    async def fake_corrections(*_args, **_kwargs):
        return "修正块", [{"ticket_id": 1, "score": 0.9}]

    monkeypatch.setattr(svc.MemoryService, "build_context", fake_context)
    monkeypatch.setattr(svc.CorrectionService, "build_correction_context_details", fake_corrections)

    input_context = {"system_prompt": "BASE"}
    # 查询顺序：① users(department_id, business_role) ② user_config(enable_memory, major, response_style)
    # 这里两项都取空值 → 没有个人资料可注入，断言聚焦于事实与修正两块。
    result = asyncio.run(svc._inject_memory_and_corrections(
        _SeqDB([[(None, None)], [(True, None, "normal")]]),
        uid="u1", thread_id="t1", query="浓硫酸怎么存放",
        kb_ids=["kb-1"], intent="graph_rag_query", input_context=input_context,
    ))
    assert input_context["system_prompt"] == "BASE\n\n[当前可用事实]\n记忆块\n\n[权威修正]\n修正块"
    assert result.memory_enabled is True
    assert result.injections == [{"ticket_id": 1, "score": 0.9}]


def test_inject_memory_and_corrections_places_profile_between_facts_and_corrections(monkeypatch):
    async def fake_context(*_args, **_kwargs):
        return "记忆块"

    async def fake_corrections(*_args, **_kwargs):
        return "修正块", []

    monkeypatch.setattr(svc.MemoryService, "build_context", fake_context)
    monkeypatch.setattr(svc.CorrectionService, "build_correction_context_details", fake_corrections)

    input_context = {"system_prompt": "BASE"}
    asyncio.run(svc._inject_memory_and_corrections(
        _SeqDB([[(7, "student")], [(True, "应用化学", "concise")]]),
        uid="u1", thread_id="t1", query="浓硫酸怎么存放",
        kb_ids=None, intent=None, input_context=input_context,
    ))
    prompt = input_context["system_prompt"]

    assert prompt.index("[当前可用事实]") < prompt.index("[个人资料]") < prompt.index("[权威修正]")
    profile = prompt.split("[个人资料]\n")[1].split("\n\n[权威修正]")[0]
    assert "身份：学生" in profile
    assert "专业：应用化学" in profile
    assert "回答风格：简单清晰" in profile
    # 冲突仲裁必须在段内说清楚，否则模型无从判断该听谁的
    assert PROFILE_PRECEDENCE_NOTE in profile
    # 学工号/姓名/性别不进 prompt：它们对回答没有帮助，只是白烧 token
    assert "学工号" not in prompt
    assert "u1" not in profile


def test_inject_memory_and_corrections_keeps_going_when_user_row_is_unavailable(monkeypatch):
    """取不到 users 行时，修正作用域按最严处理，但记忆与个人资料仍要正常注入。"""
    async def fake_context(*_args, **_kwargs):
        return "记忆块"

    async def fake_corrections(*_args, **_kwargs):
        return "修正块", []

    monkeypatch.setattr(svc.MemoryService, "build_context", fake_context)
    monkeypatch.setattr(svc.CorrectionService, "build_correction_context_details", fake_corrections)

    input_context = {"system_prompt": "BASE"}
    asyncio.run(svc._inject_memory_and_corrections(
        _SeqDB([[], [(True, "化学", "thorough")]]),
        uid="u1", thread_id="t1", query="浓硫酸怎么存放",
        kb_ids=None, intent=None, input_context=input_context,
    ))
    prompt = input_context["system_prompt"]
    assert "[当前可用事实]" in prompt
    assert "[个人资料]" in prompt
    assert "[权威修正]" in prompt
    # 身份拿不到就不编造：宁可缺一项
    assert "身份：" not in prompt
    assert "回答风格：详细严密" in prompt


def test_profile_major_suppresses_the_long_term_memory_fact(monkeypatch):
    """填了专业 → 长期记忆的 user.major 让位；没填 → 照常注入。

    这是「会话事实 > 个人资料 > 长期记忆」在代码里的落点：少了这一步，
    [当前可用事实] 和 [个人资料] 会给模型两个专业。
    """
    async def fake_context(_db, _uid, _thread, _enabled, query, suppressed_fact_keys=None):
        suppressed = suppressed_fact_keys or set()
        lines = []
        if "user.major" not in suppressed:
            lines.append("- [long_term] user.major: 化学工程")
        return "\n".join(lines) or "记忆块"

    async def fake_corrections(*_args, **_kwargs):
        return "", []

    monkeypatch.setattr(svc.MemoryService, "build_context", fake_context)
    monkeypatch.setattr(svc.CorrectionService, "build_correction_context_details", fake_corrections)

    filled = {"system_prompt": "BASE"}
    asyncio.run(svc._inject_memory_and_corrections(
        _SeqDB([[(7, "student")], [(True, "应用化学", "normal")]]),
        uid="u1", thread_id="t1", query="q", kb_ids=None, intent=None, input_context=filled,
    ))
    assert "专业：应用化学" in filled["system_prompt"]
    assert "user.major" not in filled["system_prompt"]

    empty = {"system_prompt": "BASE"}
    asyncio.run(svc._inject_memory_and_corrections(
        _SeqDB([[(7, "student")], [(True, None, "normal")]]),
        uid="u1", thread_id="t1", query="q", kb_ids=None, intent=None, input_context=empty,
    ))
    assert "user.major: 化学工程" in empty["system_prompt"]


def test_inject_memory_and_corrections_skips_empty_blocks(monkeypatch):
    async def fake_context(*_args, **_kwargs):
        return ""

    async def fake_corrections(*_args, **_kwargs):
        return "", []

    monkeypatch.setattr(svc.MemoryService, "build_context", fake_context)
    monkeypatch.setattr(svc.CorrectionService, "build_correction_context_details", fake_corrections)

    input_context = {"system_prompt": "BASE"}
    result = asyncio.run(svc._inject_memory_and_corrections(
        _SeqDB([[(None, None)], [(False, None, "normal")]]),
        uid="u1", thread_id="t1", query="浓硫酸怎么存放",
        kb_ids=None, intent=None, input_context=input_context,
    ))
    assert input_context["system_prompt"] == "BASE"
    assert result.memory_enabled is False
    assert result.injections == []


def test_inject_memory_and_corrections_survives_service_failures(monkeypatch):
    """记忆/修正服务不可用时不能阻塞主链路，与改动前行为一致。"""
    async def boom_context(*_args, **_kwargs):
        raise RuntimeError("memory down")

    async def boom_corrections(*_args, **_kwargs):
        raise RuntimeError("correction down")

    monkeypatch.setattr(svc.MemoryService, "build_context", boom_context)
    monkeypatch.setattr(svc.CorrectionService, "build_correction_context_details", boom_corrections)

    input_context = {"system_prompt": "BASE"}
    result = asyncio.run(svc._inject_memory_and_corrections(
        _SeqDB([[(None, None)], [(True, None, "normal")]]),
        uid="u1", thread_id="t1", query="浓硫酸怎么存放",
        kb_ids=None, intent=None, input_context=input_context,
    ))
    assert input_context["system_prompt"] == "BASE"
    assert result.injections == []
    # 服务挂了只影响它自己那一段：配置读到的开关值仍要如实回传
    assert result.memory_enabled is True
