"""Register (or update) the local embedding/rerank provider and refresh the model cache.

The campus relay returns HTTP 503 for /v1/embeddings and /v1/rerank, and the host
has no public internet access, so a container-local bge-m3 service is used instead.
Model ids sent by yuxi are ignored by the service, which always serves the model
configured through the MODEL_CACHE_DIR volume.
"""

import asyncio

from sqlalchemy import select

from yuxi.models.providers.cache import model_cache
from yuxi.models.providers.service import get_all_model_providers
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import ModelProvider

PROVIDER_ID = "local-embed"
EMBED_ENDPOINT = "http://embedding:8000/v1/embeddings"
RERANK_ENDPOINT = "http://embedding:8000/v1/rerank"

PAYLOAD = {
    "display_name": "Local Embedding Service",
    "provider_type": "openai",
    "base_url": "http://embedding:8000/v1",
    "embedding_base_url": EMBED_ENDPOINT,
    "rerank_base_url": RERANK_ENDPOINT,
    # get_reranker() rejects specs whose api_key is empty, so keep a placeholder.
    "api_key": "local-no-auth",
    "capabilities": ["embedding", "rerank"],
    "enabled_models": [
        {
            "id": "bge-m3",
            "type": "embedding",
            "display_name": "BGE-M3 (local, 1024d)",
            "dimension": 1024,
            "batch_size": 16,
            "enabled": True,
        },
        {
            "id": "bge-reranker-v2-m3",
            "type": "rerank",
            "display_name": "BGE-Reranker-v2-M3 (local)",
            "enabled": True,
        },
    ],
    "headers_json": {},
    "extra_json": {},
    "accounts_json": [],
    "is_enabled": True,
}


async def main() -> None:
    async with pg_manager.get_async_session_context() as db:
        result = await db.execute(select(ModelProvider).filter(ModelProvider.provider_id == PROVIDER_ID))
        row = result.scalar_one_or_none()

        if row is None:
            db.add(ModelProvider(provider_id=PROVIDER_ID, is_builtin=False, created_by="ops", **PAYLOAD))
            action = "INSERT"
        else:
            for key, value in PAYLOAD.items():
                setattr(row, key, value)
            action = "UPDATE"

        await db.commit()
        print(f"[provider] {action} {PROVIDER_ID}")

    async with pg_manager.get_async_session_context() as db:
        providers = await get_all_model_providers(db)
        model_cache.rebuild(providers)

    print(f"[cache] rebuilt, {len(model_cache.get_all_specs())} models total")
    for info in sorted(model_cache.get_all_specs(), key=lambda item: item.spec):
        if info.provider_id == PROVIDER_ID:
            print(f"    {info.spec:<38} {info.model_type:<10} dim={info.dimension} {info.base_url}")

    for spec in ("local-embed:bge-m3", "local-embed:bge-reranker-v2-m3"):
        found = model_cache.get_model_info(spec)
        print(f"[check] {spec} -> {'OK' if found else 'MISSING'}")


asyncio.run(main())
