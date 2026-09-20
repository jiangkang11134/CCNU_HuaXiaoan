"""会话事实 / 长期记忆的生命周期与上下文渲染（记忆模块）。

纠错工单（自进化）的检索/门控用例已拆到
``test/unit/self_evolution/test_correction_helpers.py``。
"""
import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from yuxi.memory.observation import (
    AUTO_CONFIRM_KEYS,
    MEMORY_FACT_KEYS,
    MEMORY_FACT_TTL_DAYS,
    arbitrate,
    memory_fact_ttl,
)
from yuxi.memory.service import SESSION_FACT_TTL, SESSION_FACT_TTL_DAYS, MemoryService
from yuxi.memory.state_machine import transition
from yuxi.storage.postgres.models_business import UserMemoryFact


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def test_session_fact_ttl_is_2_days():
    assert SESSION_FACT_TTL_DAYS == 2
    assert SESSION_FACT_TTL.days == 2


def test_session_fact_state_machine_supports_expiry():
    assert transition("active", "expired", memory=False) == "expired"
    with pytest.raises(ValueError):
        transition("superseded", "active", memory=False)

def test_memory_state_machine_supports_expiry_and_rejects_invalid_reactivation():
    assert transition("candidate", "expired") == "expired"
    assert transition("pending_confirmation", "expired") == "expired"
    with pytest.raises(ValueError):
        transition("retracted", "confirmed")


def test_graph_evidence_serialization_never_contains_memory_hints():
    # evidence 模块会连带拉起 knowledge 包的重型解析依赖（docling 等）；
    # 用 importorskip 让本文件在缺少这些依赖的本地环境下仍可整体运行。
    evidence = pytest.importorskip("yuxi.knowledge.graphs.evidence")
    context = evidence.build_query_context(
        "乙醇如何储存",
        entity_hints=["乙醇"],
        memory_hints=[{"fact_key": "user.major", "content": "化学"}],
    )
    bundle = evidence.EvidenceBundle(
        kb_id="kb-1",
        query=context.retrieval_query(),
        items=(evidence.GraphEvidence("e-1", "乙醇", "REQUIRES_STORAGE", "阴凉通风", chunk_id="c-1"),),
    )

    serialized = bundle.to_prompt_dict()
    assert serialized["items"][0]["chunk_id"] == "c-1"
    assert "memory_hints" not in serialized
    assert "user.major" not in str(serialized)

class _SeqResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows

    def scalar(self):
        return self._rows[0] if self._rows else None

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None


class _SeqDB:
    """按调用顺序依次返回预置结果的假 DB，覆盖 build_context 的多次查询。"""

    def __init__(self, batches):
        self._batches = list(batches)

    async def execute(self, *_args, **_kwargs):
        return _SeqResult(self._batches.pop(0) if self._batches else [])


def _memory_fact(fact_key, content, graph_query_role=None):
    return SimpleNamespace(
        fact_key=fact_key, content=content, tags=[], entity_hints=[], intent_hints=[],
        graph_query_role=graph_query_role, domain_scope=None, updated_at=_now(),
    )


def test_build_context_renders_session_facts_with_safety_disclaimer():
    session = _memory_fact("major", "化学")
    # 长期记忆按与 query 的词元重叠排序，零重叠且非 personalize 角色的会被丢弃
    memory = _memory_fact("lab", "浓硫酸存放于有机合成实验室")

    async def run():
        return await MemoryService.build_context(
            _SeqDB([[session], [memory]]), "u1", "t1", True, "浓硫酸怎么存放")

    text = asyncio.run(run())
    assert "- [session] major: 化学" in text
    assert "- [long_term] lab: 浓硫酸存放于有机合成实验室" in text
    assert "不得作为实验室安全规范" in text


def _build_ctx_text(session_facts, memories, query="浓硫酸怎么存放", enabled=True,
                    **kwargs):
    async def run():
        return await MemoryService.build_context(
            _SeqDB([session_facts, memories]), "u1", "t1", enabled, query, **kwargs)

    return asyncio.run(run())


def test_build_context_drops_long_term_facts_overridden_by_the_profile():
    """个人资料填了专业，长期记忆里的 `user.major` 必须让位。

    冲突仲裁是「会话事实 > 个人资料 > 长期记忆」。只声明不执行的话，模型会同时
    看到「专业：应用化学」和 `[long_term] user.major: 化学工程` 两个答案，
    等于把仲裁权又推回给模型——而模型没有依据判断该信哪个。
    """
    # personalize 角色：否则零词元重叠会被排序逻辑直接丢掉，测不到抑制
    text = _build_ctx_text(
        [], [_memory_fact("user.major", "化学工程", graph_query_role="personalize")],
        suppressed_fact_keys={"user.major"},
    )
    assert "user.major" not in text
    assert "化学工程" not in text


def test_build_context_suppression_never_reaches_session_facts():
    """会话事实是最高层——即使键名相同也绝不能被个人资料压制。

    这条防的是"顺手把 project.* 也丢进 suppressed"，那会直接颠倒用户定的顺序。
    """
    text = _build_ctx_text(
        [_memory_fact("project.constraints", "实验室无通风橱")],
        [_memory_fact("user.major", "化学工程", graph_query_role="personalize")],
        suppressed_fact_keys={"user.major", "project.constraints"},
    )
    assert "- [session] project.constraints: 实验室无通风橱" in text
    assert "user.major" not in text


def test_build_context_keeps_overridden_facts_when_profile_is_silent():
    """个人资料没填时，长期记忆照常注入——抑制是"被覆盖才让位"，不是永久屏蔽。"""
    text = _build_ctx_text(
        [], [_memory_fact("user.major", "化学工程", graph_query_role="personalize")])
    assert "- [long_term] user.major: 化学工程" in text


def test_build_context_leads_with_knowledge_base_primacy_clause():
    """用户口径（硬原则）：记忆、偏好一律不得违背事实与文档权威内容，一切以知识库为主。

    这条边界目前**只靠 `build_context` 注入的那段文本**守着——没有任何结构化校验能拦住
    模型把记忆当安全证据用（白名单管的是"能记什么"，管不了"能用它推什么"）。
    所以文本本身必须被钉死：哪天有人重构这个函数、顺手改了措辞或删掉后半句，
    边界就消失了，而且**不会有任何用例失败**。

    "安全结论必须来自知识库"是「以知识库为主」这句话**唯一**落在代码里的地方，
    比前半句更该断言；前半句只说"不能用它当证据"，后半句才说"该信谁"。
    """
    text = _build_ctx_text([_memory_fact("major", "化学")], [])
    assert text.startswith("以下内容仅用于个性化")
    assert "不得作为实验室安全规范、化学品性质或合规结论的证据" in text
    # 完整三级排序，缺任何一级都会让模型在不同注入段之间拿到相互矛盾的权威顺序
    assert "已批准并重新入库的修正 > 知识库/Graph RAG 可追溯证据 > 本段记忆与偏好" in text
    # 声明必须在事实**之前**：模型对开头的权重高于末尾，附在后面等于没写
    assert text.index("不得作为") < text.index("[session]")
    # 排序里第三级必须覆盖"偏好"，不能只提"记忆"——偏好是最容易被当成结论依据的
    assert "记忆与偏好" in text


def test_build_context_with_only_session_facts_still_carries_the_clause():
    """只有会话事实时同样要带：约束条件（如"没有通风橱"）一样不能被当成规范依据。"""
    text = _build_ctx_text([_memory_fact("project.constraints", "实验室无通风橱")], [])
    assert "已批准并重新入库的修正 > 知识库" in text
    assert "实验室无通风橱" in text


def test_build_context_omits_everything_when_no_facts():
    """没有事实时返回空串，不要留下一条孤零零的免责声明。"""
    assert _build_ctx_text([], [], query="随便问点什么") == ""


def test_build_context_does_not_touch_redis(monkeypatch):
    """回归：build_context 曾有被 `if not query` 短路的死缓存。

    key 按 query 哈希、TTL 30s，在自然对话里命中率接近 0；启用后反而会让本轮刚
    提取的会话事实最多 30s 不可见。因此移除缓存，任何 Redis 访问都视为回归。
    若将来确需缓存，应改为「会话级缓存 + 事实写入时主动失效」，并同步改本用例。
    """
    import sys
    import types

    import yuxi.memory.service as svc

    redis_mod = types.ModuleType("yuxi.storage.redis")

    async def _boom():
        raise AssertionError("build_context 不应访问 Redis")

    redis_mod.get_async_redis_client = _boom
    monkeypatch.setitem(sys.modules, "yuxi.storage.redis", redis_mod)

    session = _memory_fact("major", "化学")

    async def run():
        return await MemoryService.build_context(
            _SeqDB([[session], []]), "u1", "t1", False, "浓硫酸怎么存放")

    assert "[session] major: 化学" in asyncio.run(run())


# ---------------------------------------------------------------------------
# 分键有效期（2026-09-20）：研究方向 30 天、工作环境 7 天，**再次提及即续期**
# ---------------------------------------------------------------------------


class _MemoryDB:
    """只服务 ``_apply_memory_fact`` 的假 DB：一次查询 + add/flush + 审计事件。

    ``flush`` 必须给新行补一个 id：函数建行后立刻 flush，紧接着写审计事件，
    事件里的 ``target_id`` 取自 ``row.id``，真实库由自增序列填，这里不补就 ``int(None)``。
    """

    def __init__(self, existing=None):
        self._existing = existing
        self.added = []

    async def execute(self, *_args, **_kwargs):
        return _SeqResult([self._existing] if self._existing is not None else [])

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        for index, obj in enumerate(self.added):
            if getattr(obj, "id", None) is None:
                obj.id = 900 + index


def _memory_row(fact_key, content, *, status="confirmed", expires_at=None, row_id=501):
    """造一条既有长期记忆。

    ``row_id`` 必须给：``_apply_memory_fact`` 的续期/取代分支会把 ``row.id`` 写进审计事件，
    真实库里它是自增主键、绝不会是 None，假数据留空就只能在 ``int(None)`` 处炸。
    """
    return UserMemoryFact(
        id=row_id, uid="u1", fact_key=fact_key, content=content, status=status,
        confidence=0.9, expires_at=expires_at,
    )


def _new_memory_row(db):
    return next(o for o in db.added if isinstance(o, UserMemoryFact))


def test_memory_fact_ttl_is_per_key():
    """TTL 是分键的而不是全局的；这两个数字是用户明确给的口径，别随手改。"""
    assert MEMORY_FACT_TTL_DAYS == {
        "user.research_direction": 30,
        "user.work_environment": 7,
    }
    assert memory_fact_ttl("user.research_direction") == timedelta(days=30)
    assert memory_fact_ttl("user.work_environment") == timedelta(days=7)
    # 不在表里 = 不过期（``None`` 落库成 NULL 列，SQL 侧就是"永不过期"）
    assert memory_fact_ttl("user.major") is None
    assert memory_fact_ttl("user.response_preference") is None


def test_memory_ttl_keys_are_whitelisted():
    """TTL 表里写了白名单没有的键（笔误）会静默不生效——没有任何地方会报错。"""
    unknown = sorted(k for k in MEMORY_FACT_TTL_DAYS if k not in MEMORY_FACT_KEYS)
    assert not unknown, f"TTL 表里出现了白名单外的键: {unknown}"


def test_work_environment_is_whitelisted_and_auto_confirmed():
    """工作环境要"抽得出来"且"抽出来就能用"——缺任一条都等于记了也看不见。"""
    assert "user.work_environment" in MEMORY_FACT_KEYS
    assert "user.work_environment" in AUTO_CONFIRM_KEYS
    assert arbitrate("user.work_environment", 0.9) == "confirmed"


def test_new_memory_fact_gets_expiry_from_the_ttl_map():
    db = _MemoryDB()

    async def run():
        return await MemoryService._apply_memory_fact(
            db, "u1", "user.research_direction", "锂电正极材料", 0.9, "confirmed",
            source="test", request_id=None,
        )

    assert asyncio.run(run()) == "created"
    row = _new_memory_row(db)
    # 允许几秒误差，但必须真的是 30 天量级（防"写成秒/小时"这类单位错）
    assert timedelta(days=29, hours=23) < row.expires_at - _now() <= timedelta(days=30)


def test_repeat_mention_pushes_expiry_forward():
    """「还在说」= 「还有效」：同内容再提一次要把 7 天重新计满。"""
    stale = _memory_row("user.work_environment", "在生化化学实验室",
                        expires_at=_now() + timedelta(hours=2))
    db = _MemoryDB(existing=stale)

    async def run():
        return await MemoryService._apply_memory_fact(
            db, "u1", "user.work_environment", "在生化化学实验室", 0.9, "confirmed",
            source="test", request_id=None,
        )

    assert asyncio.run(run()) == "renewed"
    assert stale.expires_at - _now() > timedelta(days=6, hours=23)
    # 续期必须留痕，否则"到底续没续"只能靠翻 expires_at 猜
    assert any(getattr(o, "event_type", None) == "renewed" for o in db.added)


def test_keys_without_ttl_never_get_an_expiry():
    """不过期的键不能被写成"某时刻过期"——那是把永久悄悄改成临时。"""
    row = _memory_row("user.major", "计算机", expires_at=None)
    db = _MemoryDB(existing=row)

    async def run():
        return await MemoryService._apply_memory_fact(
            db, "u1", "user.major", "计算机", 0.95, "confirmed",
            source="test", request_id=None,
        )

    assert asyncio.run(run()) == "skipped"
    assert row.expires_at is None


def test_changed_content_replaces_the_row_and_restarts_the_clock():
    """内容变了走 supersede 重建，新行天然拿到重新起算的有效期。"""
    old = _memory_row("user.research_direction", "锂电正极材料",
                      expires_at=_now() + timedelta(days=1))
    db = _MemoryDB(existing=old)

    async def run():
        return await MemoryService._apply_memory_fact(
            db, "u1", "user.research_direction", "钠离子电池", 0.9, "confirmed",
            source="test", request_id=None,
        )

    assert asyncio.run(run()) == "created"
    assert old.status == "superseded"
    row = _new_memory_row(db)
    assert row.content == "钠离子电池"
    assert row.expires_at - _now() > timedelta(days=29)
