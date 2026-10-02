"""Decisions about the test suite itself, held where they cannot quietly lapse."""

from __future__ import annotations

import re
from pathlib import Path

TESTS = Path(__file__).parent


def test_every_async_test_runs_on_the_suites_one_event_loop() -> None:
    """No module opts into anyio's pytest runner.

    The suite runs pytest-asyncio in auto mode: every async test and every
    async fixture shares one function-scoped loop. A module marked
    `pytest.mark.anyio` has its test bodies run on anyio's runner instead, while
    its async fixtures - `database` among them - can still be served by
    pytest-asyncio. Whether the two loops coincide then depends on runner
    lifetimes and test order, so it passes locally and fails in CI: on PR #4
    every store test on one xdist worker reused a pooled connection opened on
    the fixtures' loop and failed with "attached to a different loop".
    """
    marked = sorted(
        str(path.relative_to(TESTS))
        for path in TESTS.rglob("*.py")
        if path.name != Path(__file__).name
        and re.search(r"pytest\.mark\.anyio|\banyio_backend\b", path.read_text(encoding="utf-8"))
    )

    assert marked == [], (
        "These test modules opt into anyio's runner; use pytest-asyncio's auto mode "
        f"like the rest of the suite: {marked}"
    )
