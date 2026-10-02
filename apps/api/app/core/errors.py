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


class InvalidCredentials(Unauthenticated):
    """The single refusal every failed credential check returns.

    One class, one message, so that no branch - an unknown address, a wrong
    password, an account with no password, a refusal relayed from Supabase -
    can become distinguishable from the others by its wording or its code.
    """

    code = "invalid_credentials"
    message = "That email and password do not match an account."


class EmailNotConfirmed(AppError):
    """A correct password for an address that has not been confirmed yet.

    Only ever raised *after* the password was accepted, so it tells a caller
    nothing they did not already prove they know. A 403 rather than a 401: the
    credential is right, the account is just not allowed in yet.
    """

    status_code = 403
    code = "email_not_confirmed"
    message = "Confirm your email address first. We sent you a link when you signed up."


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


class LastIdentity(AppError):
    """Refusing to remove somebody's only way of signing in.

    A 409 rather than a 403: nothing is forbidden about the request, the
    account is simply in a state where it cannot be granted. Adding a password
    or a second provider makes the same request succeed.
    """

    status_code = 409
    code = "last_identity"

    def __init__(
        self, message: str = "Set a password before unlinking your only sign-in method."
    ) -> None:
        super().__init__(message)


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


class EmailDeliveryUnavailable(DependencyUnavailable):
    """The confirmation email could not be sent, so no account was created.

    Almost always configuration rather than an outage: Supabase's built-in
    mailer only delivers to the project's own team, and refuses every other
    address until the project has custom SMTP. Reported as a 503 because there
    is nothing the person signing up can change to make it work.
    """

    code = "email_delivery_unavailable"
    message = "We could not send a confirmation email to that address. Please try again later."


class NotImplementedYet(AppError):
    """A documented endpoint whose implementing phase has not landed.

    Preferable to a stub that returns plausible-looking empty data: a caller
    can tell "nothing found" apart from "not built yet".
    """

    status_code = 501
    code = "not_implemented"
    message = "This capability is not implemented in the current build."
