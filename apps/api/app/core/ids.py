"""Identifier helpers.

UUID4 everywhere: ids appear in URLs, and a sequential id would let one user
enumerate another user's runs. Ownership is still checked on every read - an
unguessable id is a defence in depth, not an authorisation mechanism.
"""

from __future__ import annotations

import uuid


def new_id() -> uuid.UUID:
    return uuid.uuid4()


def parse_id(value: str) -> uuid.UUID | None:
    """Parse a path parameter without raising, so a malformed id can 404."""
    try:
        return uuid.UUID(value)
    except (ValueError, AttributeError, TypeError):
        return None
