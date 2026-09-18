"""OpenTelemetry: one trace per request and per run, across both processes.

Metrics say how often and how long; a trace says *why this one*. The seams
were already there - a request id since Phase 2, a span per node execution
since Phase 16 - and this attaches OpenTelemetry to them so a slow run can be
opened and read instead of reconstructed from timestamps.

**Configured, or absent. Never half-on.** With no exporter endpoint the global
no-op provider stays in place: every ``start_as_current_span`` below still
runs, costs almost nothing, and records nothing. That is what makes it safe to
instrument the hot path without asking whether telemetry is switched on, and
it is why there is no ``if tracing_enabled`` anywhere outside this module.

**The ids reach the ledger.** ``agent_runs``, ``tool_calls`` and ``llm_calls``
have carried ``trace_id`` and ``span_id`` columns since Phase 3, unwritten.
Filling them is what lets someone move from a row - "this step cost $0.40" -
to the trace that shows what it was doing, which is the whole argument for
having both.

**LangSmith is a separate decision, stays off unless asked, and is
configured by construction rather than through the environment.** Phase 9
disabled it because the library reads ``LANGSMITH_TRACING`` directly, past the
typed settings layer - so prompts and retrieved documents would leave the
system for a third party whenever that variable happened to be set in a shell.
The fix is not to set the variable from the settings, which would leave two
places the decision can be made and keep the settings layer from being the
only reader of the environment. It is to build the client explicitly and hand
it to ``tracing_context``, so the decision exists exactly once, in
configuration, and an unset key means no client and no tracing.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

from app.core.config import Settings
from app.core.logging import get_logger

if TYPE_CHECKING:  # pragma: no cover - import cost
    from opentelemetry.trace import Tracer

logger = get_logger(__name__)

#: The instrumentation scope every span here is created under.
SCOPE = "aether.research"

_configured = False


def configure_tracing(settings: Settings) -> bool:
    """Install a tracer provider if one is configured. Returns whether it did.

    Idempotent: two processes in one test session, or a second ``create_app``,
    must not stack exporters. The SDK is imported inside the function because
    it is not cheap and a process with no endpoint should not pay for it - the
    same rule the vendor SDKs follow.
    """
    global _configured
    if _configured or not settings.otel_exporter_otlp_endpoint:
        return False

    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    provider = TracerProvider(
        resource=Resource.create(
            {
                "service.name": settings.otel_service_name,
                "deployment.environment": settings.app_env,
            }
        )
    )
    provider.add_span_processor(
        # Batched, not simple: a span exported inline would put an HTTP round
        # trip on the path of every node, which is the thing being measured.
        BatchSpanProcessor(OTLPSpanExporter(endpoint=settings.otel_exporter_otlp_endpoint))
    )
    trace.set_tracer_provider(provider)
    _configured = True
    logger.info(
        "tracing configured",
        extra={
            "endpoint": settings.otel_exporter_otlp_endpoint,
            "service": settings.otel_service_name,
        },
    )
    return True


def tracer() -> Tracer:
    """The tracer for this scope. A no-op one until something is configured."""
    from opentelemetry import trace

    return trace.get_tracer(SCOPE)


@contextmanager
def span(name: str, **attributes: Any) -> Iterator[SpanIds]:
    """A span, and the ids to store beside whatever it measured.

    Yields ``SpanIds`` rather than the span itself: the callers here want the
    identifiers for a ledger row, and handing out the span would invite code
    that depends on the SDK being installed.
    """
    with tracer().start_as_current_span(name) as active:
        for key, value in attributes.items():
            if value is not None:
                active.set_attribute(key, value)
        yield current_ids()


def current_ids() -> SpanIds:
    """The active trace and span, as the ledger columns store them.

    Both are ``None`` when nothing is recording, which is the normal state for
    a deployment with no collector - and is stored as null rather than as a
    string of zeros, so "not traced" is distinguishable from "traced, badly".
    """
    from opentelemetry import trace

    context = trace.get_current_span().get_span_context()
    if not context.is_valid:
        return SpanIds(None, None)
    return SpanIds(trace.format_trace_id(context.trace_id), trace.format_span_id(context.span_id))


class SpanIds:
    """A trace id and a span id, or a pair of ``None``."""

    __slots__ = ("span_id", "trace_id")

    def __init__(self, trace_id: str | None, span_id: str | None) -> None:
        self.trace_id = trace_id
        self.span_id = span_id

    def __eq__(self, other: object) -> bool:
        return (
            isinstance(other, SpanIds)
            and other.trace_id == self.trace_id
            and other.span_id == self.span_id
        )

    def __repr__(self) -> str:
        return f"SpanIds(trace_id={self.trace_id!r}, span_id={self.span_id!r})"

    @property
    def recording(self) -> bool:
        return self.trace_id is not None


def build_langsmith_client(settings: Settings) -> Any | None:
    """A LangSmith client when a deployment has asked for one, else ``None``.

    Constructed with the key from the typed settings rather than left to the
    library's own environment lookup - see the module docstring. ``None`` is
    what the graph runner turns into ``enabled=False``, so "off" and
    "misconfigured" produce the same safe behaviour rather than a half-on one.
    """
    if not settings.langsmith_tracing:
        return None
    if settings.langsmith_api_key is None:
        logger.warning(
            "LangSmith tracing is switched on but no API key is configured; it stays off",
        )
        return None

    from langsmith import Client

    logger.warning(
        "LangSmith tracing is enabled: prompts and retrieved documents will be "
        "sent to a third party",
        extra={"project": settings.langsmith_project},
    )
    return Client(api_key=settings.langsmith_api_key.get_secret_value())


def attributes_for(values: Mapping[str, Any]) -> dict[str, Any]:
    """Drop the absent ones. An attribute set to ``None`` is not an attribute."""
    return {key: value for key, value in values.items() if value is not None}
