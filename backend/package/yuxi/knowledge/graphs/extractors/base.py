from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any, NamedTuple

from yuxi.knowledge.graphs.graph_utils import normalize_entity_name


class GraphExtractor(ABC):
    extractor_type: str

    def __init__(self, options: dict[str, Any] | None = None):
        self.options = options or {}

    @abstractmethod
    async def extract(self, text: str, *, chunk_metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        pass

    def validate_options(self) -> None:
        return None

    def resolved_domain(self, chunk_metadata: dict[str, Any] | None = None) -> str:
        """该块文本适用的抽取域。

        基类只认知识库级配置。支持文件级覆盖的抽取器重写这个方法——
        调用方（构建流程）不必知道覆盖规则长什么样。
        """
        from .domains import normalize_domain

        return normalize_domain(self.options.get("domain"))


def normalize_extraction_result(
    result: dict[str, Any],
    extractor_type: str,
    *,
    domain: object = None,
) -> dict[str, Any]:
    """归一化抽取结果；给了域就按该域的白名单过滤。

    ``domain`` 为 None 或为空串表示通用抽取——不校验，因为此时确实没有本体可依。
    """
    if not isinstance(result, dict):
        raise ValueError("extraction_result 必须是对象")

    entities = result.get("entities") or []
    relations = result.get("relations") or []
    if not isinstance(entities, list) or not isinstance(relations, list):
        raise ValueError("extraction_result.entities 和 relations 必须是数组")

    normalized_entities_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    entity_refs: dict[str, dict[str, Any]] = {}

    def add_entity(entity: Any, path: str) -> dict[str, Any]:
        normalized_entity = _normalize_entity(entity, path)
        key = _entity_key(normalized_entity)
        existing = normalized_entities_by_key.get(key)
        if existing is None:
            normalized_entities_by_key[key] = normalized_entity
            existing = normalized_entity
        else:
            _merge_attributes(existing, normalized_entity)

        for ref in _entity_refs(entity, existing):
            entity_refs[ref] = existing
        return existing

    for index, entity in enumerate(entities):
        add_entity(entity, f"entities[{index}]")

    normalized_relations = []
    for index, relation in enumerate(relations):
        if not isinstance(relation, dict):
            raise ValueError("relations 元素必须是对象")
        source = _normalize_relation_endpoint(
            relation.get("source"),
            entity_refs,
            add_entity,
            result,
            f"relations[{index}].source",
        )
        target = _normalize_relation_endpoint(
            relation.get("target"),
            entity_refs,
            add_entity,
            result,
            f"relations[{index}].target",
        )
        text = str(relation.get("text") or "").strip()
        if not text:
            raise ValueError("relations[].text 不能为空")
        normalized_relations.append(
            {
                "source": source,
                "target": target,
                "text": text,
                "label": str(relation.get("label") or "RELATED_TO").strip() or "RELATED_TO",
            }
        )

    verdict = _apply_ontology(list(normalized_entities_by_key.values()), normalized_relations, domain)

    metadata = dict(result.get("metadata") or {})
    metadata.setdefault("extractor_type", extractor_type)
    metadata.setdefault("schema_version", 1)
    if verdict.domain:
        # 只在**真在校验**的时候写这段。通用域下没有白名单，
        # 报一个 dropped=0 会让人误以为"校验过了"，那是假的安心。
        metadata["ontology"] = {
            "domain": verdict.domain,
            "dropped_entities": verdict.dropped_entities,
            "dropped_relations": verdict.dropped_relations,
            "unknown_labels": verdict.unknown_labels,
        }
    return {
        "entities": verdict.entities,
        "relations": verdict.relations,
        "metadata": metadata,
    }


class OntologyVerdict(NamedTuple):
    """白名单过滤的结果。

    ``domain`` 为空串表示**没有校验**（通用抽取）——调用方据此区分
    "校验通过、零丢弃"和"压根没校验"，这两件事在指标上必须分开看。
    """

    domain: str
    entities: list[dict[str, Any]]
    relations: list[dict[str, Any]]
    dropped_entities: int
    dropped_relations: int
    unknown_labels: list[str]


def _apply_ontology(entities: list[dict[str, Any]],
                    relations: list[dict[str, Any]],
                    domain: object) -> OntologyVerdict:
    """按域白名单过滤，并如实报告丢掉了什么。

    **丢，而不是收下**：本体的全部价值在于图上的 label 是可枚举、可查询的。
    把模型现编的 label 一并入库，本体就只是 prompt 里的一段装饰文字，
    而且这种"退化"在图上完全看不出来。

    **丢，但不静默**：计数与具体 label 名一并回传，随 chunk 的 extraction_result
    落库，再配合调用方的 WARNING 日志，才能回答"本体到底拦掉了多少"。
    这里刻意不抛异常——一个越界 label 不该让整块文本的抽取全部报废。

    **关系跟着实体一起丢**：实体被摘掉后关系还留着，图上就会出现指向不存在节点的
    悬空边，PPR 遍历时是纯粹的噪声。
    """
    from .domains import domain_ontology, normalize_domain

    canonical = normalize_domain(domain)
    allowed_labels, allowed_relations = domain_ontology(canonical)
    if not allowed_labels and not allowed_relations:
        return OntologyVerdict(canonical, entities, relations, 0, 0, [])

    label_set = set(allowed_labels)
    relation_set = set(allowed_relations)
    unknown: list[str] = []

    def note(label: str) -> None:
        if label not in unknown:
            unknown.append(label)

    kept_entities: list[dict[str, Any]] = []
    survivors: set[int] = set()
    for entity in entities:
        if entity["label"] in label_set:
            kept_entities.append(entity)
            survivors.add(id(entity))
        else:
            note(entity["label"])

    kept_relations: list[dict[str, Any]] = []
    for relation in relations:
        if relation["label"] not in relation_set:
            note(relation["label"])
            continue
        if id(relation["source"]) not in survivors or id(relation["target"]) not in survivors:
            continue
        kept_relations.append(relation)

    return OntologyVerdict(
        domain=canonical,
        entities=kept_entities,
        relations=kept_relations,
        dropped_entities=len(entities) - len(kept_entities),
        dropped_relations=len(relations) - len(kept_relations),
        unknown_labels=unknown,
    )


def _normalize_relation_endpoint(
    endpoint: Any,
    entity_refs: dict[str, dict[str, Any]],
    add_entity: Callable[[Any, str], dict[str, Any]],
    result: dict[str, Any],
    path: str,
) -> dict[str, Any]:
    if isinstance(endpoint, dict):
        return add_entity(endpoint, path)

    endpoint_ref = str(endpoint or "").strip()
    entity = entity_refs.get(endpoint_ref)
    if entity is None:
        raise ValueError(
            f"relations[].source/target 必须是实体对象，或引用 entities[].text/id，"
            f"未找到: {path}={endpoint_ref}, Result: {result}"
        )
    return entity


def _normalize_entity(entity: Any, path: str) -> dict[str, Any]:
    if not isinstance(entity, dict):
        raise ValueError(f"{path} 必须是对象")

    text = str(entity.get("text") or "").strip()
    if not text:
        raise ValueError(f"{path}.text 不能为空")

    attributes = entity.get("attributes") or []
    if not isinstance(attributes, list):
        raise ValueError(f"{path}.attributes 必须是数组")

    normalized_attributes = []
    for attribute in attributes:
        if not isinstance(attribute, dict):
            raise ValueError(f"{path}.attributes 元素必须是对象")
        attr_text = str(attribute.get("text") or "").strip()
        if not attr_text:
            continue
        normalized_attributes.append(
            {
                "text": attr_text,
                "label": str(attribute.get("label") or "Attribute").strip() or "Attribute",
            }
        )

    return {
        "text": text,
        "label": str(entity.get("label") or "Entity").strip() or "Entity",
        "attributes": normalized_attributes,
    }


def _entity_key(entity: dict[str, Any]) -> tuple[str, str]:
    return (normalize_entity_name(entity["text"]), entity["label"])


def _entity_refs(raw_entity: Any, entity: dict[str, Any]) -> list[str]:
    refs = [entity["text"]]
    if isinstance(raw_entity, dict):
        entity_id = str(raw_entity.get("id") or "").strip()
        if entity_id:
            refs.append(entity_id)
    return refs


def _merge_attributes(target: dict[str, Any], source: dict[str, Any]) -> None:
    known_attributes = {(attr["text"], attr["label"]) for attr in target.get("attributes") or []}
    for attribute in source.get("attributes") or []:
        attribute_key = (attribute["text"], attribute["label"])
        if attribute_key not in known_attributes:
            target.setdefault("attributes", []).append(attribute)
            known_attributes.add(attribute_key)
