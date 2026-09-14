"""Research graph failure taxonomy.

The graph separates failures a run can carry on from - a researcher that timed
out, a verifier that raised - from those it cannot. The first kind is recorded
in the state (``failed_tasks``, ``errors``) and the run continues towards a
report. The second kind is raised as one of these, and the worker (Phase 13)
marks the run failed: there is no plan to research, no report to return, or a
node broke its contract.

``retryable`` is what the worker reads to decide whether running the job again
could succeed. A failed model call has already been retried by the gateway, but
an outage can outlast its retries; a broken contract fails the same way every
time.
"""

from __future__ import annotations

from app.core.errors import AppError


class GraphError(AppError):
    """Base for every failure that ends a research run."""

    status_code = 500
    code = "research_graph_failed"
    message = "The research workflow could not complete."

    retryable: bool = False


class PlanningFailed(GraphError):
    """The first planning round failed, so there is nothing to research."""

    code = "planning_failed"
    message = "The research plan could not be created."
    retryable = True


class SynthesisFailed(GraphError):
    code = "synthesis_failed"
    message = "The report could not be written."
    retryable = True


class CitationValidationFailed(GraphError):
    """The validator could not run. Not the same as citations that failed to
    validate, which is a routed outcome with one repair attempt."""

    code = "citation_validation_failed"
    message = "The report's citations could not be checked."
    retryable = True


class NodeContractViolated(GraphError):
    """A node returned something other than what its Protocol promises.

    A bug in the node, raised rather than routed around: recording it and
    carrying on would hand every later node input it cannot trust.
    """

    code = "node_contract_violated"
    message = "An internal error stopped the research workflow."


class CheckpointSchemaOutdated(GraphError):
    """The checkpoint tables are older than the installed checkpointer expects.

    Raised when the checkpointer is opened, not on the first write: a worker
    must not start taking runs it cannot save.
    """

    code = "checkpoint_schema_outdated"
    message = (
        "The checkpoint tables are older than this build expects. Run the database migrations."
    )
