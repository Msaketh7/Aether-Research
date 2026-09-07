"""Storage failure taxonomy.

Storage errors extend the application taxonomy in ``app/core/errors.py`` rather
than forming a parallel one, so a failure that reaches a request handler already
carries the right status code and the wire envelope the frontend understands.

The distinctions that matter operationally:

* ``ObjectNotFound`` is a *fact about the data*, not a fault. Callers routinely
  expect it (a cache miss, an artifact a run never produced).
* ``StorageUnavailable`` is *retryable* - the bucket is unreachable, the endpoint
  timed out. It is a 503, the same class Postgres and Redis outages produce, so
  readiness probes and the frontend's retry logic act on it without a special
  case.
* ``StorageError`` is a *misconfiguration or a bug* - a missing bucket, denied
  credentials, a malformed request. Retrying will not help, and it must be loud.

Confusing the second and third is the failure this split exists to prevent: a
credentials error reported as retryable makes a deployment look flaky instead of
broken.
"""

from __future__ import annotations

from app.core.errors import AppError, DependencyUnavailable, NotFound, ValidationFailed


class StorageError(AppError):
    """A storage operation failed for a reason retrying will not fix."""

    status_code = 500
    code = "storage_error"
    message = "The artifact store could not complete the request."


class StorageUnavailable(DependencyUnavailable):
    """The object store is unreachable or timing out. Retryable."""

    code = "storage_unavailable"
    message = "The artifact store is temporarily unavailable."


class ObjectNotFound(NotFound):
    """No object at that key.

    A ``NotFound`` subclass so that an artifact a run never produced reads as a
    404 rather than an internal error - the same treatment ``ReportNotReady``
    gets, and for the same reason.
    """

    code = "object_not_found"
    message = "No stored artifact with that key."


class ObjectTooLarge(AppError):
    """An object exceeds the configured size ceiling.

    Enforced on the way in *and* on the way out. A cap checked only at upload
    time is not a bound: objects arrive in the bucket by other routes (a
    presigned upload, a backfill, another service), and a read is where the
    memory is actually spent.
    """

    status_code = 413
    code = "object_too_large"
    message = "That artifact exceeds the maximum size this service will handle."


class InvalidStorageKey(ValidationFailed):
    """A key that would be unsafe or unaddressable.

    Keys are derived in ``app/storage/keys.py``, never taken from a caller - so
    in normal operation this signals a bug. It is a 4xx because Phase 7's
    presigned-upload flow will let a client echo a key back, and that path must
    reject a traversal attempt as bad input rather than as a server fault.
    """

    code = "invalid_storage_key"
    message = "That artifact key is not valid."
