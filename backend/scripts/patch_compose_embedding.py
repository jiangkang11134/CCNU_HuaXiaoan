"""Add the local embedding service to docker-compose.prod.yml (idempotent).

Two changes:
1. `embedding` is appended to the NO_PROXY / no_proxy lists of the api-worker env
   anchor, so that if a proxy is ever configured it cannot intercept traffic to the
   in-network embedding service.
2. An `embedding` service is inserted right before the `web` service.

Re-running this script is safe: both edits are skipped when already present.
"""

import io
import shutil
import sys
import time

COMPOSE_PATH = "/root/Yuxi/docker-compose.prod.yml"
SERVICE_NAME = "embedding"

SERVICE_BLOCK = """  # Local embedding + rerank service. Needed because the campus relay answers 503
  # for /v1/embeddings and the host has no public internet access; without a working
  # embedding backend the RAG pipeline cannot vectorise queries at all.
  embedding:
    build:
      context: ./docker/embedding
      dockerfile: Dockerfile
    image: yuxi-embed:local
    container_name: embedding-prod
    networks:
      - app-network
    volumes:
      # Model weights live outside the image so rebuilds do not re-download ~4.5 GB.
      - ${YUXI_DATA_DIR:-./docker/volumes}/embedding-models:/models
    environment:
      MODEL_CACHE_DIR: /models
      EMBED_BATCH_SIZE: "16"
      RERANK_BATCH_SIZE: "8"
      OMP_NUM_THREADS: "8"
      PORT: "8000"
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8000/health').read()"]
      interval: 30s
      timeout: 10s
      retries: 12
      start_period: 600s
    restart: unless-stopped

"""


def main() -> None:
    with io.open(COMPOSE_PATH, encoding="utf-8") as handle:
        text = handle.read()

    original = text
    notes = []

    # --- 1. NO_PROXY entries -------------------------------------------------
    changed_proxy = 0
    for key in ("NO_PROXY", "no_proxy"):
        needle = f"  {key}: localhost,127.0.0.1,milvus,graph,minio,milvus-etcd,etcd,mineru,paddlex,sandbox-provisioner"
        if f"{needle},{SERVICE_NAME},api.siliconflow.cn" in text:
            notes.append(f"{key}: already contains {SERVICE_NAME}")
            continue
        if needle + ",api.siliconflow.cn" in text:
            text = text.replace(needle + ",api.siliconflow.cn", f"{needle},{SERVICE_NAME},api.siliconflow.cn")
            changed_proxy += 1
            notes.append(f"{key}: added {SERVICE_NAME}")
        else:
            notes.append(f"{key}: anchor not found, skipped")

    # --- 2. service block ----------------------------------------------------
    if "\n  embedding:\n" in text:
        notes.append("service: already present")
    else:
        anchor = "\n  web:\n"
        if anchor not in text:
            notes.append("service: anchor '  web:' not found, ABORTING")
        else:
            text = text.replace(anchor, "\n" + SERVICE_BLOCK + "  web:\n", 1)
            notes.append("service: inserted before web")

    if text == original:
        print("no changes needed")
    else:
        backup = f"{COMPOSE_PATH}.bak-{time.strftime('%Y%m%d%H%M%S')}"
        shutil.copy2(COMPOSE_PATH, backup)
        print(f"[backup] {backup}")
        with io.open(COMPOSE_PATH, "w", encoding="utf-8") as handle:
            handle.write(text)
        print("written")

    for note in notes:
        print(f"  {note}")

    # --- verify --------------------------------------------------------------
    text = io.open(COMPOSE_PATH, encoding="utf-8").read()
    print("service block present:", "\n  embedding:\n" in text)
    print("NO_PROXY has embedding:", f"sandbox-provisioner,{SERVICE_NAME},api.siliconflow.cn" in text)
    try:
        import yaml

        parsed = yaml.safe_load(text)
        print("services:", list(parsed["services"].keys()))
        embedding = parsed["services"].get(SERVICE_NAME)
        if embedding:
            print("  image:", embedding.get("image"))
            print("  volumes:", embedding.get("volumes"))
            print("  networks:", embedding.get("networks"))
    except ImportError:
        print("(pyyaml not installed, skipped YAML parse)")
    sys.exit(0)


main()
