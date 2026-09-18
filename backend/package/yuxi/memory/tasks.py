"""记忆抽取的入队入口（记忆模块）。

回答链路只负责"入队"，不负责抽取本身。抽取要调 LLM、要读写事实状态机，
放在热路径上会让一次**已经生成好**的回答为它承担失败风险——旧围栏协议在
``chat_service`` 里只能 ``except Exception: logger.exception`` 把异常吞掉，
因为不能因为记忆写失败就把回答丢掉。
"""
from yuxi.services.run_queue_service import get_arq_pool


async def enqueue_turn_memory_extraction(thread_id: str) -> None:
    """流结束后入队一次记忆抽取。

    参数只有 ``thread_id``：任务自己按游标取"还没抽过的轮次"。因此
    - **重复入队安全**——第二次进来时游标已推进，直接返回 idle；
    - **被节流跳过的轮次不会丢**——下一次入队会连同它一起补上。
    这样入队方不需要知道任何游标状态，避免了"入队时算一遍、任务里再算一遍"的两份真相。
    """
    queue = await get_arq_pool()
    await queue.enqueue_job("extract_turn_memory", str(thread_id))
