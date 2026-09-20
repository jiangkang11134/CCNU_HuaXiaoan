"""记忆事实的校验/裁决/落库单测。

围栏剥离器（``MemoryFenceStripper``）已随协议下线而删除：抽取改成
**回答结束后由后台任务独立调一次模型**（见 ``test_memory_extraction.py``）。
这里只测服务端三道闸：白名单校验、状态裁决、按通道分表落库。
"""
from types import SimpleNamespace

import pytest

from yuxi.memory.observation import (
    CHANNEL_MEMORY,
    CHANNEL_SESSION,
    LONG_TERM_FACT_KEYS,
    MEMORY_FACT_KEYS,
    SESSION_FACT_KEYS,
    arbitrate,
    canonical_channel,
    normalize_op,
)
from yuxi.memory.service import MemoryService


# --------------------------------------------------------------------------
# 通道裁定：key 前缀决定进哪张表，模型无权决定
# --------------------------------------------------------------------------

def test_canonical_channel_splits_whitelist_by_prefix():
    assert canonical_channel("user.major") == CHANNEL_MEMORY
    assert canonical_channel("user.response_preference") == CHANNEL_MEMORY
    assert canonical_channel("project.phase_scope") == CHANNEL_SESSION
    assert canonical_channel("project.constraints") == CHANNEL_SESSION


def test_canonical_channel_covers_whole_whitelist_exactly_once():
    """白名单必须恰好被两个通道覆盖，不能有 key 掉在缝里（也由模块底部 assert 兜底）。"""
    assert SESSION_FACT_KEYS | LONG_TERM_FACT_KEYS == MEMORY_FACT_KEYS
    assert not (SESSION_FACT_KEYS & LONG_TERM_FACT_KEYS)


def test_canonical_channel_rejects_key_outside_whitelist():
    assert canonical_channel("user.hobby") is None
    assert canonical_channel("") is None


# --------------------------------------------------------------------------
# 校验
# --------------------------------------------------------------------------

def test_normalize_op_rejects_unknown_key_and_sensitive_content():
    assert normalize_op({"fact_key": "user.hobby", "content": "爬山", "confidence": 0.9}) is None
    assert normalize_op({"fact_key": "user.major", "content": "手机号 13800138000", "confidence": 0.9}) is None
    assert normalize_op("not a dict") is None
    assert normalize_op({"fact_key": "user.major", "content": ""}) is None


def test_normalize_op_clamps_confidence_and_length():
    key, content, confidence = normalize_op(
        {"fact_key": "user.major", "content": "化" * 500, "confidence": 9.9}
    )
    assert key == "user.major"
    assert len(content) == 200
    assert confidence == 1.0


# --------------------------------------------------------------------------
# 裁决
# --------------------------------------------------------------------------

def test_arbitrate_rejects_unknown_key():
    assert arbitrate("user.hobby", 0.99) == "rejected"


def test_arbitrate_confirms_stable_profile_only_at_high_confidence():
    assert arbitrate("user.research_direction", 0.9) == "confirmed"
    # 置信度不足的稳定画像不能自动确认，停在 candidate 等人确认
    assert arbitrate("user.research_direction", 0.6) == "candidate"


def test_arbitrate_keeps_project_context_as_candidate():
    """项目约束时效性强，即使高置信度也不自动确认。"""
    assert arbitrate("project.phase_scope", 0.99) == "candidate"


# --------------------------------------------------------------------------
# 回答 prompt：抽取纪律必须已经下线
# --------------------------------------------------------------------------

def _load_chatbot_prompt_module():
    """直接按文件路径加载 prompt.py。

    走包导入会拉起 ``yuxi.agents.buildin.chatbot`` 的整条依赖链（deepagents 等），
    本机依赖版本不齐；这里只测拼接逻辑，不需要那条链。
    """
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[3] / "package" / "yuxi" / "agents" / "buildin" / "chatbot" / "prompt.py"
    spec = importlib.util.spec_from_file_location("chatbot_prompt_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_build_prompt_no_longer_carries_observe_protocol():
    """回答链路不再背抽取纪律：无论开关如何，正文 prompt 里都不能出现围栏标记。

    这是改造的核心承诺——模型只管答题，抽取由独立调用承担。若这里出现
    ``yuxi-memory``，说明有人把协议又塞回了回答 prompt。
    """
    module = _load_chatbot_prompt_module()
    for observe in (True, False):
        prompt = module.build_prompt_with_context(
            SimpleNamespace(system_prompt="SP", memory_observe=observe)
        )
        assert "yuxi-memory" not in prompt
        assert "SP" in prompt


def test_build_prompt_still_appends_intent_caliber():
    """抽取下线不应顺手把 U1 口径也带走。"""
    module = _load_chatbot_prompt_module()
    prompt = module.build_prompt_with_context(
        SimpleNamespace(
            system_prompt="SP",
            request_intent="correction",
            request_intent_confidence=0.9,
            intent_caliber=True,
        )
    )
    assert module.CORRECTION_CALIBER.strip() in prompt


# --------------------------------------------------------------------------
# 落库：按显式通道分表，且**不再对穿**
# --------------------------------------------------------------------------

class _SeqResult:
    def __init__(self, row):
        self._row = row

    def scalar_one_or_none(self):
        return self._row

    def scalars(self):
        return self

    def all(self):
        return [self._row] if self._row else []


class _SeqDB:
    """按调用顺序喂查询结果的极简 AsyncSession 替身。

    ``flush`` 需要模拟真实数据库的主键回填，否则 ``_record_event`` 拿到
    ``target_id=None``（这是替身的缺陷，不是被测代码的问题）。
    """

    def __init__(self, results):
        self._results = list(results)
        self.added: list = []
        self.commits = 0
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
        self.commits += 1

    def kinds(self) -> list[str]:
        return [type(obj).__name__ for obj in self.added]


@pytest.mark.asyncio
async def test_session_op_writes_only_session_fact():
    """改造前的核心缺陷：一条 op 同时写两张表（偏好进了会话事实表）。

    现在通道是显式的，session 通道**只能**产出 SessionFact。
    """
    db = _SeqDB([None])
    stats = await MemoryService.apply_extracted_ops(
        db, "u1", "t1", [(CHANNEL_SESSION, "project.phase_scope", "只做第一步", 0.9, None)]
    )
    assert stats == {
        "session": 1, "memory": 0, "confirmed": 0, "renewed": 0, "rejected": 0, "skipped": 0,
    }
    # 事实 + 若干审计事件，关键是没有长期记忆
    assert db.kinds().count("SessionFact") == 1
    assert "UserMemoryFact" not in db.kinds()
    # 只查了一次（会话事实的旧行），没有去碰长期记忆表
    assert db._results == []


@pytest.mark.asyncio
async def test_memory_op_writes_only_long_term_memory():
    db = _SeqDB([None])
    stats = await MemoryService.apply_extracted_ops(
        db, "u1", "t1", [(CHANNEL_MEMORY, "user.major", "化学工程", 0.95, None)]
    )
    assert stats["memory"] == 1
    assert stats["session"] == 0
    assert "SessionFact" not in db.kinds()
    memory = next(o for o in db.added if type(o).__name__ == "UserMemoryFact")
    assert memory.status == "confirmed"  # 稳定画像 + 高置信度
    assert memory.content == "化学工程"


@pytest.mark.asyncio
async def test_unknown_channel_is_rejected_not_downgraded_to_memory():
    """通道出现第三种取值说明调用方绕过了 canonical_channel，必须拒绝。"""
    db = _SeqDB([])
    stats = await MemoryService.apply_extracted_ops(
        db, "u1", "t1", [("gt", "user.major", "化学工程", 0.95, None)]
    )
    assert stats["rejected"] == 1
    assert db.added == []


@pytest.mark.asyncio
async def test_memory_disabled_still_writes_session_fact():
    """用户关掉长期记忆，不该顺带关掉会话事实（设计文档 §2.3）。"""
    db = _SeqDB([None])
    stats = await MemoryService.apply_extracted_ops(
        db,
        "u1",
        "t1",
        [
            (CHANNEL_MEMORY, "user.major", "化学工程", 0.95, None),
            (CHANNEL_SESSION, "project.phase_scope", "只做第一步", 0.9, None),
        ],
        memory_enabled=False,
    )
    assert stats["session"] == 1
    assert stats["memory"] == 0
    assert stats["skipped"] == 1
    assert "SessionFact" in db.kinds()
    assert "UserMemoryFact" not in db.kinds()


@pytest.mark.asyncio
async def test_repeated_extraction_is_idempotent():
    """后台任务会被 ARQ 重试重放同一轮，同内容必须视为已存在而不新增。"""
    existing = SimpleNamespace(
        id=7, uid="u1", thread_id="t1", fact_key="project.phase_scope",
        content="只做第一步", status="active",
    )
    db = _SeqDB([existing])
    stats = await MemoryService.apply_extracted_ops(
        db, "u1", "t1", [(CHANNEL_SESSION, "project.phase_scope", "只做第一步", 0.9, None)]
    )
    assert stats["session"] == 0  # 没新建
    assert db.added == []
    assert existing.status == "active"  # 也没被误标 superseded


@pytest.mark.asyncio
async def test_changed_session_fact_supersedes_old_row():
    existing = SimpleNamespace(
        id=7, uid="u1", thread_id="t1", fact_key="project.phase_scope",
        content="只做第一步", status="active",
    )
    db = _SeqDB([existing])
    stats = await MemoryService.apply_extracted_ops(
        db, "u1", "t1", [(CHANNEL_SESSION, "project.phase_scope", "扩到第二步", 0.9, None)]
    )
    assert stats["session"] == 1
    assert existing.status == "superseded"
    assert db.kinds().count("SessionFact") == 1


@pytest.mark.asyncio
async def test_does_not_downgrade_confirmed_memory():
    """已确认的事实不能被低置信度的新抽取推翻。"""
    existing = SimpleNamespace(
        id=7, uid="u1", fact_key="user.major", content="化学工程",
        status="confirmed", confidence=1.0,
    )
    db = _SeqDB([existing])
    stats = await MemoryService.apply_extracted_ops(
        db, "u1", "t1", [(CHANNEL_MEMORY, "user.major", "材料科学", 0.6, None)]
    )
    assert stats["skipped"] == 1
    assert stats["memory"] == 0
    assert existing.status == "confirmed"
    assert existing.content == "化学工程"


@pytest.mark.asyncio
async def test_promotes_candidate_when_same_content_confirmed():
    existing = SimpleNamespace(
        id=7, uid="u1", fact_key="user.major", content="化学工程",
        status="candidate", confidence=0.6, confirmed_at=None, confirmed_by=None,
    )
    db = _SeqDB([existing])
    stats = await MemoryService.apply_extracted_ops(
        db, "u1", "t1", [(CHANNEL_MEMORY, "user.major", "化学工程", 0.95, None)]
    )
    assert existing.status == "confirmed"
    assert stats["confirmed"] == 1
    assert stats["skipped"] == 0
