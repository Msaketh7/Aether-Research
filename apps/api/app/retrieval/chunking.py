"""Splitting a document into retrievable chunks (TDD 8.1, ADR 0012).

LlamaIndex's `SentenceSplitter` decides where chunks end (ADR 0003): token
bounded, preferring paragraph and sentence boundaries, with an overlap so that a
sentence cut by one boundary is whole in the neighbouring chunk. For Markdown,
headings are split first with `MarkdownNodeParser`, so a chunk never straddles
two sections and carries the heading path it came from.

What this module adds is the property the evidence chain depends on: **every
chunk is an exact slice of the normalised text.** ``start`` and ``end`` are
offsets into ``documents.normalized_content``, and ``text[start:end]`` is the
chunk, character for character. That is checked for every chunk rather than
trusted, and so is coverage - every non-whitespace character of the document is
in some chunk - because an evidence span resolved through an offset that is
off by a few characters cites words the source never contained, and text that
falls between chunks can never be retrieved or cited at all.

Both checks run on every document, in production as well as in the tests,
because a splitter's shortcuts show up exactly here - in a table of contents of
dotted leaders, in unbroken text, in a header repeated on every page - and a
splitter that starts dropping text must fail loudly rather than quietly shrink
the index.

## Chunk size

512 tokens with a 64-token overlap, counted with the cl100k tokenizer LlamaIndex
ships. About 380 words: enough context that a passage can support a claim on its
own, small enough that the top ten retrieved chunks fit an evidence extractor's
prompt with room to spare, and far inside the 8k context of the embedding
model, so nothing is truncated before it is embedded. The overlap is an eighth
of the chunk. These are defaults, not measurements - Phase 8's retrieval
benchmark is where chunk size is measured against recall, and every chunk
records the settings that produced it so an index built under two
configurations is detectable rather than silently mixed.

LlamaIndex is imported inside the functions that use it. The import takes
several seconds, and the API process, which never chunks, should not pay it.
"""

from __future__ import annotations

import bisect
import functools
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from app.core.enums import DocumentFormat
from app.retrieval.errors import DocumentTooLarge, OffsetMismatch
from app.retrieval.parsed import PageSpan

#: Bumped when the splitting behaviour changes, so chunks built by two versions
#: are distinguishable in the index.
CHUNKER_VERSION = "sentence-v1"

#: The longest section label kept.
_MAX_SECTION_CHARS = 300

#: An ATX heading line: its level, and its text without a closing sequence.
_HEADING = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$")


@dataclass(frozen=True, slots=True)
class Chunk:
    index: int
    #: Offsets into the normalised text; ``text[start:end] == self.text``.
    start: int
    end: int
    text: str
    token_count: int
    #: PDF pages the chunk spans, from 1. ``None`` for formats without pages.
    page_start: int | None = None
    page_end: int | None = None
    #: The Markdown heading path, e.g. ``"Guide > Installation"``.
    section: str | None = None


@dataclass(frozen=True, slots=True)
class _Section:
    start: int
    end: int
    label: str | None


class Chunker:
    """Token-bounded, structure-aware, offset-exact chunking."""

    def __init__(
        self, *, chunk_size_tokens: int, chunk_overlap_tokens: int, max_chunks: int
    ) -> None:
        if not 0 <= chunk_overlap_tokens < chunk_size_tokens:
            raise ValueError("Chunk overlap must be non-negative and smaller than the chunk size.")
        if max_chunks < 1:
            raise ValueError("max_chunks must be at least 1.")
        self._size = chunk_size_tokens
        self._overlap = chunk_overlap_tokens
        self._max_chunks = max_chunks

    @property
    def label(self) -> str:
        """Recorded on every chunk, so an index built under different settings shows it."""
        return f"{CHUNKER_VERSION}/{self._size}/{self._overlap}"

    def count_tokens(self, text: str) -> int:
        return len(_tokenizer()(text))

    def chunk(
        self, text: str, *, fmt: DocumentFormat, pages: Sequence[PageSpan] = ()
    ) -> list[Chunk]:
        """Split ``text``. CPU-bound: call it from a thread in async code."""
        locate = _page_locator(pages)
        chunks: list[Chunk] = []
        for section in self._sections(text, fmt):
            for start, end in self._split(text, section):
                if len(chunks) >= self._max_chunks:
                    raise DocumentTooLarge(
                        f"That document splits into more than {self._max_chunks:,} chunks.",
                        context={"max_chunks": self._max_chunks},
                    )
                piece = text[start:end]
                page_start, page_end = locate(start, end)
                chunks.append(
                    Chunk(
                        index=len(chunks),
                        start=start,
                        end=end,
                        text=piece,
                        token_count=self.count_tokens(piece),
                        page_start=page_start,
                        page_end=page_end,
                        section=section.label,
                    )
                )
        verify_coverage(text, chunks)
        return chunks

    # --- internals -------------------------------------------------------

    @functools.cached_property
    def _splitter(self) -> Any:
        from llama_index.core.node_parser import SentenceSplitter

        return SentenceSplitter(chunk_size=self._size, chunk_overlap=self._overlap)

    def _sections(self, text: str, fmt: DocumentFormat) -> list[_Section]:
        """Heading-delimited sections for Markdown; one section for everything else."""
        whole = [_Section(0, len(text), None)]
        if fmt is not DocumentFormat.MARKDOWN:
            return whole

        from llama_index.core.node_parser import MarkdownNodeParser
        from llama_index.core.schema import Document

        contents = [
            content
            for node in MarkdownNodeParser().get_nodes_from_documents([Document(text=text)])
            if (content := node.get_content()).strip()
        ]
        spans = _align(text, contents)
        if spans is None:
            # Sections are an aid to chunking, not a requirement of it. If the
            # heading parser's view of the text cannot be aligned with the
            # text, chunk it as one section rather than refuse the document.
            return whole
        sections = [
            _Section(start, end, label)
            for (start, end), label in zip(spans, _section_labels(contents), strict=True)
        ]
        return sections or whole

    def _split(self, text: str, section: _Section) -> list[tuple[int, int]]:
        from llama_index.core.schema import Document

        body = text[section.start : section.end]
        pieces = [
            content
            for node in self._splitter.get_nodes_from_documents([Document(text=body)])
            if (content := node.get_content()).strip()
        ]
        spans = _align(body, pieces)
        if spans is None:
            raise OffsetMismatch(context={"stage": "chunking", "section_start": section.start})
        kept: list[tuple[int, int]] = []
        for start, end in spans:
            # Every real chunk reaches past the end of the one before it. A
            # placement that does not only arises in degenerate repetition - a
            # run of one character longer than a chunk - where storing it would
            # store characters the index already holds, at the same offsets.
            if kept and end <= kept[-1][1]:
                continue
            kept.append((start, end))
        return [(section.start + start, section.start + end) for start, end in kept]


def verify_coverage(text: str, chunks: Sequence[Chunk]) -> None:
    """Every chunk is an exact slice, and only whitespace falls between chunks."""
    covered_to = 0
    for chunk in chunks:
        if text[chunk.start : chunk.end] != chunk.text:
            raise OffsetMismatch(context={"stage": "slice", "chunk": chunk.index})
        if chunk.start > covered_to and text[covered_to : chunk.start].strip():
            raise OffsetMismatch(
                context={"stage": "coverage", "gap_start": covered_to, "gap_end": chunk.start}
            )
        covered_to = max(covered_to, chunk.end)
    if text[covered_to:].strip():
        raise OffsetMismatch(
            context={"stage": "coverage", "gap_start": covered_to, "gap_end": len(text)}
        )


def _align(haystack: str, pieces: Sequence[str]) -> list[tuple[int, int]] | None:
    """Where each piece sits in ``haystack``, given that they are consecutive.

    The splitter returns chunk *texts*. LlamaIndex also reports an offset for
    each, found by searching forward from the previous chunk for the first
    match - which is ambiguous wherever text repeats. In a run of dashes, or a
    header printed on every page, the first match is usually not the right one:
    the chunks pile up at the start and the rest of the document is left
    uncovered. (Found by the unbroken-text test, whose coverage check failed.)

    What is not ambiguous is how the splitter builds chunks: each starts no
    earlier than the previous one and no later than the first non-whitespace
    character after the previous one ends. So each piece is placed at its
    *last* occurrence inside that window. For text that does not repeat there
    is exactly one occurrence and this is it; for text that does, it is the
    placement that advances furthest, which keeps the whole document covered.
    ``None`` when a piece does not occur at all.
    """
    spans: list[tuple[int, int]] = []
    floor = 0
    ceiling = _next_text(haystack, 0)
    for piece in pieces:
        start = haystack.rfind(piece, floor, ceiling + len(piece))
        if start < 0:
            # Not where a consecutive chunk would be. Take the next occurrence
            # anyway; the coverage check then reports the gap it leaves.
            start = haystack.find(piece, floor)
        if start < 0:
            return None
        end = start + len(piece)
        spans.append((start, end))
        floor = start
        ceiling = _next_text(haystack, end)
    return spans


def _next_text(text: str, index: int) -> int:
    """The first non-whitespace position at or after ``index``."""
    while index < len(text) and text[index].isspace():
        index += 1
    return index


def _section_labels(contents: Sequence[str]) -> list[str | None]:
    """Each section's heading path, such as ``Guide > Installation``.

    Built from the headings themselves rather than from LlamaIndex's
    ``header_path``, which joins headings with "/" - so a heading containing a
    slash (a file path, "input/output") came back split into several levels.
    Found by ingesting this repository's own TDD, whose headings name source
    directories.
    """
    stack: list[tuple[int, str]] = []
    labels: list[str | None] = []
    for content in contents:
        match = _HEADING.match(content.lstrip().split("\n", 1)[0])
        if match:
            level = len(match.group(1))
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, match.group(2)))
        labels.append(" > ".join(title for _, title in stack)[:_MAX_SECTION_CHARS] or None)
    return labels


def _page_locator(
    pages: Sequence[PageSpan],
) -> Callable[[int, int], tuple[int | None, int | None]]:
    """A function from a chunk's offsets to the first and last page it touches."""
    if not pages:
        return lambda _start, _end: (None, None)
    starts = [page.start for page in pages]

    def locate(start: int, end: int) -> tuple[int | None, int | None]:
        first = max(0, bisect.bisect_right(starts, start) - 1)
        # A chunk that begins in the separator between two pages begins on the
        # second one.
        if pages[first].end <= start and first + 1 < len(pages):
            first += 1
        last = max(first, bisect.bisect_right(starts, end - 1) - 1)
        return pages[first].number, pages[last].number

    return locate


@functools.lru_cache(maxsize=1)
def _tokenizer() -> Callable[[str], list[int]]:
    from llama_index.core.utils import get_tokenizer

    tokenizer: Callable[[str], list[int]] = get_tokenizer()
    return tokenizer
