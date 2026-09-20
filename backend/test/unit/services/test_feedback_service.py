from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from yuxi.self_evolution.service import CorrectionService
from yuxi.services import feedback_service as svc

from _fake_system_kv import fake_execute


class _FakeResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value

    def scalar(self):
        return self.value


class _FakeSession:
    """按调用顺序喂查询结果的极简 AsyncSession 替身；结果用尽后返回 None。"""

    def __init__(self, results, kv=None):
        self.results = list(results)
        self.kv = kv
        self.added = []
        self.committed = False
        self.rolled_back = False

    async def execute(self, query):
        return fake_execute(self, query, lambda: _FakeResult(self.results.pop(0) if self.results else None))

    async def get(self, _model, _key):
        raise AssertionError("SystemKV 不能按主键取：主键是整型 id，必须 select(...).filter(key == ...)")

    def add(self, item):
        self.added.append(item)

    async def commit(self):
        self.committed = True

    async def refresh(self, item):
        item.id = 9
        item.created_at = datetime(2026, 1, 2, 3, 4, 5)

    async def rollback(self):
        self.rolled_back = True


@pytest.mark.asyncio
async def test_submit_message_feedback_syncs_langfuse_score(monkeypatch: pytest.MonkeyPatch):
    message = SimpleNamespace(
        id=3,
        conversation_id=7,
        extra_metadata={"langfuse_trace_id": "trace-1"},
    )
    conversation = SimpleNamespace(id=7, uid="user-1")
    db = _FakeSession([message, conversation, None])
    calls = []

    monkeypatch.setattr(svc, "submit_user_feedback_score", lambda **kwargs: calls.append(kwargs) or True)

    result = await svc.submit_message_feedback_view(
        message_id=3,
        rating="like",
        reason=None,
        db=db,
        current_uid="user-1",
    )

    assert result == {
        "id": 9,
        "message_id": 3,
        "rating": "like",
        "reason": None,
        # 库里的 created_at 是 naive UTC（上面 refresh 塞的 datetime 无时区），
        # 序列化必须带 UTC 标记；裸 isoformat 会被前端当本地时间渲染（偏早 8 小时）。
        "created_at": "2026-01-02T03:04:05Z",
    }
    assert db.committed is True
    assert db.rolled_back is False
    assert calls == [
        {
            "trace_id": "trace-1",
            "feedback_id": 9,
            "message_id": 3,
            "conversation_id": 7,
            "uid": "user-1",
            "rating": "like",
            "reason": None,
        }
    ]


@pytest.mark.asyncio
async def test_submit_message_feedback_skips_langfuse_without_trace_id(monkeypatch: pytest.MonkeyPatch):
    message = SimpleNamespace(id=3, conversation_id=7, extra_metadata={})
    conversation = SimpleNamespace(id=7, uid="user-1")
    db = _FakeSession([message, conversation, None])
    calls = []

    monkeypatch.setattr(svc, "submit_user_feedback_score", lambda **kwargs: calls.append(kwargs) or True)

    result = await svc.submit_message_feedback_view(
        message_id=3,
        rating="dislike",
        reason="不相关",
        db=db,
        current_uid="user-1",
    )

    assert result["rating"] == "dislike"
    assert result["reason"] == "不相关"
    assert calls == []


def _dislike_setup(*, kv=None, query_text="浓硫酸怎么存放", business_role="faculty", role="user"):
    """点踩场景：message / conversation / 无历史反馈 / 用户 / 前一条 user 提问。

    默认用教师身份——工单是处置层，只有教师/管理员的点踩才自动进 P1–P4 管道。
    """
    message = SimpleNamespace(
        id=3,
        conversation_id=7,
        request_id="req-1",
        content="应存放于阴凉通风处",
        extra_metadata={},
    )
    conversation = SimpleNamespace(id=7, uid="user-1", thread_id="thread-1")
    user = SimpleNamespace(uid="user-1", role=role, business_role=business_role)
    return message, conversation, _FakeSession(
        [message, conversation, None, user, query_text], kv=kv)


@pytest.fixture(autouse=True)
def _stub_preenrich(monkeypatch: pytest.MonkeyPatch):
    """预富化入队会去连 ARQ/Redis，单测里一律打桩；返回已入队的工单号。"""
    from yuxi.self_evolution import tasks as memory_tasks

    enqueued: list[int] = []

    async def fake_enqueue(ticket_id):
        enqueued.append(ticket_id)

    monkeypatch.setattr(memory_tasks, "enqueue_correction_preenrich", fake_enqueue)
    return enqueued


@pytest.mark.asyncio
async def test_dislike_auto_creates_correction_ticket(monkeypatch: pytest.MonkeyPatch):
    """教师点踩必须落成待审核工单——这是自进化管道唯一的自动入口。"""
    _message, _conversation, db = _dislike_setup()
    captured: dict = {}

    async def fake_create(_db, uid, thread_id, original, proposed, **kwargs):
        captured.update(uid=uid, thread_id=thread_id, original=original,
                        proposed=proposed, kwargs=kwargs)
        return SimpleNamespace(id=42)

    monkeypatch.setattr(CorrectionService, "create_correction", fake_create)

    result = await svc.submit_message_feedback_view(
        message_id=3, rating="dislike", reason="废液不能这样处理",
        db=db, current_uid="user-1",
    )

    assert result["rating"] == "dislike"
    assert captured["uid"] == "user-1"
    assert captured["thread_id"] == "thread-1"
    assert captured["proposed"] == "废液不能这样处理"
    assert "【用户提问】" in captured["original"] and "浓硫酸怎么存放" in captured["original"]
    assert "【助手回答】" in captured["original"] and "阴凉通风" in captured["original"]
    assert captured["kwargs"]["target_type"] == "answer"
    assert captured["kwargs"]["target_id"] == "3"
    assert captured["kwargs"]["request_id"] == "req-1"
    assert captured["kwargs"]["kb_id"] is None


@pytest.mark.asyncio
async def test_dislike_links_ticket_id_back_to_feedback(monkeypatch: pytest.MonkeyPatch):
    """工单与反馈双向可见：反馈行要记住自己变成了哪张工单，供后续状态同步。"""
    _message, _conversation, db = _dislike_setup()

    async def fake_create(*_args, **_kwargs):
        return SimpleNamespace(id=42)

    monkeypatch.setattr(CorrectionService, "create_correction", fake_create)

    await svc.submit_message_feedback_view(
        message_id=3, rating="dislike", reason="废液不能这样处理",
        db=db, current_uid="user-1",
    )

    feedback_row = db.added[0]
    assert feedback_row.ticket_id == 42
    assert feedback_row.reporter_role == "faculty"
    assert "42" in feedback_row.processing_note


@pytest.mark.asyncio
async def test_admin_dislike_creates_ticket(monkeypatch: pytest.MonkeyPatch):
    """管理员点踩同样进工单（role 归一化后为 system_admin）。"""
    _message, _conversation, db = _dislike_setup(role="admin", business_role="student")
    called = []

    async def fake_create(*_args, **_kwargs):
        called.append(True)
        return SimpleNamespace(id=7)

    monkeypatch.setattr(CorrectionService, "create_correction", fake_create)

    await svc.submit_message_feedback_view(
        message_id=3, rating="dislike", reason="说法有误", db=db, current_uid="user-1")

    assert called == [True]
    assert db.added[0].reporter_role == "system_admin"
    assert db.added[0].ticket_id == 7


@pytest.mark.asyncio
async def test_student_dislike_stays_in_feedback_only(monkeypatch: pytest.MonkeyPatch):
    """反馈是收集层，工单是处置层：学生的点踩照常记录，但不进自进化管道。"""
    _message, _conversation, db = _dislike_setup(business_role="student")
    called = []

    async def fake_create(*_args, **_kwargs):
        called.append(True)
        return SimpleNamespace(id=1)

    monkeypatch.setattr(CorrectionService, "create_correction", fake_create)

    result = await svc.submit_message_feedback_view(
        message_id=3, rating="dislike", reason="答得太长了", db=db, current_uid="user-1")

    assert result["rating"] == "dislike"
    assert called == []
    feedback_row = db.added[0]
    assert feedback_row.reporter_role == "student"
    assert feedback_row.ticket_id is None
    assert feedback_row.processing_status == "backlog"


@pytest.mark.asyncio
async def test_like_does_not_create_ticket(monkeypatch: pytest.MonkeyPatch):
    _message, _conversation, db = _dislike_setup()
    called = []

    async def fake_create(*_args, **_kwargs):
        called.append(True)
        return SimpleNamespace(id=1)

    monkeypatch.setattr(CorrectionService, "create_correction", fake_create)

    await svc.submit_message_feedback_view(
        message_id=3, rating="like", reason=None, db=db, current_uid="user-1")

    assert called == []


@pytest.mark.asyncio
async def test_kill_switch_disables_auto_ticket(monkeypatch: pytest.MonkeyPatch):
    """SystemKV feedback_to_ticket.enabled=false 时整体关掉自动建单。"""
    _message, _conversation, db = _dislike_setup(
        kv=SimpleNamespace(value={"enabled": False}))
    called = []

    async def fake_create(*_args, **_kwargs):
        called.append(True)
        return SimpleNamespace(id=1)

    monkeypatch.setattr(CorrectionService, "create_correction", fake_create)

    await svc.submit_message_feedback_view(
        message_id=3, rating="dislike", reason="有问题", db=db, current_uid="user-1")

    assert called == []


@pytest.mark.asyncio
async def test_ticket_failure_does_not_break_dislike(monkeypatch: pytest.MonkeyPatch):
    """建单失败只能告警，绝不能让点踩本身失败。"""
    _message, _conversation, db = _dislike_setup()

    async def fake_create(*_args, **_kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(CorrectionService, "create_correction", fake_create)

    result = await svc.submit_message_feedback_view(
        message_id=3, rating="dislike", reason="有问题", db=db, current_uid="user-1")

    assert result["rating"] == "dislike"
    assert db.rolled_back is False


@pytest.mark.asyncio
async def test_create_correction_from_dislike_skips_empty_reason():
    """理由是工单的 proposed_content，为空就没有可审核的内容，直接跳过。"""
    message, conversation, db = _dislike_setup()
    assert await svc.create_correction_from_dislike(
        db=db, message=message, conversation=conversation,
        uid="user-1", reason="   ", reporter_role="faculty") is None


@pytest.mark.asyncio
async def test_create_correction_from_dislike_rejects_student(monkeypatch: pytest.MonkeyPatch):
    """角色门槛直接落在建单函数上，而不只依赖调用方——避免将来新入口绕过。"""
    message, conversation, db = _dislike_setup()
    called = []

    async def fake_create(*_args, **_kwargs):
        called.append(True)
        return SimpleNamespace(id=1)

    monkeypatch.setattr(CorrectionService, "create_correction", fake_create)

    assert await svc.create_correction_from_dislike(
        db=db, message=message, conversation=conversation,
        uid="user-1", reason="答错了", reporter_role="student") is None
    assert called == []


def test_extract_kb_id_handles_missing_evidence():
    assert svc._extract_kb_id({}) is None
    assert svc._extract_kb_id({"sources": "not-a-list"}) is None
    assert svc._extract_kb_id({"sources": [{"kb_id": "kb-9"}]}) == "kb-9"
    assert svc._extract_kb_id({"knowledge_evidence": [{"knowledge_id": "kb-7"}]}) == "kb-7"


@pytest.mark.asyncio
async def test_resolve_kb_id_prefers_evidence_over_agent_binding():
    """检索证据最准确；取不到才退回 Agent 绑定。"""
    conversation = SimpleNamespace(agent_id="lab-agent")
    db = _FakeSession([{"context": {"knowledges": ["kb-from-agent"]}}])
    assert await svc._resolve_ticket_kb_id(
        db, {"sources": [{"kb_id": "kb-from-evidence"}]}, conversation
    ) == "kb-from-evidence"


@pytest.mark.asyncio
async def test_resolve_kb_id_falls_back_to_single_agent_knowledge():
    """knowledge_evidence 目前没有写入方，所以主路径其实是 Agent 绑定。"""
    conversation = SimpleNamespace(agent_id="lab-agent")
    db = _FakeSession([{"context": {"knowledges": ["kb-1"]}}])
    assert await svc._resolve_ticket_kb_id(db, {}, conversation) == "kb-1"


@pytest.mark.asyncio
async def test_resolve_kb_id_keeps_global_when_agent_binds_multiple():
    """绑定多个库时无法判断这条修正属于哪个库，宁可留空也不猜——绑错库是静默错配。"""
    conversation = SimpleNamespace(agent_id="lab-agent")
    db = _FakeSession([{"context": {"knowledges": ["kb-1", "kb-2"]}}])
    assert await svc._resolve_ticket_kb_id(db, {}, conversation) is None


@pytest.mark.asyncio
async def test_resolve_kb_id_handles_missing_agent_and_broken_config():
    assert await svc._resolve_ticket_kb_id(
        _FakeSession([]), {}, SimpleNamespace(agent_id="")) is None
    # Agent 存在的但 config 结构不对 → 留空，不抛
    assert await svc._resolve_ticket_kb_id(
        _FakeSession([{"context": {"knowledges": "kb-1"}}]), {},
        SimpleNamespace(agent_id="a")) is None
    assert await svc._resolve_ticket_kb_id(
        _FakeSession([None]), {}, SimpleNamespace(agent_id="a")) is None


@pytest.mark.asyncio
async def test_dislike_enqueues_preenrichment(monkeypatch: pytest.MonkeyPatch, _stub_preenrich):
    """建单后要入队预富化：审批页靠它显示实体关联，否则管理员只能盲批。"""
    _message, _conversation, db = _dislike_setup()

    async def fake_create(*_args, **_kwargs):
        return SimpleNamespace(id=42)

    monkeypatch.setattr(CorrectionService, "create_correction", fake_create)

    await svc.submit_message_feedback_view(
        message_id=3, rating="dislike", reason="废液不能这样处理",
        db=db, current_uid="user-1",
    )

    assert _stub_preenrich == [42]


@pytest.mark.asyncio
async def test_review_approved_marks_feedback_resolved():
    """工单与反馈必须同步：管理员批了工单，来源反馈不能还挂在待复核。"""
    feedback = SimpleNamespace(ticket_id=42, processing_status="ready_for_review")
    db = _FakeSession([feedback])

    await CorrectionService._sync_feedback_status(
        db, SimpleNamespace(id=42, status="approved"), "admin-1", "认定有效")

    assert feedback.processing_status == "resolved"
    assert feedback.processed_by == "admin-1"
    assert feedback.processed_at is not None
    assert feedback.processing_note == "认定有效"


@pytest.mark.asyncio
async def test_review_rejected_marks_feedback_dismissed_with_default_note():
    feedback = SimpleNamespace(ticket_id=42, processing_status="ready_for_review")
    db = _FakeSession([feedback])

    await CorrectionService._sync_feedback_status(
        db, SimpleNamespace(id=42, status="rejected"), "admin-1", None)

    assert feedback.processing_status == "dismissed"
    assert "驳回" in feedback.processing_note


@pytest.mark.asyncio
async def test_sync_skips_manual_ticket_without_source_feedback():
    """管理端手工建的工单没有来源反馈，同步应当静默跳过而不是报错。"""
    db = _FakeSession([None])
    await CorrectionService._sync_feedback_status(
        db, SimpleNamespace(id=42, status="approved"), "admin-1", None)


@pytest.mark.asyncio
async def test_sync_failure_never_breaks_review():
    """同步只是记账，绝不能把审核结果一起赔进去。"""
    class _BoomSession(_FakeSession):
        async def execute(self, _query):
            raise RuntimeError("db down")

    await CorrectionService._sync_feedback_status(
        _BoomSession([]), SimpleNamespace(id=42, status="approved"), "admin-1", None)


def test_batch_process_includes_student_backlog():
    """所有人的反馈都应能流转：学生的 backlog 不能永远卡在初始态。"""
    assert "backlog" in svc.BATCH_PROCESSABLE_STATUSES


# ---------------------------------------------------------------------------
# 反馈处置（管理员侧）：学生反馈唯一的人工出口
#
# 学生的点踩不会自动建单，只会留在收集层。若管理员也不能改它的处置状态，
# "收集层"就只是个无底洞——这些用例钉的就是这个出口。
# ---------------------------------------------------------------------------

def _feedback_row(**overrides):
    row = SimpleNamespace(
        id=5, message_id=3, uid="user-1", rating="dislike",
        reason="废液不能这样处理", processing_status="backlog", priority=10,
        processing_note=None, processed_by=None, processed_at=None, ticket_id=None,
    )
    for key, value in overrides.items():
        setattr(row, key, value)
    return row


def _convert_setup(*, feedback=None, query_text="浓硫酸怎么存放"):
    """转工单场景的查询顺序：反馈行 → 消息 → 会话 → 前一条 user 提问。

    会话故意不给 agent_id，避免 _resolve_ticket_kb_id 再多查一次 Agent 绑定。
    """
    message = SimpleNamespace(
        id=3, conversation_id=7, request_id="req-1",
        content="应存放于阴凉通风处", extra_metadata={},
    )
    conversation = SimpleNamespace(id=7, uid="user-1", thread_id="thread-1", agent_id="")
    return _FakeSession([feedback or _feedback_row(), message, conversation, query_text])


def test_feedback_status_vocabulary_is_self_consistent():
    """状态词表是前后端唯一口径，不能出现"批处理会写但不在集合里"这种漂移。"""
    assert set(svc.BATCH_PROCESSABLE_STATUSES) <= set(svc.FEEDBACK_STATUSES)
    assert svc.FEEDBACK_TERMINAL_STATUSES == {"resolved", "dismissed"}
    # 工单终审后 _sync_feedback_status 写入的两个终态必须在册
    assert {"resolved", "dismissed"} <= set(svc.FEEDBACK_STATUSES)
    assert set(svc.FEEDBACK_STATUS_LABELS) == set(svc.FEEDBACK_STATUSES)


@pytest.mark.asyncio
async def test_update_feedback_records_actor_and_note():
    feedback = _feedback_row()
    db = _FakeSession([feedback])

    result = await svc.update_feedback_processing(
        db=db, feedback_id=5, actor_uid="admin-1",
        processing_status="ready_for_review", priority=70, processing_note="先看这条",
    )

    assert result is feedback
    assert feedback.processing_status == "ready_for_review"
    assert feedback.priority == 70
    assert feedback.processing_note == "先看这条"
    assert feedback.processed_by == "admin-1"
    # 中间态（排队/调优先级）不算"处置完成"，不写 processed_at
    assert feedback.processed_at is None
    assert db.committed is True


@pytest.mark.asyncio
async def test_update_feedback_stamps_processed_at_only_on_terminal_status():
    feedback = _feedback_row(processing_status="ready_for_review")
    db = _FakeSession([feedback])

    await svc.update_feedback_processing(
        db=db, feedback_id=5, actor_uid="admin-1", processing_status="resolved")

    assert feedback.processing_status == "resolved"
    assert feedback.processed_at is not None


@pytest.mark.asyncio
async def test_update_feedback_rejects_unknown_status():
    db = _FakeSession([_feedback_row()])

    with pytest.raises(HTTPException) as excinfo:
        await svc.update_feedback_processing(
            db=db, feedback_id=5, actor_uid="admin-1", processing_status="whatever")

    assert excinfo.value.status_code == 422
    assert db.committed is False


@pytest.mark.asyncio
@pytest.mark.parametrize(("raw", "expected"), [(-5, 0), (999, 100), (42, 42)])
async def test_update_feedback_clamps_priority(raw, expected):
    feedback = _feedback_row()
    db = _FakeSession([feedback])

    await svc.update_feedback_processing(
        db=db, feedback_id=5, actor_uid="admin-1", priority=raw)

    assert feedback.priority == expected


@pytest.mark.asyncio
async def test_update_feedback_404s_on_missing_row():
    db = _FakeSession([None])

    with pytest.raises(HTTPException) as excinfo:
        await svc.update_feedback_processing(db=db, feedback_id=404, actor_uid="admin-1")

    assert excinfo.value.status_code == 404


@pytest.mark.asyncio
async def test_convert_feedback_creates_ticket_and_links_it(monkeypatch: pytest.MonkeyPatch):
    """管理员转单：建工单 + 反馈行记住 ticket_id + 状态推进到 ticketed。"""
    db = _convert_setup()
    captured: dict = {}

    async def fake_create(_db, uid, thread_id, original, proposed, **kwargs):
        captured.update(uid=uid, thread_id=thread_id, original=original,
                        proposed=proposed, kwargs=kwargs)
        return SimpleNamespace(id=88)

    monkeypatch.setattr(CorrectionService, "create_correction", fake_create)

    feedback, ticket_id = await svc.convert_feedback_to_ticket(
        db=db, feedback_id=5, actor_uid="admin-1")

    assert ticket_id == 88
    assert feedback.ticket_id == 88
    assert feedback.processing_status == "ticketed"
    assert feedback.processed_by == "admin-1"
    assert "88" in feedback.processing_note
    assert captured["uid"] == "user-1"
    assert captured["thread_id"] == "thread-1"
    assert captured["proposed"] == "废液不能这样处理"
    assert "【用户提问】" in captured["original"] and "浓硫酸怎么存放" in captured["original"]
    assert "【助手回答】" in captured["original"] and "阴凉通风" in captured["original"]
    assert captured["kwargs"]["target_type"] == "answer"
    assert captured["kwargs"]["target_id"] == "3"
    assert captured["kwargs"]["scope"] is None


@pytest.mark.asyncio
async def test_convert_feedback_is_idempotent(monkeypatch: pytest.MonkeyPatch):
    """同一条点踩被点两次不该产出两张工单。"""
    db = _convert_setup(feedback=_feedback_row(ticket_id=42))
    called = []

    async def fake_create(*_args, **_kwargs):
        called.append(True)
        return SimpleNamespace(id=1)

    monkeypatch.setattr(CorrectionService, "create_correction", fake_create)

    _feedback, ticket_id = await svc.convert_feedback_to_ticket(
        db=db, feedback_id=5, actor_uid="admin-1")

    assert ticket_id == 42
    assert called == []


@pytest.mark.asyncio
async def test_convert_feedback_rejects_empty_reason():
    """reason 是 proposed_content；为空就没有可审核的内容。"""
    db = _convert_setup(feedback=_feedback_row(reason="   "))

    with pytest.raises(HTTPException) as excinfo:
        await svc.convert_feedback_to_ticket(db=db, feedback_id=5, actor_uid="admin-1")

    assert excinfo.value.status_code == 422


@pytest.mark.asyncio
async def test_convert_feedback_rejects_invalid_scope_instead_of_going_global(
    monkeypatch: pytest.MonkeyPatch,
):
    """非法 scope 必须报错，不能静默回落。

    normalize_scope 的缺省值是 kb_truth（全局生效），静默回落等于把一条
    可能只是"个人口味"的反馈放行到全体用户——这正是 M1 的红线。
    """
    db = _convert_setup()
    called = []

    async def fake_create(*_args, **_kwargs):
        called.append(True)
        return SimpleNamespace(id=1)

    monkeypatch.setattr(CorrectionService, "create_correction", fake_create)

    with pytest.raises(HTTPException) as excinfo:
        await svc.convert_feedback_to_ticket(
            db=db, feedback_id=5, actor_uid="admin-1", scope="kb_truth_typo")

    assert excinfo.value.status_code == 422
    assert called == []


@pytest.mark.asyncio
async def test_convert_feedback_passes_explicit_scope(monkeypatch: pytest.MonkeyPatch):
    """管理员看过内容后可以直接定作用域（create_correction 会记成 admin 来源）。"""
    db = _convert_setup()
    captured: dict = {}

    async def fake_create(_db, uid, thread_id, original, proposed, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(id=88)

    monkeypatch.setattr(CorrectionService, "create_correction", fake_create)

    await svc.convert_feedback_to_ticket(
        db=db, feedback_id=5, actor_uid="admin-1", scope="user_pref")

    assert captured["scope"] == "user_pref"


@pytest.mark.asyncio
async def test_convert_feedback_404s_when_feedback_missing():
    db = _FakeSession([None])

    with pytest.raises(HTTPException) as excinfo:
        await svc.convert_feedback_to_ticket(db=db, feedback_id=5, actor_uid="admin-1")

    assert excinfo.value.status_code == 404


@pytest.mark.asyncio
async def test_convert_feedback_404s_when_message_missing():
    """反馈行在、但它指向的消息没了（被清理）→ 不能静默建一张内容为空的工单。"""
    db = _FakeSession([_feedback_row(), None])

    with pytest.raises(HTTPException) as excinfo:
        await svc.convert_feedback_to_ticket(db=db, feedback_id=5, actor_uid="admin-1")

    assert excinfo.value.status_code == 404


@pytest.mark.asyncio
async def test_convert_feedback_enqueues_preenrichment(
    monkeypatch: pytest.MonkeyPatch, _stub_preenrich,
):
    """与自动路径一致：转单后要入队预富化，否则审批页看不到实体关联。"""
    db = _convert_setup()

    async def fake_create(*_args, **_kwargs):
        return SimpleNamespace(id=88)

    monkeypatch.setattr(CorrectionService, "create_correction", fake_create)

    await svc.convert_feedback_to_ticket(db=db, feedback_id=5, actor_uid="admin-1")

    assert _stub_preenrich == [88]
