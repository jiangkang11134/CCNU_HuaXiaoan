from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from yuxi.knowledge.graphs.extractors import (
    GraphExtractor,
    GraphExtractorFactory,
    LLMGraphExtractor,
    normalize_extraction_result,
)
from yuxi.knowledge.graphs.milvus_graph_service import MilvusGraphService


def _raw_graph_node(node_id: str, *, labels: list[str] | None = None, name: str | None = None) -> dict:
    return {
        "id": node_id,
        "labels": labels or ["MilvusKB", "Entity"],
        "properties": {"name": name or node_id, "kb_id": "kb_test"},
    }


def _raw_graph_edge(edge_id: str, source_id: str, target_id: str) -> dict:
    return {
        "id": edge_id,
        "type": "RELATED_TO",
        "source_id": source_id,
        "target_id": target_id,
        "properties": {},
    }


def test_normalize_extraction_result_defaults_and_validates_refs():
    result = normalize_extraction_result(
        {
            "entities": [{"text": "张三"}, {"text": "公司"}],
            "relations": [{"source": "张三", "target": "公司", "text": "任职于"}],
        },
        "llm",
    )

    assert result["entities"][0]["label"] == "Entity"
    assert result["relations"][0]["label"] == "RELATED_TO"
    assert result["relations"][0]["source"] == {"text": "张三", "label": "Entity", "attributes": []}
    assert result["metadata"] == {"extractor_type": "llm", "schema_version": 1}


def test_normalize_extraction_result_accepts_llm_nested_relation_entities():
    result = normalize_extraction_result(
        {
            "relations": [
                {
                    "source": {
                        "text": "张三",
                        "label": "Person",
                        "attributes": [{"text": "工程师", "label": "Occupation"}],
                    },
                    "target": {"text": "公司", "label": "Organization"},
                    "text": "任职于",
                    "label": "WORKS_AT",
                }
            ]
        },
        "llm",
    )

    assert result["entities"] == [
        {"text": "张三", "label": "Person", "attributes": [{"text": "工程师", "label": "Occupation"}]},
        {"text": "公司", "label": "Organization", "attributes": []},
    ]
    assert result["relations"][0]["source"]["attributes"] == [{"text": "工程师", "label": "Occupation"}]
    assert result["relations"][0]["target"] == {"text": "公司", "label": "Organization", "attributes": []}


@pytest.mark.parametrize(
    "payload",
    [
        {"entities": [{"text": "张三"}], "relations": [{"source": "张三", "target": "不存在", "text": "关系"}]},
        {"entities": [{"text": ""}], "relations": []},
    ],
)
def test_normalize_extraction_result_rejects_invalid_payload(payload):
    with pytest.raises(ValueError):
        normalize_extraction_result(payload, "llm")


def test_llm_graph_extractor_rejects_custom_prompt():
    extractor = LLMGraphExtractor({"model_spec": "test/model", "prompt": "custom"})

    with pytest.raises(ValueError, match="不支持自定义完整 Prompt"):
        extractor.validate_options()


def test_llm_graph_extractor_appends_schema_to_fixed_prompt():
    extractor = LLMGraphExtractor(
        {
            "model_spec": "test/model",
            "schema": "实体类型只能是 Person 或 Organization",
            "concurrency_count": 5,
            "model_params": {"temperature": 0.1},
        }
    )

    prompt = extractor._build_prompt("张三任职于公司")

    assert "请从下面文本中抽取实体和实体关系" in prompt
    assert "抽取 Schema 约束" in prompt
    assert "实体类型只能是 Person 或 Organization" in prompt
    assert "文本：\n张三任职于公司" in prompt


def test_graph_extractor_factory_supports_only_llm():
    assert GraphExtractorFactory.supported_types() == ["llm"]


def test_graph_extractor_factory_rejects_spacy():
    with pytest.raises(ValueError, match="spacy"):
        GraphExtractorFactory.create("spacy", {"model": "zh_core_web_sm"})


@pytest.mark.asyncio
async def test_milvus_graph_service_configure_rejects_spacy():
    kb = SimpleNamespace(kb_type="milvus", additional_params={})

    class Repo:
        async def get_by_kb_id(self, kb_id):
            return kb

        async def update(self, kb_id, data):
            raise AssertionError("unsupported extractor should not be persisted")

    service = MilvusGraphService(kb_repo=Repo())

    with pytest.raises(ValueError, match="不支持的图谱抽取器类型"):
        await service.configure(
            "kb_test",
            extractor_type="spacy",
            extractor_options={"model": "zh_core_web_sm"},
            created_by="user_1",
        )


@pytest.mark.asyncio
async def test_milvus_graph_service_configure_persists_updated_concurrency():
    kb = SimpleNamespace(
        kb_type="milvus",
        additional_params={
            "graph_build_config": {
                "locked": True,
                "extractor_type": "llm",
                "extractor_options": {"model_spec": "test/model", "concurrency_count": 5},
            }
        },
    )

    class Repo:
        async def get_by_kb_id(self, kb_id):
            return kb

        async def update(self, kb_id, data):
            kb.additional_params = data["additional_params"]
            return kb

    chunk_repo = SimpleNamespace(
        count_by_kb_id=AsyncMock(return_value=0),
        count_graph_pending_by_kb_id=AsyncMock(return_value=0),
        count_graph_indexed_by_kb_id=AsyncMock(return_value=0),
    )
    graph_repo = SimpleNamespace(count_by_kb_id=AsyncMock(return_value=(3, 2)))
    service = MilvusGraphService(kb_repo=Repo(), chunk_repo=chunk_repo, graph_repo=graph_repo)

    await service.configure(
        "kb_test",
        extractor_type="llm",
        extractor_options={"model_spec": "test/model", "concurrency_count": 9},
        created_by="user_1",
    )
    status = await service.get_status("kb_test")

    assert status["config"]["extractor_options"]["concurrency_count"] == 9
    assert status["entity_count"] == 3
    assert status["relationship_count"] == 2


@pytest.mark.asyncio
async def test_milvus_graph_service_build_pending_chunks_skips_failed_chunks_in_next_batch(monkeypatch):
    kb = SimpleNamespace(
        kb_type="milvus",
        embedding_model_spec="test/embedding",
        additional_params={
            "graph_build_config": {
                "locked": True,
                "extractor_type": "llm",
                "extractor_options": {"model_spec": "test/model", "concurrency_count": 1},
            }
        },
    )
    chunks = [
        SimpleNamespace(
            chunk_id="chunk_fail",
            file_id="file_1",
            content="失败 chunk",
            extraction_result=None,
            chunk_index=1,
        ),
        SimpleNamespace(
            chunk_id="chunk_ok",
            file_id="file_1",
            content="成功 chunk",
            extraction_result=None,
            chunk_index=2,
        ),
    ]
    indexed_chunk_ids = set()
    observed_exclusions = []

    class KbRepo:
        async def get_by_kb_id(self, kb_id):
            return kb

    class ChunkRepo:
        async def count_graph_pending_by_kb_id(self, kb_id):
            return len([chunk for chunk in chunks if chunk.chunk_id not in indexed_chunk_ids])

        async def list_graph_pending_by_kb_id(self, kb_id, limit, *, exclude_chunk_ids=None):
            observed_exclusions.append(set(exclude_chunk_ids or set()))
            pending = [
                chunk
                for chunk in chunks
                if chunk.chunk_id not in indexed_chunk_ids and chunk.chunk_id not in (exclude_chunk_ids or set())
            ]
            return pending[:limit]

        async def update_extraction_result(self, chunk_id, extraction_result):
            return None

        async def mark_graph_indexed(self, chunk_id, ent_ids=None, tags=None):
            indexed_chunk_ids.add(chunk_id)

    class GraphRepo:
        async def upsert_chunk_graph(self, **kwargs):
            return None

    class GraphVectorStore:
        async def insert_missing_graph_records(self, **kwargs):
            return None

    class Extractor(GraphExtractor):
        """继承真实基类，而不是照着感觉手写一个替身。

        抽取链路上服务端还会调 `resolved_domain()`；替身没跟上时，这个用例
        会以「建图失败」的样子红掉，归因看起来像服务端的 bug，很难想到是替身。
        继承基类之后，接口新增会被替身自动继承。
        """

        extractor_type = "llm"

        async def extract(self, text, *, chunk_metadata=None):
            if chunk_metadata["chunk_id"] == "chunk_fail":
                raise RuntimeError("extract failed")
            return {"entities": [{"text": "成功实体"}], "relations": []}

    monkeypatch.setattr(GraphExtractorFactory, "create", lambda extractor_type, options: Extractor())
    # 写图要连真 Neo4j，而这个用例考的是「失败块下一轮不再被取到」，不是写图本身。
    # 不打桩的话它会以「Chunk 图谱构建失败」的样子红掉，归因看起来像批处理有 bug。
    # 这里刻意返回空实体：非空会让 seen_entity_names 非空，进而触发 P3 复核钩子去连库，
    # 那是另一个关注点，实体落库路径由本文件的其它用例覆盖。
    monkeypatch.setattr(
        MilvusGraphService,
        "write_chunk_graph",
        lambda self, kb_id, chunk, normalized_result: ([], []),
    )

    service = MilvusGraphService(
        kb_repo=KbRepo(),
        chunk_repo=ChunkRepo(),
        graph_repo=GraphRepo(),
        graph_vector_store=GraphVectorStore(),
    )
    result = await service.build_pending_chunks("kb_test", batch_size=1)

    assert result == {"kb_id": "kb_test", "success": 1, "failed": 1, "remaining": 1}
    assert indexed_chunk_ids == {"chunk_ok"}
    assert {"chunk_fail"} in observed_exclusions


def test_milvus_graph_service_writes_chunk_entity_and_relation():
    tx = MagicMock()
    session = MagicMock()
    session.__enter__.return_value = session
    session.execute_write.side_effect = lambda func: func(tx)
    driver = MagicMock()
    driver.session.return_value = session
    connection = SimpleNamespace(driver=driver)
    service = MilvusGraphService(neo4j_connection=connection)
    chunk = SimpleNamespace(
        chunk_id="chunk_1",
        file_id="file_1",
        kb_id="kb_test",
        chunk_index=1,
        content="张三任职于公司",
        start_char_pos=0,
        end_char_pos=8,
    )

    entities, triples = service.write_chunk_graph(
        "kb_test",
        chunk,
        normalize_extraction_result(
            {
                "relations": [
                    {
                        "source": {
                            "text": "张三",
                            "label": "Person",
                            "attributes": [{"text": "工程师", "label": "Occupation"}],
                        },
                        "target": {"text": "公司", "label": "Organization"},
                        "text": "任职于",
                        "label": "WORKS_AT",
                    }
                ],
            },
            "llm",
        ),
    )

    assert [entity["name"] for entity in entities] == ["张三", "公司"]
    assert {entity["label"] for entity in entities} == {"Person", "Organization"}
    assert triples[0]["relation_type"] == "WORKS_AT"
    queries = [call.args[0] for call in tx.run.call_args_list]
    assert any("MERGE (c:Chunk:MilvusKB:`kb_test`" in query for query in queries)
    assert any("MERGE (e:Entity:MilvusKB:`kb_test`" in query for query in queries)
    assert any("MERGE (source)-[r:RELATION" in query for query in queries)
    entity_call = next(call for call in tx.run.call_args_list if "MERGE (e:Entity" in call.args[0])
    assert entity_call.kwargs["attributes"] == '[{"text": "工程师", "label": "Occupation"}]'


def test_milvus_graph_service_process_query_result_keeps_complete_edges():
    service = MilvusGraphService()
    result = service._process_query_result(
        [
            {
                "h": _raw_graph_node("node-a"),
                "t": _raw_graph_node("node-b"),
                "r": _raw_graph_edge("edge-a-b", "node-a", "node-b"),
            }
        ],
        limit=2,
        kb_id="kb_test",
    )

    assert [node["id"] for node in result["nodes"]] == ["node-a", "node-b"]
    assert [edge["id"] for edge in result["edges"]] == ["edge-a-b"]


def test_milvus_graph_service_process_query_result_filters_edges_after_node_limit():
    service = MilvusGraphService()
    result = service._process_query_result(
        [
            {
                "h": _raw_graph_node("node-a"),
                "t": _raw_graph_node("node-b"),
                "r": _raw_graph_edge("edge-a-b", "node-a", "node-b"),
            }
        ],
        limit=1,
        kb_id="kb_test",
    )

    assert [node["id"] for node in result["nodes"]] == ["node-a"]
    assert result["edges"] == []


def test_milvus_graph_service_process_query_result_filters_edges_to_excluded_chunk_nodes():
    service = MilvusGraphService()
    result = service._process_query_result(
        [
            {
                "h": _raw_graph_node("entity-a"),
                "t": _raw_graph_node("chunk-a", labels=["MilvusKB", "Chunk"]),
                "r": _raw_graph_edge("edge-entity-chunk", "entity-a", "chunk-a"),
            }
        ],
        limit=2,
        kb_id="kb_test",
        exclude_chunk=True,
    )

    assert [node["id"] for node in result["nodes"]] == ["entity-a"]
    assert result["edges"] == []


def test_milvus_graph_service_process_query_result_clamps_negative_limit():
    service = MilvusGraphService()
    result = service._process_query_result(
        [
            {
                "h": _raw_graph_node("node-a"),
                "t": _raw_graph_node("node-b"),
                "r": _raw_graph_edge("edge-a-b", "node-a", "node-b"),
            }
        ],
        limit=-1,
        kb_id="kb_test",
    )

    assert result == {"nodes": [], "edges": []}


@pytest.mark.asyncio
async def test_milvus_graph_service_query_nodes_empty_kb_id():
    service = MilvusGraphService()
    result = await service.query_nodes(kb_id=None, keyword="test")
    assert result == {"nodes": [], "edges": []}


@pytest.mark.asyncio
async def test_milvus_graph_service_get_labels_empty_kb_id():
    service = MilvusGraphService()
    result = await service.get_labels(kb_id=None)
    assert result == []


@pytest.mark.asyncio
async def test_milvus_graph_service_get_stats_empty_kb_id():
    service = MilvusGraphService()
    result = await service.get_stats(kb_id=None)
    assert result == {"total_nodes": 0, "total_edges": 0, "entity_types": []}
