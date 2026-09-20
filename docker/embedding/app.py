"""Local embedding + rerank service (OpenAI-compatible).

Why this exists
---------------
The campus relay answers HTTP 503 for /v1/embeddings and /v1/rerank,
the siliconflow key is rejected, and the host has no public internet access. Without
a working embedding backend the RAG pipeline cannot vectorise the user query, so
every question fails and every evaluation fails.

This service runs BAAI/bge-m3 (1024 dims, matching the existing Milvus collections)
plus BAAI/bge-reranker-v2-m3 on CPU inside the app-network, so retrieval no longer
depends on any external endpoint.

Weights are fetched from ModelScope (~9.5 MB/s measured on this network) with a
HuggingFace mirror fallback (~1.1 MB/s).

Endpoints
---------
GET  /health          -> readiness probe
POST /v1/embeddings   -> {"model","input"} -> {"data":[{"embedding":[...]}]}
POST /v1/rerank       -> {"model","query","documents"} -> {"results":[{"index","relevance_score"}]}

Run `python app.py --download-only` to pre-fetch the weights and exit.
"""

import argparse
import os
import shutil
import sys
import time
import urllib.request

import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

EMBED_REPO = os.getenv("EMBED_REPO", "BAAI/bge-m3")
RERANK_REPO = os.getenv("RERANK_REPO", "BAAI/bge-reranker-v2-m3")
MODEL_ROOT = os.getenv("MODEL_CACHE_DIR") or "/models"
EMBED_BATCH_SIZE = int(os.getenv("EMBED_BATCH_SIZE", "16"))
RERANK_BATCH_SIZE = int(os.getenv("RERANK_BATCH_SIZE", "8"))

MODELSCOPE_URL = "https://www.modelscope.cn/models/{repo}/resolve/master/{path}"
HF_URL = "https://hf-mirror.com/{repo}/resolve/main/{path}"
USER_AGENT = "yuxi-local-embedding/1.0"

# Only the files sentence-transformers actually reads. Skipping onnx/, imgs/ and the
# ColBERT/sparse heads keeps the download at ~2.3 GB per model instead of far more.
MODEL_FILES = {
    "BAAI/bge-m3": [
        "config.json",
        "config_sentence_transformers.json",
        "sentence_bert_config.json",
        "modules.json",
        "pytorch_model.bin",
        "tokenizer.json",
        "tokenizer_config.json",
        "special_tokens_map.json",
        "sentencepiece.bpe.model",
        "1_Pooling/config.json",
    ],
    "BAAI/bge-reranker-v2-m3": [
        "config.json",
        "model.safetensors",
        "tokenizer.json",
        "tokenizer_config.json",
        "special_tokens_map.json",
        "sentencepiece.bpe.model",
    ],
}

app = FastAPI(title="yuxi-local-embedding", version="1.0.0")

_embedder = None
_reranker = None


def local_dir(repo_id: str) -> str:
    return os.path.join(MODEL_ROOT, repo_id.replace("/", "__"))


def _fetch(url: str, dest: str) -> int:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=300) as response, open(dest, "wb") as handle:
        shutil.copyfileobj(response, handle, length=1024 * 1024)
    return os.path.getsize(dest)


def download_model(repo_id: str) -> str:
    """Download the required files, trying ModelScope first and HF mirror second."""
    target = local_dir(repo_id)
    files = MODEL_FILES.get(repo_id)
    if files is None:
        raise RuntimeError(f"no file manifest for {repo_id}")

    for path in files:
        destination = os.path.join(target, path)
        if os.path.exists(destination) and os.path.getsize(destination) > 0:
            continue

        os.makedirs(os.path.dirname(destination), exist_ok=True)
        started = time.time()
        for label, template in (("modelscope", MODELSCOPE_URL), ("hf-mirror", HF_URL)):
            url = template.format(repo=repo_id, path=path)
            try:
                size = _fetch(url, destination)
                print(
                    f"[download] {repo_id}/{path} <- {label} "
                    f"{size / 1e6:.1f} MB in {time.time() - started:.1f}s",
                    flush=True,
                )
                break
            except Exception as exc:  # try the next mirror
                print(f"[download] {label} failed for {path}: {type(exc).__name__}: {exc}", flush=True)
                if os.path.exists(destination):
                    os.remove(destination)
        else:
            raise RuntimeError(f"could not download {repo_id}/{path} from any mirror")

    return target


def get_embedder():
    global _embedder
    if _embedder is None:
        from sentence_transformers import SentenceTransformer

        started = time.time()
        path = download_model(EMBED_REPO)
        _embedder = SentenceTransformer(path, device="cpu", trust_remote_code=True)
        print(f"[embed] loaded {EMBED_REPO} in {time.time() - started:.1f}s", flush=True)
    return _embedder


def get_reranker():
    global _reranker
    if _reranker is None:
        from sentence_transformers import CrossEncoder

        started = time.time()
        path = download_model(RERANK_REPO)
        _reranker = CrossEncoder(path, device="cpu", max_length=512, trust_remote_code=True)
        print(f"[rerank] loaded {RERANK_REPO} in {time.time() - started:.1f}s", flush=True)
    return _reranker


class EmbeddingRequest(BaseModel):
    model: str | None = None
    input: str | list[str]


class RerankRequest(BaseModel):
    model: str | None = None
    query: str
    documents: list[str]
    top_n: int | None = None
    max_chunks_per_doc: int | None = None


@app.get("/health")
def health():
    return {
        "status": "ok",
        "embed_model": EMBED_REPO,
        "rerank_model": RERANK_REPO,
        "embed_loaded": _embedder is not None,
        "rerank_loaded": _reranker is not None,
    }


@app.post("/v1/embeddings")
def embeddings(request: EmbeddingRequest):
    texts = [request.input] if isinstance(request.input, str) else list(request.input)
    if not texts:
        raise HTTPException(status_code=400, detail="input must not be empty")

    vectors = get_embedder().encode(
        texts,
        normalize_embeddings=True,
        batch_size=EMBED_BATCH_SIZE,
        show_progress_bar=False,
        convert_to_numpy=True,
    )

    return {
        "object": "list",
        "data": [
            {"object": "embedding", "index": index, "embedding": vector.tolist()}
            for index, vector in enumerate(vectors)
        ],
        "model": request.model or EMBED_REPO,
        "usage": {"prompt_tokens": 0, "total_tokens": 0},
    }


@app.post("/v1/rerank")
def rerank(request: RerankRequest):
    if not request.documents:
        return {"model": request.model or RERANK_REPO, "results": []}

    pairs = [[request.query, document] for document in request.documents]
    scores = get_reranker().predict(pairs, batch_size=RERANK_BATCH_SIZE)

    results = [
        {"index": index, "relevance_score": float(score), "document": None}
        for index, score in enumerate(scores)
    ]
    results.sort(key=lambda item: item["relevance_score"], reverse=True)
    if request.top_n:
        results = results[: request.top_n]

    return {"model": request.model or RERANK_REPO, "results": results}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--download-only", action="store_true")
    options = parser.parse_args()

    if options.download_only:
        for repo in (EMBED_REPO, RERANK_REPO):
            target = download_model(repo)
            print(f"[download] {repo} -> {target}", flush=True)
        sys.exit(0)

    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8000")), workers=1)
