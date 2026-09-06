"""Worker entry point.

The second process type from ADR 0001: it consumes the queue and executes the
research graph. **Neither exists yet** - the LangGraph workflow is Phase 9 and
the durable worker loop is Phase 13.

This module is deliberately a refusal rather than a stub that marks runs failed
or fabricates progress. A queued run staying queued is the truthful state of a
build with no worker, and the API reports it as such.
"""

from __future__ import annotations

import sys


def main() -> int:
    sys.stderr.write(
        "The research worker is implemented in Phase 13 and executes the graph "
        "built in Phase 9. Runs enqueued by the API will remain 'queued' until "
        "then.\n"
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
