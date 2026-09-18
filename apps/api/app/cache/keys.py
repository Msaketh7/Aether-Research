"""What a cache entry is keyed by, and what invalidates it (TDD 13).

Three rules, and each of them is an invalidation rule as much as a naming one.

**A key is a content hash, never a human-readable string.** The inputs are
canonicalised to JSON and hashed, so "the same search" means the same query,
the same domains, the same recency window and the same result count - not a
string that happens to look similar. A caller cannot accidentally widen a key
by forgetting to include a parameter, because the key is built from the
parameters rather than written out beside them.

**Every key carries a schema version.** The cached value is an encoding of a
type this codebase owns, and changing that type changes what the bytes mean.
Bumping ``KEY_VERSION`` invalidates every entry in one edit, which is the only
invalidation that is reliable across a deployment that is half old and half
new: the two halves simply do not read each other's entries.

**Every key carries its namespace**, so a TTL and a policy apply to a whole
class of entries rather than to whichever call sites remembered.
"""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from typing import Any

#: Bumped whenever a cached value's encoding changes. See the module docstring.
KEY_VERSION = "v1"

_PREFIX = "aether:cache"


class CacheNamespace(StrEnum):
    """What may be cached. A closed list, because each entry is a decision.

    Absent on purpose: retrieval results (freshness matters per run, and they
    are the user's own corpus), a run's evidence and reports (they are rows,
    and a cache in front of the system of record is a second source of truth),
    and model completions other than embeddings - see ``app.models.gateway``.
    """

    #: A provider's answer to one query. Public, short-lived (TDD 13).
    SEARCH = "search"
    #: A fetched page, by URL. Public; the archived bytes.
    PAGE = "page"
    #: The readable article extracted from a page's HTML, by content hash. A
    #: deterministic, CPU-bound transformation of bytes we already have.
    EXTRACT = "extract"
    #: One text's vector, by text and model. Deterministic by definition.
    EMBEDDING = "embedding"


def fingerprint(*parts: Any) -> str:
    """A stable hash of the inputs that decide a value.

    Canonical JSON rather than ``repr`` or a joined string: sorted keys, no
    insignificant whitespace, and an explicit failure on anything that is not
    JSON-serialisable. A key built from ``str(obj)`` would change with a
    dataclass field order and silently drop the whole cache.
    """
    canonical = json.dumps(parts, sort_keys=True, separators=(",", ":"), default=_unsupported)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def cache_key(namespace: CacheNamespace, *parts: Any) -> str:
    return f"{_PREFIX}:{KEY_VERSION}:{namespace.value}:{fingerprint(*parts)}"


def _unsupported(value: Any) -> str:
    raise TypeError(
        f"{type(value).__name__} cannot be part of a cache key: keys are built from "
        "JSON-serialisable values so that they are stable across processes."
    )
