"""Point the runtime config at models that actually resolve.

Before this change every entry below pointed either at a disabled provider
(siliconflow-cn, whose key was also rejected) or at a model the campus relay
cannot serve, which produced two independent fatal errors:

1. content_guard_llm_model unresolvable -> ContentGuardConfigurationError ->
   chat_service returns an error chunk before the LLM is ever called, so the
   user never gets an answer.
2. embed_model unresolvable -> the query cannot be vectorised -> retrieval
   returns nothing -> no grounded answer, and every evaluation fails.

The relay's own embedding/rerank endpoints answer HTTP 503, so those two now
point at the container-local service instead.
"""

import io
import shutil
import sys
import time

CONFIG_PATH = "/app/saves/config/base.toml"

REPLACEMENTS = [
    # dsv4-0731 hangs (30s+ with no response) on the relay; GLM-5.2-W4A8 answers in ~0.5s.
    ('default_model = "ccnu:dsv4-0731"', 'default_model = "ccnu:GLM-5.2-W4A8"'),
    # qwen3.5 returns "upstream error" immediately; Kimi-K2.6 answers in ~0.14s.
    ('fast_model = "ccnu:qwen3.5"', 'fast_model = "ccnu:moonshotai/Kimi-K2.6"'),
    # Both siliconflow-cn entries were unresolvable (provider disabled, key rejected).
    (
        'reranker = "siliconflow-cn:BAAI/bge-reranker-v2-m3"',
        'reranker = "local-embed:bge-reranker-v2-m3"',
    ),
    (
        'embed_model = "siliconflow-cn:Pro/BAAI/bge-m3"',
        'embed_model = "local-embed:bge-m3"',
    ),
    (
        'content_guard_llm_model = "siliconflow-cn:deepseek-ai/DeepSeek-V4-Flash"',
        'content_guard_llm_model = "ccnu:GLM-5.2-W4A8"',
    ),
]


def main() -> None:
    with io.open(CONFIG_PATH, encoding="utf-8") as handle:
        text = handle.read()

    backup = f"{CONFIG_PATH}.bak-{time.strftime('%Y%m%d%H%M%S')}"
    shutil.copy2(CONFIG_PATH, backup)
    print(f"[backup] {backup}")

    applied, missing = [], []
    for old, new in REPLACEMENTS:
        if new in text:
            applied.append(f"already: {new}")
            continue
        if old not in text:
            missing.append(old)
            continue
        text = text.replace(old, new)
        applied.append(f"set: {new}")

    # embed_model has historically been absent from the file entirely.
    if "embed_model" not in text:
        anchor = 'reranker = "local-embed:bge-reranker-v2-m3"'
        if anchor in text:
            text = text.replace(anchor, anchor + '\nembed_model = "local-embed:bge-m3"')
            applied.append("inserted: embed_model")

    with io.open(CONFIG_PATH, "w", encoding="utf-8") as handle:
        handle.write(text)

    for line in applied:
        print(f"  {line}")
    if missing:
        print("  NOT FOUND (already changed by someone else?):")
        for line in missing:
            print(f"    {line}")

    print("--- resulting head ---")
    with io.open(CONFIG_PATH, encoding="utf-8") as handle:
        for index, line in enumerate(handle):
            if index >= 6:
                break
            print("   ", line.rstrip())
    sys.exit(0)


main()
