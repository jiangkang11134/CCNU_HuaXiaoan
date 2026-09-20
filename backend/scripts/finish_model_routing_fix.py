"""Finish the model-routing fix in one shot and write a report.

Steps: backup + rewrite base.toml -> restart api/worker -> verify through the
application's own code paths (not raw curl) -> dump everything to a report file.
"""

import asyncio
import io
import json
import os
import re
import shutil
import subprocess
import sys
import time

CONFIG = "/root/Yuxi/docker/volumes/yuxi/config/base.toml"
COMPOSE_DIR = "/root/Yuxi"
REPORT = "/tmp/final_report.txt"

TARGETS = {
    "default_model": "ccnu:GLM-5.2-W4A8",
    "fast_model": "ccnu:moonshotai/Kimi-K2.6",
    "embed_model": "local-embed:bge-m3",
    "reranker": "local-embed:bge-reranker-v2-m3",
    "content_guard_llm_model": "ccnu:GLM-5.2-W4A8",
}

lines = []


def say(text=""):
    lines.append(str(text))


def run(cmd, timeout=300):
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
    return (result.stdout or "") + (result.stderr or "")


# ---------------------------------------------------------------- 1. config
say("=" * 60)
say("STEP 1  rewrite base.toml")
say("=" * 60)

if not os.path.exists(CONFIG):
    say(f"FATAL: {CONFIG} not found")
    io.open(REPORT, "w", encoding="utf-8").write("\n".join(lines))
    sys.exit(1)

backup = f"{CONFIG}.bak-{time.strftime('%Y%m%d%H%M%S')}"
shutil.copy2(CONFIG, backup)
say(f"backup -> {backup}")

text = io.open(CONFIG, encoding="utf-8").read()

for key, value in TARGETS.items():
    pattern = re.compile(rf'^{key}\s*=\s*".*?"\s*$', re.MULTILINE)
    replacement = f'{key} = "{value}"'
    if pattern.search(text):
        old = pattern.search(text).group(0)
        if old == replacement:
            say(f"  {key}: already correct")
        else:
            text = pattern.sub(replacement, text)
            say(f"  {key}: {old.strip()}  ->  {replacement}")
    else:
        # key absent entirely (embed_model historically was); insert after 'reranker'
        anchor = re.search(r'^reranker\s*=\s*".*?"\s*$', text, re.MULTILINE)
        if anchor:
            text = text[: anchor.end()] + "\n" + replacement + text[anchor.end() :]
            say(f"  {key}: INSERTED -> {replacement}")
        else:
            text = replacement + "\n" + text
            say(f"  {key}: prepended -> {replacement}")

io.open(CONFIG, "w", encoding="utf-8").write(text)

say("")
say("--- base.toml head ---")
for line in io.open(CONFIG, encoding="utf-8").read().splitlines()[:7]:
    say("  " + line)

# ---------------------------------------------------------------- 2. restart
say("")
say("=" * 60)
say("STEP 2  restart api + worker")
say("=" * 60)
out = run(f"cd {COMPOSE_DIR} && docker compose -f docker-compose.prod.yml restart api worker", timeout=420)
say(out.strip()[-600:])

say("waiting for health...")
for _ in range(40):
    time.sleep(5)
    status = run("docker ps --format '{{.Names}} {{.Status}}' | grep api-prod").strip()
    if "healthy" in status:
        say("  " + status)
        break
else:
    say("  still not healthy: " + status)

say("")
say(run("docker ps --format '{{.Names}}\t{{.Status}}' | grep -E 'embedding|api-prod|worker-prod'").strip())

# ---------------------------------------------------------------- 3. verify
say("")
say("=" * 60)
say("STEP 3  verify through the application's own code")
say("=" * 60)

VERIFY = r'''
import asyncio, json

async def main():
    from yuxi.config import config
    from yuxi.models.providers.cache import model_cache

    print("[config as loaded by the app]")
    for key in ("default_model", "fast_model", "embed_model", "reranker", "content_guard_llm_model"):
        print("   ", key, "=", getattr(config, key, None))

    print()
    print("[spec resolution]")
    for key in ("default_model", "fast_model", "embed_model", "reranker", "content_guard_llm_model"):
        spec = getattr(config, key, None)
        info = model_cache.get_model_info(spec) if spec else None
        print("   ", key, "->", "OK" if info else "NOT FOUND", spec)

    print()
    print("[embedding model test]")
    try:
        from yuxi.models.embed import select_embedding_model
        model = select_embedding_model(config.embed_model)
        ok, msg = await model.test_connection()
        print("    ", "PASS" if ok else "FAIL", msg)
    except Exception as exc:
        print("     FAIL", type(exc).__name__, str(exc)[:200])

    print()
    print("[rerank model test]")
    try:
        from yuxi.models.rerank import get_reranker
        reranker = get_reranker(config.reranker)
        ok, msg = await reranker.test_connection()
        print("    ", "PASS" if ok else "FAIL", msg)
    except Exception as exc:
        print("     FAIL", type(exc).__name__, str(exc)[:200])

    print()
    print("[chat model test]")
    try:
        from yuxi.models.chat import select_model
        model = select_model(config.default_model)
        result = await model.call("用一句话说明浓硫酸稀释的正确操作", stream=False)
        print("     PASS", str(getattr(result, "content", result))[:120])
    except Exception as exc:
        print("     FAIL", type(exc).__name__, str(exc)[:200])

    print()
    print("[content guard test]")
    try:
        from yuxi.utils.guard import content_guard
        blocked = await content_guard.check("实验室里怎么稀释浓硫酸？")
        print("     PASS (no config error), blocked =", blocked)
    except Exception as exc:
        print("     FAIL", type(exc).__name__, str(exc)[:200])

asyncio.run(main())
'''

tmp_script = "/tmp/_verify.py"
io.open(tmp_script, "w", encoding="utf-8").write(VERIFY)
say(run(f"docker cp {tmp_script} api-prod:/tmp/_verify.py && docker exec api-prod python3 /tmp/_verify.py 2>&1 | grep -v 'INFO\\|DEBUG\\|WARNING'", timeout=600))

# ---------------------------------------------------------------- 4. summary
say("")
say("=" * 60)
say("SUMMARY")
say("=" * 60)
say(run("docker logs api-prod --tail 60 2>&1 | grep -iE 'error|exception|traceback' | tail -10") or "  (no errors in api logs)")

io.open(REPORT, "w", encoding="utf-8").write("\n".join(lines))
print("report written to " + REPORT)
