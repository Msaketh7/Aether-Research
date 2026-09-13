"""What a caller asks retrieval for: the question, and how hard to look.

Split from ``ChunkFilter`` on purpose. A filter says *what may be returned* and
is a correctness constraint - a retriever that widened one would put excluded
material into a report. A plan says *how to search* and is a cost and quality
dial: which arms run, how many candidates each fetches, how the two are
weighted, whether anything reranks. Mixing them into one bag of keyword
arguments is how a search ends up silently unfiltered because a tuning
parameter was misspelt, which is why both are validated values with
``extra="forbid"``.

Every bound here is a bound on work done per call, as the engineering rules
require: no unbounded candidate set, no unbounded result.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.retrieval.results import RetrievalStrategy

#: The most chunks any single call will return. The consumer is a model's
#: context window, and a caller asking for hundreds has made a mistake that a
#: truncated report would hide.
MAX_RESULTS = 50

#: The most candidates one arm will fetch before fusion. Matches the
#: repository's own ceiling on any list query.
MAX_CANDIDATES = 200

#: Longest query text accepted. Long enough for a planner's full subtask
#: sentence, short enough that it cannot be used to smuggle a document in.
MAX_QUERY_CHARS = 2000


class RetrievalPlan(BaseModel):
    """How to search, independent of what is being searched for.

    The defaults describe the strategy the TDD (section 8.2) specifies: both
    arms, roughly fifty candidates, fused, cut to about ten. Each is a
    documented starting point rather than a measured optimum - the benchmark in
    ``app.retrieval.benchmark`` is what moves them.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    strategy: RetrievalStrategy = RetrievalStrategy.HYBRID
    #: Chunks returned to the caller.
    limit: int = Field(default=10, ge=1, le=MAX_RESULTS)
    #: Chunks each arm fetches before fusion. The TDD's "top ~50 -> top ~8-12".
    candidates: int = Field(default=50, ge=1, le=MAX_CANDIDATES)
    #: RRF's rank constant. See ``app.retrieval.fusion``.
    rrf_k: int = Field(default=60, ge=1, le=1000)
    #: Per-arm weight in the fusion. Zero disables an arm without changing the
    #: strategy, which is how a deployment with no embeddings stays configured
    #: for hybrid without pretending the dense arm ran.
    dense_weight: float = Field(default=1.0, ge=0.0, le=100.0)
    lexical_weight: float = Field(default=1.0, ge=0.0, le=100.0)
    #: Whether the configured reranker runs at all.
    rerank: bool = True

    @model_validator(mode="after")
    def _coherent(self) -> RetrievalPlan:
        if self.candidates < self.limit:
            raise ValueError(
                "candidates must be at least limit: fusion cannot return more chunks "
                "than the arms fetched."
            )
        if self.strategy is RetrievalStrategy.HYBRID and not (
            self.dense_weight or self.lexical_weight
        ):
            raise ValueError("A hybrid plan with both weights at zero would search nothing.")
        return self

    @property
    def wants_dense(self) -> bool:
        return self.strategy in _DENSE_STRATEGIES and self.dense_weight > 0

    @property
    def wants_lexical(self) -> bool:
        return self.strategy in _LEXICAL_STRATEGIES and self.lexical_weight > 0


_DENSE_STRATEGIES = frozenset({RetrievalStrategy.DENSE, RetrievalStrategy.HYBRID})
_LEXICAL_STRATEGIES = frozenset({RetrievalStrategy.LEXICAL, RetrievalStrategy.HYBRID})


class QueryText(BaseModel):
    """A validated search string.

    Trimmed and length-bounded, and that is all: it is *not* escaped. Postgres
    parses it with ``websearch_to_tsquery`` and the embedding provider takes it
    as data, so there is no expression for punctuation to break out of. The
    checks that matter are the ones a caller would otherwise forget - an empty
    query matching everything, and a query long enough to be a document.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str = Field(min_length=1, max_length=MAX_QUERY_CHARS)

    @field_validator("text", mode="before")
    @classmethod
    def _trim(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    def __str__(self) -> str:
        return self.text
