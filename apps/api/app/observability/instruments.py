"""The recorders that turn one call into a metric and a span (Phase 17).

Composed around the recorders the earlier phases built rather than replacing
them, which is the same arrangement Phase 16 used for the ledger: a model call
is logged, counted, traced and stored, and each of those is a decorator that
can be left out. The wiring in ``app.workers.runner`` reads as the list of
things that happen to a call, which is what it should read as.

Two rules hold throughout.

**A metric never fails the work.** Every observation is wrapped. The failure
mode being avoided is specific and has bitten real systems: a label that
cannot be rendered, or a registry in a strange state, taking down a research
run over a counter.

**A label is bounded or it is not a label.** Provider, model, role, tool and
status are closed vocabularies. A run id, a URL and a query are not, and none
of them appears here - they belong in the ledger, which is a database.
"""

from __future__ import annotations

from typing import Any

from app.cache.keys import CacheNamespace
from app.core.enums import RunStatus
from app.core.logging import get_logger
from app.models.recording import CallRecorder, LlmCallRecord
from app.observability.metrics import Metrics
from app.observability.tracing import current_ids
from app.sources.base import CallRecorder as ToolCallRecorder
from app.sources.base import ToolCallRecord

logger = get_logger(__name__)


def _guard(what: str) -> Any:
    """A decorator would read worse than the try blocks below; this is the log."""
    logger.debug("a metric could not be recorded", extra={"metric": what})


class MeteredCallRecorder:
    """Counts a model call, then passes it on."""

    def __init__(self, metrics: Metrics, inner: CallRecorder) -> None:
        self._metrics = metrics
        self._inner = inner

    async def record(self, call: LlmCallRecord) -> None:
        try:
            labels = {
                "provider": call.provider.value,
                "model": call.model,
                "role": call.role.value,
                "status": call.status.value,
                "cache_hit": str(call.cache_hit).lower(),
            }
            self._metrics.llm_calls.labels(**labels).inc()
            self._metrics.llm_duration.labels(
                provider=call.provider.value, model=call.model
            ).observe(call.latency_ms / 1000)
            for kind, tokens in (
                ("prompt", call.prompt_tokens),
                ("completion", call.completion_tokens),
            ):
                if tokens:
                    self._metrics.llm_tokens.labels(
                        provider=call.provider.value, model=call.model, kind=kind
                    ).inc(tokens)
            if call.cost_usd:
                # Only a known price is added. A call the registry cannot
                # price adds nothing here rather than adding zero, and is
                # visible as an uncosted call on the run instead.
                self._metrics.llm_cost.labels(provider=call.provider.value, model=call.model).inc(
                    call.cost_usd
                )
        except Exception:
            _guard("llm_calls")
        await self._inner.record(call)


class MeteredToolRecorder:
    """Counts a tool call, then passes it on."""

    def __init__(self, metrics: Metrics, inner: ToolCallRecorder) -> None:
        self._metrics = metrics
        self._inner = inner

    async def record(self, call: ToolCallRecord) -> None:
        try:
            self._metrics.tool_calls.labels(
                tool=call.tool_name.value,
                status=call.status.value,
                cache_hit=str(call.cache_hit).lower(),
            ).inc()
            self._metrics.tool_duration.labels(tool=call.tool_name.value).observe(
                call.latency_ms / 1000
            )
        except Exception:
            _guard("tool_calls")
        await self._inner.record(call)


def observe_cache(metrics: Metrics, namespace: CacheNamespace, origin: str) -> None:
    """One cache lookup, by where the value came from.

    Three origins rather than hit and miss: a value served from the store and
    a value that joined a call already in flight both cost nothing, and they
    are different optimisations with different fixes when one stops working.
    """
    try:
        metrics.cache_lookups.labels(namespace=namespace.value, origin=origin).inc()
    except Exception:
        _guard("cache_lookups")


def observe_slot_wait(metrics: Metrics, seconds: float) -> None:
    """One model call's wait for the gateway's concurrency slot (Phase 21).

    Every acquisition is observed, the immediate ones included. A histogram
    fed only the waits would report a healthy median while the system queued,
    because the calls that did not queue would not be in the denominator.
    """
    try:
        metrics.llm_slot_wait.observe(seconds)
    except Exception:
        _guard("llm_slot_wait")


def observe_retrieval(metrics: Metrics, *, seconds: float, results: int) -> None:
    try:
        metrics.retrieval_duration.observe(seconds)
        metrics.retrieval_results.observe(results)
    except Exception:
        _guard("retrieval")


def observe_run(metrics: Metrics, *, status: RunStatus, seconds: float | None) -> None:
    """How a run ended, and how long it took to get there.

    ``seconds`` is ``None`` for a run whose start is not known, which is not
    recorded as zero - a histogram with a false observation in it is worse
    than one with a gap.
    """
    try:
        metrics.research_runs.labels(status=status.value).inc()
        if seconds is not None:
            metrics.research_duration.labels(status=status.value).observe(seconds)
    except Exception:
        _guard("research_runs")


def span_ids() -> tuple[str | None, str | None]:
    """The active trace and span, for a caller that stores them as columns."""
    ids = current_ids()
    return ids.trace_id, ids.span_id
