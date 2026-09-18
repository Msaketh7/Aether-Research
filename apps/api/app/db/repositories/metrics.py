"""The live system panel, measured (Phase 17).

Four queries over a time window - runs, model calls, tool calls, and the
workers currently holding a lease - assembled into one ``SystemMetrics``. The
shapes are deliberately plain SQL aggregates: what makes this module correct is
not cleverness but the handling of *absence*, which is different in every one
of them.

* No run finished in the window: the success rate is ``None``, not ``0.0``.
* No model was called: tokens and cost are ``None``, not ``0``.
* A model was called whose price the registry does not declare: the total cost
  is ``None``. A partial sum presented as the total is the specific dishonesty
  the whole system is built to avoid, and it is one `COUNT(*) FILTER` away
  from being caught.
* Redis is unreachable: the queue depth is ``None``, and the readiness probe
  makes the same distinction for the same reason.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import Float, Select, and_, cast, func, or_, select
from sqlalchemy.sql.elements import ColumnElement

from app.core.enums import RunStatus
from app.core.logging import get_logger
from app.db.models.research import ResearchRunRow
from app.db.models.trace import LlmCallRow, ToolCallRow
from app.db.session import Database
from app.observability.system import WINDOW_SPANS, SystemMetrics
from app.workers.lifecycle import IN_FLIGHT_STATUSES
from app.workers.queue import JobQueue

logger = get_logger(__name__)

_TERMINAL = [status.value for status in RunStatus if status.is_terminal]
_IN_FLIGHT = [status.value for status in IN_FLIGHT_STATUSES]


class SqlAlchemySystemMetrics:
    """Reads the live panel from the rows the system already writes."""

    def __init__(self, database: Database, queue: JobQueue, *, lease_seconds: int) -> None:
        self._database = database
        self._queue = queue
        self._lease_seconds = lease_seconds

    async def snapshot(self, window: str = "24h") -> SystemMetrics:
        span = WINDOW_SPANS.get(window, WINDOW_SPANS["24h"])
        since = func.now() - span

        async with self._database.session() as session:
            runs = (await session.execute(self._runs_query(since))).one()
            calls = (await session.execute(self._llm_query(since))).one()
            tools = (await session.execute(self._tool_query(since))).one()
            workers = (await session.execute(self._workers_query())).scalar_one()

        finished = int(runs.finished or 0)
        model_calls = int(calls.calls or 0)
        tool_calls = int(tools.calls or 0)
        cacheable = model_calls + tool_calls
        cached = int(calls.cached or 0) + int(tools.cached or 0)

        return SystemMetrics(
            window=window,
            research_success_rate=(
                None if finished == 0 else round(int(runs.completed or 0) / finished, 4)
            ),
            research_failure_rate=(
                None if finished == 0 else round(int(runs.failed or 0) / finished, 4)
            ),
            latency_p50_seconds=_seconds(runs.p50),
            latency_p95_seconds=_seconds(runs.p95),
            latency_p99_seconds=_seconds(runs.p99),
            llm_latency_p95_ms=_rounded(calls.p95_ms),
            tool_latency_p95_ms=_rounded(tools.p95_ms),
            cache_hit_rate=None if cacheable == 0 else round(cached / cacheable, 4),
            total_tokens=None if model_calls == 0 else int(calls.tokens or 0),
            # One unpriced call and the total is not a total.
            total_cost_usd=(
                None
                if model_calls == 0 or int(calls.uncosted or 0) > 0
                else round(float(calls.cost or 0.0), 6)
            ),
            queue_depth=await self._depth(),
            active_workers=int(workers or 0),
        )

    # --- the queries ------------------------------------------------------

    def _runs_query(self, since: ColumnElement[dt.datetime]) -> Select[Any]:
        """Runs that reached a terminal status in the window, and how long they took.

        Measured from `started_at` rather than `created_at`: the time a run
        spent queued is the queue's latency, not the run's, and mixing them
        would make a backlog look like a slow graph.
        """
        duration = func.extract("epoch", ResearchRunRow.completed_at - ResearchRunRow.started_at)
        return select(
            func.count().label("finished"),
            func.count()
            .filter(ResearchRunRow.status == RunStatus.COMPLETED.value)
            .label("completed"),
            func.count().filter(ResearchRunRow.status == RunStatus.FAILED.value).label("failed"),
            func.percentile_cont(0.5).within_group(duration).label("p50"),
            func.percentile_cont(0.95).within_group(duration).label("p95"),
            func.percentile_cont(0.99).within_group(duration).label("p99"),
        ).where(
            ResearchRunRow.status.in_(_TERMINAL),
            ResearchRunRow.completed_at.is_not(None),
            ResearchRunRow.started_at.is_not(None),
            ResearchRunRow.completed_at >= since,
        )

    def _llm_query(self, since: ColumnElement[dt.datetime]) -> Select[Any]:
        return select(
            func.count().label("calls"),
            func.count().filter(LlmCallRow.cache_hit.is_(True)).label("cached"),
            func.count().filter(LlmCallRow.cost_usd.is_(None)).label("uncosted"),
            func.sum(LlmCallRow.prompt_tokens + LlmCallRow.completion_tokens).label("tokens"),
            func.sum(LlmCallRow.cost_usd).label("cost"),
            func.percentile_cont(0.95)
            .within_group(cast(LlmCallRow.latency_ms, Float))
            .label("p95_ms"),
        ).where(LlmCallRow.created_at >= since)

    def _tool_query(self, since: ColumnElement[dt.datetime]) -> Select[Any]:
        return select(
            func.count().label("calls"),
            func.count().filter(ToolCallRow.cache_hit.is_(True)).label("cached"),
            func.percentile_cont(0.95)
            .within_group(cast(ToolCallRow.latency_ms, Float))
            .label("p95_ms"),
        ).where(ToolCallRow.started_at >= since)

    def _workers_query(self) -> Select[tuple[int]]:
        """Distinct workers holding a run whose lease has not expired.

        The same definition the reconciliation sweep uses to decide a worker
        has died (ADR 0017), so the panel and the sweep can never disagree
        about who is alive.
        """
        expired = func.now() - dt.timedelta(seconds=self._lease_seconds)
        return select(func.count(func.distinct(ResearchRunRow.worker_id))).where(
            and_(
                ResearchRunRow.worker_id.is_not(None),
                ResearchRunRow.status.in_(_IN_FLIGHT),
                or_(
                    ResearchRunRow.heartbeat_at.is_(None),
                    ResearchRunRow.heartbeat_at > expired,
                ),
            )
        )

    async def _depth(self) -> int | None:
        """Pending jobs, or ``None`` when the queue could not be asked.

        Never 0 on a failure: an empty queue and an unreachable one look
        identical to a reader who is told zero, and they call for opposite
        actions.
        """
        try:
            return await self._queue.depth()
        except Exception as exc:
            logger.warning("could not read the queue depth", extra={"error": str(exc)})
            return None


def _seconds(value: Any) -> float | None:
    return None if value is None else round(float(value), 3)


def _rounded(value: Any) -> float | None:
    return None if value is None else round(float(value), 1)
