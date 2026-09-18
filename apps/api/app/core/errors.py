"""Error taxonomy.

Every failure the API can produce is one of these classes. Two properties
matter and are enforced by construction:

* the wire shape always matches ``ApiErrorBody`` in ``@aether/shared-types``,
  so the frontend's error handling - already written and tested in Phase 1 -
  works against the real backend without change;
* ``message`` is safe to display. Internal detail goes to the logs under the
  request id, never to the client.
"""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    """Base class for every expected failure.

    ``code`` is stable and greppable; it is part of the API contract and must
    not be reworded casually.
    """

    status_code: int = 500
    code: str = "internal_error"
    message: str = "Something went wrong."

    def __init__(
        self,
        message: str | None = None,
        *,
        code: str | None = None,
        details: dict[str, list[str]] | None = None,
        context: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.message = message or self.message
        self.code = code or self.code
        self.details = details
        # Diagnostic context for the logs only. Never serialised to a client.
        self.context = context or {}
        # Response headers the failure itself carries - `Retry-After` on a
        # refusal that says when to come back. Part of the contract, unlike
        # `context`: a client that is told to wait cannot read a log line.
        self.headers = headers
        super().__init__(self.message)


# --- client errors --------------------------------------------------------


class ValidationFailed(AppError):
    status_code = 422
    code = "validation_failed"
    message = "Check the highlighted fields."


class NotFound(AppError):
    """Also used where a permission failure must not confirm existence."""

    status_code = 404
    code = "not_found"
    message = "Not found."


class RunNotFound(NotFound):
    code = "run_not_found"
    message = "No research run with that id."


class UploadNotFound(NotFound):
    code = "upload_not_found"
    message = "No uploaded document with that id."


class ReportNotReady(NotFound):
    """Distinct from `run_not_found`: the run exists, the report does not yet.

    The frontend treats this as an expected state rather than an error, which
    is why it needs its own code.
    """

    code = "report_not_ready"
    message = "This run has not produced a validated report yet."


class Unauthenticated(AppError):
    status_code = 401
    code = "unauthenticated"
    message = "Sign in to continue."


class Forbidden(AppError):
    status_code = 403
    code = "forbidden"
    message = "You do not have access to this resource."


class RegistrationClosed(AppError):
    """``REGISTRATION_ENABLED=false``. A deployment that provisions accounts
    some other way, saying so rather than silently accepting sign-ups."""

    status_code = 403
    code = "registration_closed"
    message = "This deployment does not accept new registrations."


class Conflict(AppError):
    status_code = 409
    code = "conflict"
    message = "That action conflicts with the current state."


class RunNotCancellable(Conflict):
    code = "run_not_cancellable"
    message = "This run has already finished."


class TooManyConcurrentRuns(AppError):
    status_code = 429
    code = "too_many_concurrent_runs"
    message = "You already have the maximum number of research runs in flight."


class RateLimited(AppError):
    """Too many requests from one identity for one class of route (Phase 20).

    Distinct from `too_many_concurrent_runs`, which is about how much work is
    in flight rather than how fast it was asked for; a client's response to the
    two is different, so the codes are.

    Always carries `Retry-After`, because a 429 without one is an invitation to
    retry immediately.
    """

    status_code = 429
    code = "rate_limited"
    message = "Too many requests. Try again shortly."


# --- server and dependency errors ----------------------------------------


class DependencyUnavailable(AppError):
    """A backing service (database, queue, provider) is not answering.

    Separate from `internal_error` because it is retryable and because it is the
    signal readiness probes and circuit breakers act on.
    """

    status_code = 503
    code = "dependency_unavailable"
    message = "A required service is temporarily unavailable."


class NotImplementedYet(AppError):
    """A documented endpoint whose implementing phase has not landed.

    Preferable to a stub that returns plausible-looking empty data: a caller
    can tell "nothing found" apart from "not built yet".
    """

    status_code = 501
    code = "not_implemented"
    message = "This capability is not implemented in the current build."
