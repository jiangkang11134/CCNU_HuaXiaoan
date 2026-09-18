"""入队入口的单测。

入队是"回答链路"与"抽取任务"之间唯一的接缝：链路只传 ``thread_id``，
任务自己按游标取轮次。这条接缝很容易在重构时被悄悄改坏（名字不匹配、
漏传 thread_id），而坏了之后**不会报错**，只是记忆不再更新。
"""
import pytest

from yuxi.memory import tasks


class _Queue:
    def __init__(self):
        self.jobs: list[tuple] = []

    async def enqueue_job(self, name, *args):
        self.jobs.append((name, args))


@pytest.mark.asyncio
async def test_enqueue_uses_registered_job_name_and_thread_id(monkeypatch):
    queue = _Queue()

    async def fake_get_pool():
        return queue

    monkeypatch.setattr(tasks, "get_arq_pool", fake_get_pool)

    await tasks.enqueue_turn_memory_extraction("thread-9")

    # 名字必须与 run_worker 注册的函数名一致，参数必须是线程 id（任务靠它取游标）
    assert queue.jobs == [("extract_turn_memory", ("thread-9",))]


@pytest.mark.asyncio
async def test_enqueue_propagates_queue_failure(monkeypatch):
    """入队失败要让调用方看见并自己决定是否吞掉。

    调用方（chat_service）用 try/except 包住它并只记日志：记忆晚一轮生效可以接受，
    但"入队失败"和"抽取失败"必须能被区分开，所以这里不能自己吞。
    """
    async def fake_get_pool():
        raise RuntimeError("redis down")

    monkeypatch.setattr(tasks, "get_arq_pool", fake_get_pool)

    with pytest.raises(RuntimeError):
        await tasks.enqueue_turn_memory_extraction("thread-9")
