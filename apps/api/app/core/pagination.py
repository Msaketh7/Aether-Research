"""Cursor pagination.

Every list endpoint is bounded. An unbounded query is a production incident
waiting for the first user with a long history, so the page size is clamped
here rather than trusted from the caller.
"""

from __future__ import annotations

import base64
import binascii

from pydantic import BaseModel, Field

from app.core.errors import ValidationFailed


class Page[T](BaseModel):
    """Wire shape of every list response. Mirrors `Page<T>` in shared-types."""

    items: list[T]
    next_cursor: str | None = None
    total: int | None = None


def encode_cursor(value: str) -> str:
    """Opaque cursor. Opaque so its meaning can change without breaking clients."""
    return base64.urlsafe_b64encode(value.encode()).decode().rstrip("=")


def decode_cursor(cursor: str) -> str:
    padding = "=" * (-len(cursor) % 4)
    try:
        return base64.urlsafe_b64decode(cursor + padding).decode()
    except (binascii.Error, UnicodeDecodeError) as exc:
        raise ValidationFailed(
            "That pagination cursor is not valid.",
            details={"cursor": ["Malformed cursor."]},
        ) from exc


class PageParams(BaseModel):
    """Validated pagination inputs."""

    limit: int = Field(default=20, ge=1, le=100)
    cursor: str | None = None

    @classmethod
    def clamped(
        cls,
        limit: int | None,
        cursor: str | None,
        *,
        default: int,
        maximum: int,
    ) -> PageParams:
        resolved = default if limit is None else max(1, min(limit, maximum))
        return cls(limit=resolved, cursor=cursor)
