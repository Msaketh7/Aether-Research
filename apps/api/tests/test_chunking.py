"""Chunking: exact offsets, full coverage, a bounded size - checked, not trusted.

The small chunker (64 tokens) makes many chunks from little text, which is what
exercises the boundaries. The production size is used where the property being
tested - overlap - only appears at that size.
"""

from __future__ import annotations

from itertools import pairwise

import pytest

from app.core.enums import DocumentFormat
from app.retrieval.chunking import Chunker, verify_coverage
from app.retrieval.errors import DocumentTooLarge
from app.retrieval.parsed import PageSpan
from app.retrieval.parsers import PAGE_SEPARATOR
from tests.support.documents import prose

SMALL = Chunker(chunk_size_tokens=64, chunk_overlap_tokens=8, max_chunks=10_000)


def assert_exact(text, chunks):
    """Every chunk is a slice of the text, in order, and nothing is lost between them."""
    assert chunks
    for chunk in chunks:
        assert text[chunk.start : chunk.end] == chunk.text
    verify_coverage(text, chunks)
    starts = [chunk.start for chunk in chunks]
    assert starts == sorted(starts)
    assert len(set(starts)) == len(starts)


def test_every_chunk_is_an_exact_slice_of_the_text():
    text = prose(30)
    chunks = SMALL.chunk(text, fmt=DocumentFormat.TEXT)
    assert len(chunks) > 5
    assert_exact(text, chunks)
    assert [chunk.index for chunk in chunks] == list(range(len(chunks)))


def test_chunks_stay_within_the_token_budget():
    chunks = SMALL.chunk(prose(30), fmt=DocumentFormat.TEXT)
    assert max(chunk.token_count for chunk in chunks) <= 64
    assert all(chunk.token_count > 0 for chunk in chunks)


def test_neighbouring_chunks_overlap_at_the_production_size():
    text = prose(120)
    chunks = Chunker(chunk_size_tokens=512, chunk_overlap_tokens=64, max_chunks=1000).chunk(
        text, fmt=DocumentFormat.TEXT
    )
    assert len(chunks) > 3
    assert_exact(text, chunks)
    assert any(after.start < before.end for before, after in pairwise(chunks))


def test_a_table_of_contents_with_dotted_leaders_is_kept_whole():
    """One enormous "sentence" of dotted leaders forces the splitter's
    sub-sentence fallback - the path where characters are easiest to lose."""
    text = " ".join(f"Section {number} {'.' * 12} {number * 3}" for number in range(1, 400))
    assert_exact(text, SMALL.chunk(text, fmt=DocumentFormat.TEXT))


def test_text_repeated_on_every_page_resolves_to_the_right_occurrence():
    """A running header makes a first-occurrence search put every chunk on page one."""
    header = "ACME Corp confidential. This header repeats on every page. "
    text = "".join(
        header + f"Finding number {number} is recorded on this page in some detail. " * 4
        for number in range(40)
    )
    assert_exact(text, SMALL.chunk(text, fmt=DocumentFormat.TEXT))


def test_unbroken_text_with_no_spaces_is_still_covered():
    text = "x" * 5000
    assert_exact(text, SMALL.chunk(text, fmt=DocumentFormat.TEXT))


def test_chunks_carry_the_pdf_pages_they_touch():
    page_texts = [prose(3, offset=number) for number in range(4)]
    spans: list[PageSpan] = []
    cursor = 0
    for number, page in enumerate(page_texts, start=1):
        spans.append(PageSpan(number=number, start=cursor, end=cursor + len(page)))
        cursor += len(page) + len(PAGE_SEPARATOR)
    text = PAGE_SEPARATOR.join(page_texts)

    chunks = SMALL.chunk(text, fmt=DocumentFormat.PDF, pages=spans)

    assert_exact(text, chunks)
    for chunk in chunks:
        touched = [
            span.number for span in spans if span.start < chunk.end and chunk.start < span.end
        ]
        assert (chunk.page_start, chunk.page_end) == (touched[0], touched[-1])
    assert {chunk.page_start for chunk in chunks} == {1, 2, 3, 4}


def test_markdown_chunks_stay_inside_their_section_and_name_it():
    text = (
        "# Guide\n\nIntro paragraph.\n\n## Install\n\n"
        + prose(4)
        + "\n\n## Configure\n\n"
        + prose(4, offset=3)
        + "\n"
    )
    chunks = SMALL.chunk(text, fmt=DocumentFormat.MARKDOWN)

    assert_exact(text, chunks)
    assert {"Guide", "Guide > Install", "Guide > Configure"} <= {chunk.section for chunk in chunks}
    configure_at = text.index("## Configure")
    for chunk in chunks:
        if chunk.section == "Guide > Install":
            assert chunk.end <= configure_at


def test_a_heading_that_contains_a_slash_is_one_level():
    """LlamaIndex joins heading paths with "/", so this came back as three levels."""
    text = "# Guide\n\n" + prose(2) + "\n\n## input/output formats\n\n" + prose(2, offset=2)
    chunks = SMALL.chunk(text, fmt=DocumentFormat.MARKDOWN)
    assert "Guide > input/output formats" in {chunk.section for chunk in chunks}


def test_a_document_that_would_pass_the_chunk_ceiling_is_refused():
    chunker = Chunker(chunk_size_tokens=64, chunk_overlap_tokens=8, max_chunks=3)
    with pytest.raises(DocumentTooLarge):
        chunker.chunk(prose(30), fmt=DocumentFormat.TEXT)


def test_chunking_is_deterministic():
    text = prose(20)
    first = [(chunk.start, chunk.end) for chunk in SMALL.chunk(text, fmt=DocumentFormat.TEXT)]
    second = [(chunk.start, chunk.end) for chunk in SMALL.chunk(text, fmt=DocumentFormat.TEXT)]
    assert first == second


def test_the_chunker_labels_the_settings_that_produced_a_chunk():
    chunker = Chunker(chunk_size_tokens=512, chunk_overlap_tokens=64, max_chunks=10)
    assert chunker.label == "sentence-v1/512/64"


def test_an_overlap_as_large_as_the_chunk_is_refused():
    with pytest.raises(ValueError):
        Chunker(chunk_size_tokens=64, chunk_overlap_tokens=64, max_chunks=10)
