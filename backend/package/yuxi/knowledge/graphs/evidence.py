"""Typed Graph RAG evidence objects.

User memory is intentionally represented separately from graph evidence.  Callers
may use ``GraphQueryContext.memory_hints`` to personalize wording, but must only
ground safety claims in ``EvidenceBundle`` items.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class GraphEvidence:
    """A traceable graph fact and its source chunk."""

    evidence_id: str
    subject: str
    predicate: str
    object: str
    chunk_id: str | None = None
    file_id: str | None = None
    page: int | None = None
    score: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class EvidenceBundle:
    """Bounded evidence passed to answer generation."""

    kb_id: str
    query: str
    items: tuple[GraphEvidence, ...] = ()
    paths: tuple[tuple[str, ...], ...] = ()
    degraded: bool = False
    degradation_reason: str | None = None

    @property
    def has_evidence(self) -> bool:
        return bool(self.items)

    def to_prompt_dict(self) -> dict[str, Any]:
        """Serialize only graph evidence; never include user memory content."""
        return {
            "kb_id": self.kb_id,
            "query": self.query,
            "degraded": self.degraded,
            "degradation_reason": self.degradation_reason,
            "items": [
                {
                    "evidence_id": item.evidence_id,
                    "subject": item.subject,
                    "predicate": item.predicate,
                    "object": item.object,
                    "chunk_id": item.chunk_id,
                    "file_id": item.file_id,
                    "page": item.page,
                    "score": item.score,
                    "metadata": item.metadata,
                }
                for item in self.items
            ],
            "paths": [list(path) for path in self.paths],
        }


@dataclass(frozen=True, slots=True)
class GraphQueryContext:
    """Query hints assembled before graph retrieval.

    ``memory_hints`` are non-authoritative personalization signals and are kept
    outside ``EvidenceBundle`` by design.
    """

    query: str
    domain_scope: str | None = None
    entity_hints: tuple[str, ...] = ()
    intent_hints: tuple[str, ...] = ()
    memory_hints: tuple[dict[str, Any], ...] = ()

    def retrieval_query(self) -> str:
        parts = [self.query.strip()]
        parts.extend(x.strip() for x in self.entity_hints if x.strip())
        return " ".join(dict.fromkeys(parts))


def build_query_context(
    query: str,
    *,
    domain_scope: str | None = None,
    entity_hints: list[str] | tuple[str, ...] | None = None,
    intent_hints: list[str] | tuple[str, ...] | None = None,
    memory_hints: list[dict[str, Any]] | tuple[dict[str, Any], ...] | None = None,
) -> GraphQueryContext:
    """Normalize bounded hints without allowing arbitrary prompt injection."""
    clean = lambda values: tuple(dict.fromkeys(str(v).strip()[:200] for v in (values or ()) if str(v).strip()))
    return GraphQueryContext(
        query=(query or "").strip()[:4000],
        domain_scope=(domain_scope or "").strip()[:100] or None,
        entity_hints=clean(entity_hints),
        intent_hints=clean(intent_hints),
        memory_hints=tuple(memory_hints or ())[:10],
    )
