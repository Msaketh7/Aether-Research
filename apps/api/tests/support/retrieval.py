"""Chunks built in code, for the retrieval tests that need no database.

Fusion, reranking and the metrics are arithmetic over chunks, not over rows.
Building the chunks here keeps those tests honest about what they exercise -
and fast enough to assert properties over many orderings.

Ids are derived from the name with ``uuid5``, so an ordering assertion is
reproducible across processes: the tiebreaks in fusion and MMR are by id, and a
random id would make them flap.
"""

from __future__ import annotations

import uuid

from app.core.enums import DocumentFormat, SourceType
from app.retrieval.filters import ChunkView
from app.retrieval.results import RetrievedChunk, ScoredChunk
from app.sources.untrusted import UntrustedText

_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "aether/tests/retrieval")


def chunk_id(name: str) -> uuid.UUID:
    return uuid.uuid5(_NAMESPACE, name)


def view(
    name: str,
    *,
    text: str = "some text",
    document: str = "doc",
    source: str = "source",
    source_type: SourceType = SourceType.UPLOAD,
    embedded: bool = True,
) -> ChunkView:
    return ChunkView(
        id=chunk_id(name),
        document_id=chunk_id(document),
        source_id=chunk_id(source),
        source_type=source_type,
        chunk_index=0,
        text=UntrustedText(text, source_url="upload://fixture"),
        token_count=max(1, len(text.split())),
        char_start=0,
        char_end=len(text),
        page_start=None,
        page_end=None,
        section=None,
        format=DocumentFormat.TEXT,
        language="en",
        embedded=embedded,
    )


def scored(name: str, score: float = 1.0, *, text: str = "some text") -> ScoredChunk:
    return ScoredChunk(chunk=view(name, text=text), score=score)


def retrieved(name: str, score: float, *, text: str = "some text") -> RetrievedChunk:
    """A candidate as it reaches a reranker: already fused, not yet cut."""
    return RetrievedChunk(chunk=view(name, text=text), score=score, fusion_score=score)
