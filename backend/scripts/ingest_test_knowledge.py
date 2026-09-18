"""Upload and index the four local test PDFs through the Yuxi API.

Usage:
  python backend/scripts/ingest_test_knowledge.py --base-url http://127.0.0.1:5050 \
      --kb-id <id> --token <token> --model-spec <provider/model>

The script is intentionally API-driven so it exercises the same path as the
administrator UI. Existing duplicate content is rejected by the API; use a
fresh test knowledge base for another full run. Credentials are never stored.

域（--domain）走 `extractor_options.domain`，与配置接口的字段一致。
此前它被写进了 `extractor_type`，而抽取器工厂只注册了 `llm`，
所以这一步会直接抛"不支持的图谱抽取器类型"——脚本从来没能配置成功过。

单个文件想用不同的域时，用
`PUT /api/knowledge/databases/{kb_id}/graph-build/document-domain`
（`{"file_ids": [...], "doc_domain": "laboratory_management"}`）覆盖。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import requests


DEFAULT_DIR = Path(r"D:\华小安\测试知识库")


def request_json(session: requests.Session, method: str, url: str, **kwargs):
    response = session.request(method, url, timeout=120, **kwargs)
    if response.status_code >= 400:
        raise RuntimeError(f"{method} {url} -> {response.status_code}: {response.text[:1000]}")
    return response.json() if response.content else {}


def main() -> int:
    parser = argparse.ArgumentParser(description="Ingest local test PDFs into a Yuxi knowledge base")
    parser.add_argument("--base-url", default="http://127.0.0.1:5050", help="Yuxi API origin")
    parser.add_argument("--kb-id", required=True, help="Existing Milvus knowledge-base id")
    parser.add_argument("--token", required=True, help="Administrator Bearer token")
    parser.add_argument("--source-dir", type=Path, default=DEFAULT_DIR)
    parser.add_argument(
        "--domain",
        choices=("chemical", "laboratory_management"),
        default="chemical",
        help="图谱抽取域，写入 extractor_options.domain（单文件可用文件级接口覆盖）",
    )
    parser.add_argument(
        "--model-spec",
        required=True,
        help="图谱抽取用的模型，形如 provider/model；配置接口会校验它非空",
    )
    args = parser.parse_args()

    files = sorted(args.source_dir.glob("*.pdf"))
    if len(files) != 4:
        raise RuntimeError(f"expected exactly 4 PDFs in {args.source_dir}, found {len(files)}")

    base_url = args.base_url.rstrip("/")
    session = requests.Session()
    session.headers.update({"Authorization": f"Bearer {args.token}"})
    minio_items: list[str] = []
    content_hashes: dict[str, str] = {}

    for path in files:
        with path.open("rb") as stream:
            result = request_json(
                session,
                "POST",
                f"{base_url}/api/knowledge/files/upload",
                params={"kb_id": args.kb_id},
                files={"file": (path.name, stream, "application/pdf")},
            )
        item = result.get("minio_path") or result.get("file_path")
        if not item:
            raise RuntimeError(f"upload response has no MinIO path for {path.name}")
        minio_items.append(item)
        content_hashes[item] = result.get("content_hash", "")
        print(f"uploaded: {path.name}")

    add_result = request_json(
        session,
        "POST",
        f"{base_url}/api/knowledge/databases/{args.kb_id}/documents/add",
        json={
            "items": minio_items,
            "params": {
                "content_type": "file",
                "auto_index": False,
                "content_hashes": content_hashes,
            },
        },
    )
    print("added:", json.dumps(add_result, ensure_ascii=False))

    request_json(session, "POST", f"{base_url}/api/knowledge/databases/{args.kb_id}/documents/parse-pending")
    request_json(session, "POST", f"{base_url}/api/knowledge/databases/{args.kb_id}/documents/index-pending")
    print("parse/index tasks queued")

    graph = request_json(
        session,
        "POST",
        f"{base_url}/api/knowledge/databases/{args.kb_id}/graph-build/config",
        json={
            "extractor_type": "llm",
            "extractor_options": {"domain": args.domain, "model_spec": args.model_spec},
        },
    )
    print("graph configured:", json.dumps(graph, ensure_ascii=False))
    build = request_json(
        session,
        "POST",
        f"{base_url}/api/knowledge/databases/{args.kb_id}/graph-build/index",
        json={"batch_size": 20},
    )
    print("graph task queued:", json.dumps(build, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
