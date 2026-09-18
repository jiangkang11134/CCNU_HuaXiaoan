from yuxi.services.run_queue_service import get_arq_pool


async def enqueue_correction_graph_writeback(ticket_id: int, actor_uid: str | None = None):
    """P4：审核通过后异步把反馈改写为图块写入 Graph RAG。

    改写涉及 LLM + Milvus + Neo4j，耗时不可控，因此不放在审核请求里同步做。
    失败后工单 graph_written 保持 false，管理端可手动重试（POST /corrections/{id}/graph）。
    """
    queue = await get_arq_pool()
    await queue.enqueue_job("write_correction_graph", int(ticket_id), actor_uid)


async def enqueue_correction_preenrich(ticket_id: int):
    """建单后异步预富化 entity_hints。

    审批页要展示"这条修正会关联到哪些实体"，否则管理员只能盲批。实体链接本身
    不依赖终裁内容，所以建单时就能算；但它是 5 万条实体表的词面匹配，不放在
    点踩热路径上同步做。
    """
    queue = await get_arq_pool()
    await queue.enqueue_job("preenrich_correction", int(ticket_id))
