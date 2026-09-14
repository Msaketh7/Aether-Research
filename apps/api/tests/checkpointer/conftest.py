"""Tests that open LangGraph's Postgres checkpointer, which talks through psycopg.

psycopg's async mode refuses Windows' default proactor event loop outright, so
these tests - and only these - run on a selector loop.

The hook lives in a conftest of its own directory rather than behind a marker in
the root one. pytest-asyncio asks it about every test collected under the
directory that defines it, and requires a mapping back for each; defined at the
root, it would move the whole suite. On Linux, where the service runs, a
selector loop is the default anyway.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable

import pytest


def pytest_asyncio_loop_factories(
    config: pytest.Config, item: pytest.Item
) -> dict[str, Callable[[], asyncio.AbstractEventLoop]]:
    return {"selector": asyncio.SelectorEventLoop}
