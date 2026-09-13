"""Measuring retrieval strategies against each other.

The build plan asks this phase to *benchmark the strategies* and to stop
treating the chunk size as settled. Both need the same machinery: a corpus, a
set of questions, a definition of which chunks answer them, and the four metrics
in the evaluation methodology (section 3.1).

**How relevance is labelled.** A case names verbatim *anchor* passages - text
that actually appears in the corpus and that answers the question. A chunk is
relevant if it contains one. That definition is deliberate:

* it is objective and re-checkable, unlike a remembered opinion about which
  chunk was best;
* it survives re-chunking, which is the whole point - the same labels score a
  256-token corpus and a 1024-token one, so the chunk-size sweep compares
  strategies rather than comparing label sets;
* it cannot silently go stale. An anchor that no longer appears in the corpus
  makes its case *unmeasurable* and says so, rather than scoring zero and
  looking like a retrieval regression.

**What this is not.** These are hand-written labels over a small corpus. They
are enough to rank strategies against each other and to catch a regression; they
are not a statement about absolute retrieval quality on the open web, and the
report says so rather than implying otherwise. No number here is written down
anywhere until it has been produced by running this (evaluation doc, section 1).
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol
from uuid import UUID

from app.retrieval.filters import ChunkFilter, ChunkView
from app.retrieval.metrics import (
    Mean,
    mean,
    ndcg_at_k,
    precision_at_k,
    recall_at_k,
    reciprocal_rank,
)
from app.retrieval.query import RetrievalPlan
from app.retrieval.results import RetrievalStrategy
from app.retrieval.retriever import Retriever

#: Chunks pulled per page when resolving anchors. The repository's own ceiling.
_PAGE = 200


@dataclass(frozen=True, slots=True)
class BenchmarkCase:
    """One question, and the passages that answer it."""

    id: str
    question: str
    #: Verbatim text from the corpus. A chunk containing any of these is
    #: relevant. Compared case-insensitively with whitespace collapsed, because
    #: chunking may re-wrap a line but never rewrites a word.
    anchors: tuple[str, ...]
    notes: str = ""


@dataclass(frozen=True, slots=True)
class LabelledCase:
    """A case with its anchors resolved against a particular corpus."""

    case: BenchmarkCase
    relevant: frozenset[UUID]
    #: Anchors that matched no chunk at all. A non-empty list here is a broken
    #: label, not a retrieval failure, and it is reported as such.
    missing_anchors: tuple[str, ...]

    @property
    def measurable(self) -> bool:
        return bool(self.relevant)


@dataclass(frozen=True, slots=True)
class CorpusProfile:
    """What was searched, so a result is attributable to a corpus and not just a query."""

    run_id: UUID
    documents: int
    chunks: int
    embedded_chunks: int
    chunk_size_tokens: int
    chunk_overlap_tokens: int

    @property
    def fully_embedded(self) -> bool:
        return self.chunks > 0 and self.embedded_chunks == self.chunks


@dataclass(frozen=True, slots=True)
class StrategyReport:
    """One strategy's numbers over every measurable case."""

    name: str
    strategy: RetrievalStrategy
    reranker: str | None
    k: int
    recall: Mean
    precision: Mean
    mrr: Mean
    ndcg: Mean
    mean_latency_ms: float
    cases_measured: int
    cases_unmeasurable: tuple[str, ...]
    #: Cases where not one relevant chunk reached the top k. Named rather than
    #: counted: a benchmark that cannot say which question it failed is hard to
    #: act on, and these are where a retrieval defect is visible.
    misses: tuple[str, ...] = ()

    def row(self) -> str:
        return (
            f"{self.name:<24} {_fmt(self.recall)}  {_fmt(self.precision)}  "
            f"{_fmt(self.mrr)}  {_fmt(self.ndcg)}  {self.mean_latency_ms:7.1f}ms"
        )


@dataclass(frozen=True, slots=True)
class SkippedStrategy:
    """A strategy that could not run here, with the reason a reader needs.

    Its own type rather than a ``StrategyReport`` full of ``None``: *not
    measured* and *measured as zero* are different facts, and the report must
    not let them look alike.
    """

    name: str
    reason: str


@dataclass(frozen=True, slots=True)
class BenchmarkReport:
    corpus: CorpusProfile
    strategies: tuple[StrategyReport, ...]
    skipped: tuple[SkippedStrategy, ...] = ()
    cases: int = 0

    def render(self) -> str:
        header = (
            f"{'strategy':<24} {'recall@k':>10}  {'prec@k':>10}  "
            f"{'MRR':>10}  {'nDCG@k':>10}  {'latency':>9}"
        )
        lines = [
            f"corpus: {self.corpus.documents} documents, {self.corpus.chunks} chunks "
            f"at {self.corpus.chunk_size_tokens}/{self.corpus.chunk_overlap_tokens} tokens, "
            f"{self.corpus.embedded_chunks} embedded",
            f"cases:  {self.cases}",
            "",
            header,
            "-" * len(header),
            *(report.row() for report in self.strategies),
        ]
        for report in self.strategies:
            if report.misses:
                lines += ["", f"{report.name} found nothing for: {', '.join(report.misses)}"]
        unmeasurable = self.strategies[0].cases_unmeasurable if self.strategies else ()
        if unmeasurable:
            lines += ["", f"no chunk carries the anchors of: {', '.join(unmeasurable)}"]
        if self.skipped:
            lines += ["", "not measured:"]
            lines += [f"  {item.name}: {item.reason}" for item in self.skipped]
        return "\n".join(lines)


@dataclass
class _CaseRun:
    """Per-case measurements for one strategy, accumulated as cases run."""

    recall: list[float | None] = field(default_factory=list)
    precision: list[float | None] = field(default_factory=list)
    rr: list[float | None] = field(default_factory=list)
    ndcg: list[float | None] = field(default_factory=list)
    latencies: list[float] = field(default_factory=list)


def load_cases(path: Path) -> list[BenchmarkCase]:
    """Read a labelled case file. Fails loudly on a malformed entry.

    A dataset that silently dropped a case it could not parse would move every
    aggregate without moving any code, which is precisely the confound the
    ``dataset_version`` pinning in the evaluation methodology exists to prevent.
    """
    raw = json.loads(path.read_text(encoding="utf-8"))
    entries = raw["cases"] if isinstance(raw, dict) else raw
    cases: list[BenchmarkCase] = []
    seen: set[str] = set()
    for entry in entries:
        case = BenchmarkCase(
            id=str(entry["id"]),
            question=str(entry["question"]),
            anchors=tuple(str(anchor) for anchor in entry["anchors"]),
            notes=str(entry.get("notes", "")),
        )
        if not case.anchors:
            raise ValueError(f"Case {case.id!r} names no anchors, so nothing is relevant to it.")
        if case.id in seen:
            raise ValueError(f"Duplicate case id {case.id!r} in {path}.")
        seen.add(case.id)
        cases.append(case)
    return cases


def label(cases: Iterable[BenchmarkCase], chunks: Sequence[ChunkView]) -> list[LabelledCase]:
    """Resolve each case's anchors to the chunks of this corpus that contain them.

    Whitespace is collapsed on both sides before comparing: a chunker may re-wrap
    a sentence across lines, and an anchor copied out of the source would then
    match nothing for a reason that has nothing to do with retrieval.
    """
    normalised = [(chunk.id, _collapse(chunk.text.expose())) for chunk in chunks]
    labelled: list[LabelledCase] = []
    for case in cases:
        relevant: set[UUID] = set()
        missing: list[str] = []
        for anchor in case.anchors:
            needle = _collapse(anchor)
            matches = {chunk_id for chunk_id, text in normalised if needle in text}
            if matches:
                relevant |= matches
            else:
                missing.append(anchor)
        labelled.append(
            LabelledCase(case=case, relevant=frozenset(relevant), missing_anchors=tuple(missing))
        )
    return labelled


class ChunkSource(Protocol):
    """Paging a run's chunks - the only thing labelling needs from storage.

    A Protocol rather than an import of the repository class: the benchmark
    depends on being able to read a run's chunks, not on which object does it,
    and that is what lets the harness be tested without a database.
    """

    async def list_chunks(
        self,
        filters: ChunkFilter,
        *,
        user_id: UUID,
        limit: int,
        after: tuple[UUID, int] | None = None,
    ) -> tuple[list[ChunkView], bool]:
        """One page of chunks, and whether more follow."""
        ...


async def collect_chunks(source: ChunkSource, *, run_id: UUID, user_id: UUID) -> list[ChunkView]:
    """Every chunk of a run, paged through the repository's own bounded reads."""
    collected: list[ChunkView] = []
    cursor: tuple[UUID, int] | None = None
    while True:
        page, has_more = await source.list_chunks(
            ChunkFilter(run_id=run_id), user_id=user_id, limit=_PAGE, after=cursor
        )
        collected.extend(page)
        if not has_more or not page:
            return collected
        last = page[-1]
        cursor = (last.document_id, last.chunk_index)


async def run_benchmark(
    retriever: Retriever,
    *,
    labelled: Sequence[LabelledCase],
    corpus: CorpusProfile,
    plans: Mapping[str, RetrievalPlan],
    user_id: UUID,
) -> BenchmarkReport:
    """Run every plan over every measurable case and report the four metrics.

    A plan whose dense arm is skipped on every case is reported as *not
    measured* rather than as a strategy that scored badly: a dense arm with
    nothing embedded to search has not lost to the lexical one, it never ran.
    """
    measurable = [item for item in labelled if item.measurable]
    unmeasurable = tuple(item.case.id for item in labelled if not item.measurable)

    reports: list[StrategyReport] = []
    skipped: list[SkippedStrategy] = []

    for name, plan in plans.items():
        run = _CaseRun()
        reranker: str | None = None
        arms_skipped: set[str] = set()

        misses: list[str] = []
        for item in measurable:
            started = time.perf_counter()
            result = await retriever.retrieve_hybrid(
                item.case.question,
                filters=ChunkFilter(run_id=corpus.run_id),
                user_id=user_id,
                plan=plan,
            )
            run.latencies.append((time.perf_counter() - started) * 1000)
            reranker = result.reranker
            for arm in result.skipped_arms:
                if arm.skipped_reason and _arm_wanted(plan, arm.strategy):
                    arms_skipped.add(arm.skipped_reason)

            ranked = list(result.chunk_ids)
            found = recall_at_k(ranked, item.relevant, plan.limit)
            if not found:
                misses.append(item.case.id)
            run.recall.append(found)
            run.precision.append(precision_at_k(ranked, item.relevant, plan.limit))
            run.rr.append(reciprocal_rank(ranked, item.relevant))
            run.ndcg.append(ndcg_at_k(ranked, item.relevant, plan.limit))

        if arms_skipped:
            skipped.append(SkippedStrategy(name=name, reason="; ".join(sorted(arms_skipped))))
            continue

        reports.append(
            StrategyReport(
                name=name,
                strategy=plan.strategy,
                reranker=reranker,
                k=plan.limit,
                recall=mean(run.recall),
                precision=mean(run.precision),
                mrr=mean(run.rr),
                ndcg=mean(run.ndcg),
                mean_latency_ms=(sum(run.latencies) / len(run.latencies) if run.latencies else 0.0),
                cases_measured=len(measurable),
                cases_unmeasurable=unmeasurable,
                misses=tuple(misses),
            )
        )

    return BenchmarkReport(
        corpus=corpus,
        strategies=tuple(reports),
        skipped=tuple(skipped),
        cases=len(labelled),
    )


def _arm_wanted(plan: RetrievalPlan, strategy: RetrievalStrategy) -> bool:
    """Whether this plan asked for that arm, so skipping it invalidates the row."""
    if strategy is RetrievalStrategy.DENSE:
        return plan.wants_dense
    if strategy is RetrievalStrategy.LEXICAL:
        return plan.wants_lexical
    return False


def _collapse(text: str) -> str:
    return " ".join(text.split()).casefold()


def _fmt(value: Mean) -> str:
    return f"{value.value:>10.3f}" if value.value is not None else f"{'n/a':>10}"
