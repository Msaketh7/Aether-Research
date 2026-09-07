"""Model-call failure taxonomy.

The whole point of a gateway is that a caller reacts to *what went wrong*, not
to whichever SDK exception happened to surface. Three distinctions drive real
behaviour and each is a separate class:

* **Retryable on the same model** - a 429, a 5xx, a timeout, a dropped
  connection. The gateway backs off and tries again.
* **Retryable on a different model** - the context window is too small, the
  model is not configured, the provider is down entirely. Backing off will not
  help; the fallback chain will.
* **Not retryable at all** - a malformed request, a rejected credential, a
  response that will not validate. Retrying burns the budget and still fails.

Collapsing these is how a system ends up retrying an invalid API key sixty
times, or failing a run outright over a transient rate limit.
"""

from __future__ import annotations

from typing import Any

from app.core.errors import AppError


class ModelError(AppError):
    """Base for every model-call failure."""

    status_code = 502
    code = "model_error"
    message = "The language model could not complete this request."

    #: Whether backing off and retrying *the same model* could succeed.
    retryable: bool = False
    #: Whether trying a *different* model could succeed.
    failover: bool = False


# --- retryable on the same model -----------------------------------------


class ProviderRateLimited(ModelError):
    """The provider is throttling. Back off and retry."""

    status_code = 429
    code = "provider_rate_limited"
    message = "The language model provider is rate limiting this request."
    retryable = True
    failover = True

    def __init__(self, *args: Any, retry_after_seconds: float | None = None, **kwargs: Any) -> None:
        # The provider usually knows better than our backoff curve does.
        self.retry_after_seconds = retry_after_seconds
        super().__init__(*args, **kwargs)


class ProviderTimeout(ModelError):
    status_code = 504
    code = "provider_timeout"
    message = "The language model did not respond in time."
    retryable = True
    failover = True


class ProviderUnavailable(ModelError):
    """A 5xx or a connection failure."""

    status_code = 503
    code = "provider_unavailable"
    message = "The language model provider is temporarily unavailable."
    retryable = True
    failover = True


# --- retryable only on a different model ---------------------------------


class ContextWindowExceeded(ModelError):
    """The prompt does not fit.

    Failover-only: retrying the same model with the same prompt is guaranteed to
    fail again, but a model with a larger window may succeed.
    """

    status_code = 413
    code = "context_window_exceeded"
    message = "This request is too large for the selected model."
    failover = True


class CapabilityNotSupported(ModelError):
    """The model cannot do what was asked - embeddings, token counting, tools.

    Raised instead of degrading silently. Anthropic has no embeddings endpoint;
    returning an empty vector would poison a retrieval index in a way that only
    shows up as bad search results weeks later.
    """

    status_code = 501
    code = "capability_not_supported"
    message = "The selected model does not support that operation."
    failover = True


class ModelNotConfigured(ModelError):
    """The registry has no such model, or its provider has no credentials.

    A configuration defect, surfaced at the point of use because that is the
    only place that knows which role wanted it.
    """

    status_code = 500
    code = "model_not_configured"
    message = "No model is configured for that role."


# --- not retryable --------------------------------------------------------


class ProviderRejectedRequest(ModelError):
    """A 4xx that is our fault: malformed request, bad credential, bad model id."""

    status_code = 502
    code = "provider_rejected_request"
    message = "The language model provider rejected this request."


class ProviderRefused(ModelError):
    """The model declined to answer on safety grounds.

    A first-class outcome, not an error to swallow: a research run that hits a
    refusal must record it and say so, because silently returning empty text
    would look like a source with nothing in it.
    """

    status_code = 422
    code = "provider_refused"
    message = "The language model declined to answer this request."


class StructuredOutputInvalid(ModelError):
    """The response did not validate against the requested schema.

    Kept separate from a provider error because the fix is a prompt or schema
    change, not a retry - and because the evaluation suite needs to count these.
    """

    status_code = 502
    code = "structured_output_invalid"
    message = "The language model returned a response that did not match the expected shape."
    # One retry is often enough when the provider has no native constrained
    # decoding, so this is retryable but never a reason to change model.
    retryable = True
