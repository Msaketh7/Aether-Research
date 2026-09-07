"""The research-tool interface, and the executor that bounds every call.

The same shape as the LLM gateway in ``app/models``, for the same reason: a
timeout, a retry policy, an error classification and a recorded call are things
that must be true of *every* tool, and a rule applied at each call site is a rule
that will be missed at the next one.

What a tool is:

* a ``ToolName`` from the closed vocabulary the ``tool_calls`` table constrains;
* a **strict Pydantic input schema**, so an agent cannot invent a parameter and
  cannot pass an unbounded ``limit``;
* a typed result;
* a ``run`` that raises from ``app/sources/errors.py`` and nothing else.

What a tool is *not*: a shell. There is no tool that executes a command, reads a
path, or evaluates anything (TDD 15.3). The set is fixed at six, declared here,
and an agent gets whichever subset its role permits.
"""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict

from app.core.enums import ToolName, ToolStatus
from app.core.logging import get_logger
from app.sources.errors import ToolError

logger = get_logger(__name__)


class ToolInput(BaseModel):
    """Base for every tool's input schema.

    ``extra="forbid"`` is the point. An agent that hallucinates a parameter gets
    a validation error naming it, rather than having it silently ignored - which
    is how a `max_results=1000` ends up quietly capped at 10 and nobody notices
    the plan the agent thought it was executing never happened.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


@dataclass(frozen=True, slots=True)
class ToolCallRecord:
    """One attempt at one tool call.

    Mirrors the ``tool_calls`` columns (TDD 7.2) so Phase 17's database sink is
    a mapping rather than a redesign. Recorded per *attempt*, not per success:
    a call that took three tries cost three requests.
    """

    tool_name: ToolName
    status: ToolStatus
    request: Mapping[str, object]
    response_summary: Mapping[str, object]
    latency_ms: int
    attempt: int
    retries: int = 0
    error_code: str | None = None
    cache_hit: bool = False


@runtime_checkable
class CallRecorder(Protocol):
    async def record(self, call: ToolCallRecord) -> None: ...


class LoggingToolRecorder:
    """One structured line per attempt."""

    async def record(self, call: ToolCallRecord) -> None:
        logger.info(
            "tool call",
            extra={
                "tool": call.tool_name.value,
                "status": call.status.value,
                "latency_ms": call.latency_ms,
                "attempt": call.attempt,
                "retries": call.retries,
                "error_code": call.error_code,
                "cache_hit": call.cache_hit,
                **{f"request_{k}": v for k, v in call.request.items()},
                **{f"result_{k}": v for k, v in call.response_summary.items()},
            },
        )


class CollectingToolRecorder:
    """Keeps records in memory, for tests and per-run summaries."""

    def __init__(self) -> None:
        self.calls: list[ToolCallRecord] = []

    async def record(self, call: ToolCallRecord) -> None:
        self.calls.append(call)


@dataclass(frozen=True, slots=True)
class ToolResult[T]:
    """A successful tool call and what it cost to make."""

    value: T
    tool_name: ToolName
    latency_ms: int
    attempts: int = 1
    summary: Mapping[str, object] = field(default_factory=dict)


class ToolExecutor:
    """Bounds, retries, classifies and records every tool call.

    The retry policy is deliberately narrow. Only errors that declare themselves
    ``retryable`` are retried - a timeout, a 5xx, a rate limit. A refused URL is
    never retried, because retrying a blocked SSRF attempt is not a recovery
    strategy, it is the attack succeeding more slowly.
    """

    def __init__(
        self,
        *,
        recorder: CallRecorder | None = None,
        max_attempts: int = 3,
        timeout_seconds: float = 30.0,
        base_delay_seconds: float = 0.5,
        max_delay_seconds: float = 8.0,
        max_concurrent_calls: int = 8,
    ) -> None:
        self._recorder = recorder or LoggingToolRecorder()
        self._max_attempts = max_attempts
        self._timeout = timeout_seconds
        self._base_delay = base_delay_seconds
        self._max_delay = max_delay_seconds
        self._semaphore = asyncio.Semaphore(max_concurrent_calls)

    async def run[T](
        self,
        tool_name: ToolName,
        request: ToolInput,
        operation: Callable[[], Awaitable[T]],
        *,
        summarize: Callable[[T], Mapping[str, object]] | None = None,
    ) -> ToolResult[T]:
        """Execute one tool call under the shared policy."""
        request_summary = _summarize_request(request)
        last_error: ToolError | None = None

        for attempt in range(1, self._max_attempts + 1):
            started = time.perf_counter()
            try:
                async with self._semaphore, asyncio.timeout(self._timeout):
                    value = await operation()
            except (ToolError, TimeoutError) as exc:
                error = _as_tool_error(exc)
                last_error = error
                await self._recorder.record(
                    ToolCallRecord(
                        tool_name=tool_name,
                        status=_status_for(error),
                        request=request_summary,
                        response_summary={},
                        latency_ms=int((time.perf_counter() - started) * 1000),
                        attempt=attempt,
                        retries=attempt - 1,
                        error_code=error.code,
                    )
                )
                if error.retryable and attempt < self._max_attempts:
                    await asyncio.sleep(self._backoff(attempt, error))
                    continue
                raise error from exc

            latency_ms = int((time.perf_counter() - started) * 1000)
            summary = dict(summarize(value)) if summarize else {}
            await self._recorder.record(
                ToolCallRecord(
                    tool_name=tool_name,
                    status=ToolStatus.OK,
                    request=request_summary,
                    response_summary=summary,
                    latency_ms=latency_ms,
                    attempt=attempt,
                    retries=attempt - 1,
                )
            )
            return ToolResult(
                value=value,
                tool_name=tool_name,
                latency_ms=latency_ms,
                attempts=attempt,
                summary=summary,
            )

        raise last_error if last_error else AssertionError  # pragma: no cover

    def _backoff(self, attempt: int, error: ToolError) -> float:
        """Exponential backoff with jitter, honouring ``Retry-After``.

        Jitter because a fan-out of researchers that all fail at once and all
        retry at exactly 1.0s reproduces the burst that rate-limited them.
        """
        advised: float | None = getattr(error, "retry_after_seconds", None)
        if advised is not None:
            return min(float(advised), self._max_delay)
        exponential = self._base_delay * (2 ** (attempt - 1))
        jittered: float = exponential + random.uniform(0, self._base_delay)  # noqa: S311
        return min(jittered, self._max_delay)


def _status_for(error: ToolError) -> ToolStatus:
    """Map a failure onto the closed ``tool_calls.status`` vocabulary."""
    from app.sources.errors import FetchTimeout, UpstreamRateLimited

    if isinstance(error, UpstreamRateLimited):
        return ToolStatus.RATE_LIMITED
    if isinstance(error, FetchTimeout):
        return ToolStatus.TIMEOUT
    return ToolStatus.ERROR


def _as_tool_error(exc: BaseException) -> ToolError:
    """An executor-level timeout is a fetch timeout from the caller's view."""
    if isinstance(exc, ToolError):
        return exc
    from app.sources.errors import FetchTimeout

    return FetchTimeout(context={"error": str(exc) or type(exc).__name__})


def _summarize_request(request: ToolInput) -> Mapping[str, object]:
    """What of a request is safe and useful to log.

    Values are truncated: a query can be long, and a log line that carries a
    whole prompt is a log line nobody reads.
    """
    summary: dict[str, object] = {}
    for key, value in request.model_dump().items():
        if isinstance(value, str):
            summary[key] = value[:200]
        elif isinstance(value, int | float | bool) or value is None:
            summary[key] = value
        else:
            summary[key] = str(value)[:200]
    return summary
