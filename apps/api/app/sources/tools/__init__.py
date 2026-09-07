"""The six research tools.

One module per tool. Each declares a strict Pydantic input schema, returns a
typed result, and raises only from ``app/sources/errors.py``. None of them opens
its own HTTP client: they are handed the guarded one, which is what keeps the
SSRF controls in a single place.
"""

from __future__ import annotations
