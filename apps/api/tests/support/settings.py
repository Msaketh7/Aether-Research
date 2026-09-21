"""Building a valid ``Settings`` for any environment.

Since ADR 0022 a deployment outside ``local`` and ``test`` must carry a JWT
signing key, because generating an ephemeral one would mean each instance
signing with a different key - a token issued by one refused by every other,
and everybody signed out on every restart. The validator refuses that, so a
test that wants a `staging` or `production` Settings has to supply a key.

Generated per call rather than pinned as a literal. A private key committed to
a repository is a finding whether or not anything real uses it, and generating
a P-256 key costs well under a millisecond.
"""

from __future__ import annotations

from typing import Any

from app.core.config import Settings


def signing_key_pem() -> str:
    from joserfc.jwk import ECKey

    return ECKey.generate_key("P-256").as_pem(private=True).decode()


def settings_for(app_env: str, **overrides: Any) -> Settings:
    """A valid ``Settings`` for ``app_env``, with a key where one is required."""
    values: dict[str, Any] = {"app_env": app_env, **overrides}
    if app_env not in {"local", "test"} and "jwt_private_key" not in values:
        values["jwt_private_key"] = signing_key_pem()
    return Settings(**values)
