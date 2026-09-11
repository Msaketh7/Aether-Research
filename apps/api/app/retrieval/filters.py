"""Narrowing a run's chunks before anything ranks them (TDD 8.2).

Phase 8's retrievers rank; this decides what they rank over. A filter is a
validated value, not a dict of keyword arguments, for the same reason tool
inputs are (``extra="forbid"``): a misspelt filter must fail loudly, not
quietly widen a search to the whole run.

Results carry the chunk text as ``UntrustedText`` (ADR 0011). A chunk read back
from the index is retrieved source content like any other, and the type is what
stops it from being interpolated into a prompt.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.enums import DocumentFormat, SourceType
from app.sources.untrusted import UntrustedText

#: Ceiling on any id list in a filter. A run holds at most a few hundred sources.
MAX_FILTER_IDS = 200


class ChunkFilter(BaseModel):
    """What a retriever may restrict a run's chunks to. Every field narrows."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: UUID
    source_types: tuple[SourceType, ...] = ()
    source_ids: tuple[UUID, ...] = Field(default=(), max_length=MAX_FILTER_IDS)
    document_ids: tuple[UUID, ...] = Field(default=(), max_length=MAX_FILTER_IDS)
    formats: tuple[DocumentFormat, ...] = ()
    #: ISO 639-1 codes, as ``documents.language`` stores them.
    languages: tuple[str, ...] = Field(default=(), max_length=20)
    #: Markdown heading path, matched as a prefix: ``"Guide"`` matches
    #: ``"Guide > Installation"``.
    section_prefix: str | None = Field(default=None, min_length=1, max_length=300)
    #: Chunks that touch any page in this range. Chunks without pages never match.
    page_from: int | None = Field(default=None, ge=1)
    page_to: int | None = Field(default=None, ge=1)
    #: Against the source's own publication date. Sources without one never match.
    published_after: datetime | None = None
    published_before: datetime | None = None
    #: ``True`` for chunks that have a vector, ``False`` for ones still waiting.
    embedded: bool | None = None

    @field_validator("languages")
    @classmethod
    def _language_codes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        codes = tuple(code.strip().lower() for code in value)
        for code in codes:
            if not (2 <= len(code) <= 3 and code.isascii() and code.isalpha()):
                raise ValueError(f"{code!r} is not an ISO 639 language code.")
        return codes

    @model_validator(mode="after")
    def _ranges(self) -> ChunkFilter:
        if (
            self.page_from is not None
            and self.page_to is not None
            and self.page_from > self.page_to
        ):
            raise ValueError("page_from must not be after page_to.")
        if (
            self.published_after is not None
            and self.published_before is not None
            and self.published_after > self.published_before
        ):
            raise ValueError("published_after must not be after published_before.")
        return self


@dataclass(frozen=True, slots=True)
class ChunkView:
    """A chunk as retrieval sees it: its text, where it came from, where it sits."""

    id: UUID
    document_id: UUID
    source_id: UUID
    source_type: SourceType
    chunk_index: int
    text: UntrustedText
    token_count: int
    #: Offsets into the document's normalised content.
    char_start: int
    char_end: int
    page_start: int | None
    page_end: int | None
    section: str | None
    format: DocumentFormat | None
    language: str | None
    embedded: bool
