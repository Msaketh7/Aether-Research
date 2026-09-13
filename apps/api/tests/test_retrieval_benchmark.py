"""The benchmark harness: labelling, scoring, and refusing to invent a number.

The harness is what the retrieval numbers in the documentation come from, so a
defect here is worse than a defect in retrieval - it would be a wrong number
presented as a measurement. These tests drive it with a retriever whose answers
are fixed in the test, so every expected metric is arithmetic that can be
checked by hand.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

from app.retrieval.benchmark import (
    BenchmarkCase,
    CorpusProfile,
    collect_chunks,
    label,
    load_cases,
    run_benchmark,
)
from app.retrieval.filters import ChunkView
from app.retrieval.query import RetrievalPlan
from app.retrieval.results import ArmOutcome, RetrievalResult, RetrievalStrategy, RetrievedChunk
from tests.support.retrieval import chunk_id, view

DENSE = RetrievalStrategy.DENSE
LEXICAL = RetrievalStrategy.LEXICAL

REVENUE = "Data centre revenue reached 26.3 billion dollars, the company reported on Tuesday."
EXPORTS = "Export controls restrict shipments of the fastest accelerators to some regions."
MEMORY = "Memory bandwidth, not raw compute, limits most production serving deployments."


def case(identifier: str, *anchors: str) -> BenchmarkCase:
    return BenchmarkCase(id=identifier, question=f"question for {identifier}", anchors=anchors)


def profile(**overrides: object) -> CorpusProfile:
    fields: dict[str, object] = {
        "run_id": uuid.uuid4(),
        "documents": 1,
        "chunks": 3,
        "embedded_chunks": 0,
        "chunk_size_tokens": 512,
        "chunk_overlap_tokens": 64,
    }
    fields.update(overrides)
    return CorpusProfile(**fields)  # type: ignore[arg-type]


class FakeRetriever:
    """Answers with a fixed ranking, and with whichever arms it is told ran."""

    def __init__(self, ranking: list[str], *, skipped: tuple[RetrievalStrategy, ...] = ()) -> None:
        self._ranking = ranking
        self._skipped = skipped
        self.calls: list[str] = []

    async def retrieve(self, query, *, run_id, user_id, limit=None):  # pragma: no cover
        raise NotImplementedError

    async def retrieve_with_filters(self, query, *, filters, user_id, limit=None):
        raise NotImplementedError  # pragma: no cover

    async def retrieve_hybrid(self, query, *, filters, user_id, plan=None):
        self.calls.append(query)
        plan = plan or RetrievalPlan()
        chunks = tuple(
            RetrievedChunk(chunk=view(name), score=1.0, fusion_score=1.0)
            for name in self._ranking[: plan.limit]
        )
        arms = tuple(
            ArmOutcome(
                strategy=strategy,
                ran=strategy not in self._skipped,
                returned=0 if strategy in self._skipped else len(chunks),
                latency_ms=0,
                skipped_reason="nothing to search" if strategy in self._skipped else None,
            )
            for strategy in (DENSE, LEXICAL)
        )
        return RetrievalResult(
            strategy=plan.strategy,
            chunks=chunks,
            arms=arms,
            candidates=len(chunks),
            reranker="none",
            latency_ms=0,
        )


# --- loading a dataset ----------------------------------------------------


def write_dataset(tmp_path: Path, cases: list[dict]) -> Path:
    path = tmp_path / "cases.json"
    path.write_text(json.dumps({"cases": cases}), encoding="utf-8")
    return path


def test_a_dataset_round_trips(tmp_path):
    path = write_dataset(
        tmp_path, [{"id": "one", "question": "why?", "anchors": ["because"], "notes": "n"}]
    )
    assert load_cases(path) == [
        BenchmarkCase(id="one", question="why?", anchors=("because",), notes="n")
    ]


def test_a_case_with_no_anchors_is_refused(tmp_path):
    """Nothing would be relevant to it, so it would silently score every
    strategy as unmeasurable rather than failing where the mistake is."""
    path = write_dataset(tmp_path, [{"id": "one", "question": "why?", "anchors": []}])
    with pytest.raises(ValueError, match="names no anchors"):
        load_cases(path)


def test_a_duplicate_case_id_is_refused(tmp_path):
    """Two cases with one id would be counted twice and attributed once."""
    path = write_dataset(
        tmp_path,
        [
            {"id": "one", "question": "a", "anchors": ["x"]},
            {"id": "one", "question": "b", "anchors": ["y"]},
        ],
    )
    with pytest.raises(ValueError, match="Duplicate case id"):
        load_cases(path)


# --- labelling ------------------------------------------------------------


def test_an_anchor_labels_every_chunk_that_contains_it():
    """Chunks overlap, so one passage legitimately lands in two of them."""
    chunks = [
        view("a", text=REVENUE),
        view("b", text=f"...{REVENUE} and more"),
        view("c", text=EXPORTS),
    ]
    labelled = label([case("revenue", "26.3 billion dollars")], chunks)
    assert labelled[0].relevant == {chunk_id("a"), chunk_id("b")}


def test_an_anchor_matches_across_a_rewrapped_line():
    """A chunker may re-wrap a sentence. Whitespace is collapsed on both sides
    so that an anchor copied out of the source still resolves."""
    chunks = [view("a", text="Memory bandwidth,\n   not raw    compute, limits serving.")]
    labelled = label([case("memory", "Memory bandwidth, not raw compute")], chunks)
    assert labelled[0].relevant == {chunk_id("a")}


def test_an_anchor_that_matches_nothing_is_named_rather_than_ignored():
    """A stale label is a broken dataset, not a retrieval regression, and the
    two must not look alike."""
    labelled = label([case("gone", "a passage that was deleted")], [view("a", text=REVENUE)])
    assert labelled[0].missing_anchors == ("a passage that was deleted",)
    assert not labelled[0].measurable


def test_a_case_with_one_resolvable_anchor_stays_measurable():
    labelled = label(
        [case("mixed", "26.3 billion", "not in the corpus")], [view("a", text=REVENUE)]
    )
    assert labelled[0].measurable
    assert labelled[0].missing_anchors == ("not in the corpus",)


# --- running --------------------------------------------------------------


async def test_the_metrics_are_computed_from_the_ranking():
    """One relevant chunk, returned third of three, at k=3: recall 1, precision
    1/3, MRR 1/3."""
    chunks = [view("a", text=EXPORTS), view("b", text=MEMORY), view("c", text=REVENUE)]
    labelled = label([case("revenue", "26.3 billion dollars")], chunks)
    report = await run_benchmark(
        FakeRetriever(["a", "b", "c"]),
        labelled=labelled,
        corpus=profile(),
        plans={"lexical": RetrievalPlan(strategy=LEXICAL, limit=3, candidates=3)},
        user_id=uuid.uuid4(),
    )
    strategy = report.strategies[0]
    assert strategy.recall.value == 1.0
    assert strategy.precision.value == pytest.approx(1 / 3)
    assert strategy.mrr.value == pytest.approx(1 / 3)


async def test_a_case_no_strategy_can_answer_is_named():
    chunks = [view("a", text=EXPORTS), view("b", text=MEMORY)]
    labelled = label([case("revenue", "26.3 billion dollars")], chunks)
    report = await run_benchmark(
        FakeRetriever(["a", "b"]),
        labelled=labelled,
        corpus=profile(),
        plans={"lexical": RetrievalPlan(strategy=LEXICAL, limit=2, candidates=2)},
        user_id=uuid.uuid4(),
    )
    # The anchor resolved to no chunk here, so the case is unmeasurable rather
    # than a miss - the distinction the harness exists to keep.
    assert report.strategies[0].cases_unmeasurable == ("revenue",)
    assert report.strategies[0].recall.value is None


async def test_a_miss_is_named_when_the_case_was_measurable():
    chunks = [view("a", text=EXPORTS), view("b", text=MEMORY), view("c", text=REVENUE)]
    labelled = label([case("revenue", "26.3 billion dollars")], chunks)
    report = await run_benchmark(
        FakeRetriever(["a", "b"]),
        labelled=labelled,
        corpus=profile(),
        plans={"lexical": RetrievalPlan(strategy=LEXICAL, limit=2, candidates=2)},
        user_id=uuid.uuid4(),
    )
    assert report.strategies[0].misses == ("revenue",)
    assert report.strategies[0].recall.value == 0.0


async def test_a_strategy_whose_arm_could_not_run_is_not_scored():
    """The rule the whole harness turns on. A dense strategy with nothing
    embedded has not lost to the lexical one; it never ran, and reporting a 0.0
    would put a fabricated number in a benchmark table."""
    chunks = [view("a", text=REVENUE)]
    labelled = label([case("revenue", "26.3 billion dollars")], chunks)
    report = await run_benchmark(
        FakeRetriever(["a"], skipped=(DENSE,)),
        labelled=labelled,
        corpus=profile(),
        plans={"dense": RetrievalPlan(strategy=DENSE, limit=1, candidates=1)},
        user_id=uuid.uuid4(),
    )
    assert report.strategies == ()
    assert [item.name for item in report.skipped] == ["dense"]
    assert report.skipped[0].reason == "nothing to search"


async def test_an_arm_a_plan_did_not_want_does_not_invalidate_it():
    """A lexical plan skips the dense arm by design. That is not a strategy
    that failed to run - it is the strategy working as specified."""
    chunks = [view("a", text=REVENUE)]
    labelled = label([case("revenue", "26.3 billion dollars")], chunks)
    report = await run_benchmark(
        FakeRetriever(["a"], skipped=(DENSE,)),
        labelled=labelled,
        corpus=profile(),
        plans={"lexical": RetrievalPlan(strategy=LEXICAL, limit=1, candidates=1)},
        user_id=uuid.uuid4(),
    )
    assert report.skipped == ()
    assert report.strategies[0].recall.value == 1.0


async def test_the_rendered_report_states_what_was_not_measured():
    chunks = [view("a", text=REVENUE)]
    labelled = label([case("revenue", "26.3 billion dollars")], chunks)
    report = await run_benchmark(
        FakeRetriever(["a"], skipped=(DENSE,)),
        labelled=labelled,
        corpus=profile(chunks=1),
        plans={"dense": RetrievalPlan(strategy=DENSE, limit=1, candidates=1)},
        user_id=uuid.uuid4(),
    )
    rendered = report.render()
    assert "not measured:" in rendered
    assert "nothing to search" in rendered


# --- paging the corpus ----------------------------------------------------


class PagedChunks:
    """A chunk source that answers in pages, like the repository does."""

    def __init__(self, chunks: list[ChunkView]) -> None:
        self._chunks = chunks
        self.pages = 0

    async def list_chunks(self, filters, *, user_id, limit, after=None):
        self.pages += 1
        start = (
            0
            if after is None
            else next(
                index + 1
                for index, chunk in enumerate(self._chunks)
                if (chunk.document_id, chunk.chunk_index) == after
            )
        )
        page = self._chunks[start : start + 2]
        return page, start + 2 < len(self._chunks)


async def test_collecting_a_corpus_follows_every_page():
    """Labelling has to see the whole corpus; a bounded read that stopped at one
    page would silently label only its first chunks."""
    chunks = [
        ChunkView(**{**vars_of(view(name)), "chunk_index": index})
        for index, name in enumerate(("a", "b", "c", "d", "e"))
    ]
    source = PagedChunks(chunks)
    collected = await collect_chunks(source, run_id=uuid.uuid4(), user_id=uuid.uuid4())
    assert len(collected) == 5
    assert source.pages == 3


def vars_of(chunk: ChunkView) -> dict:
    return {field: getattr(chunk, field) for field in ChunkView.__slots__}
