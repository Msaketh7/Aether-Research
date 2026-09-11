"""The parser child process: one document's bytes in, one JSON object out.

Started by ``IsolatedParser`` as ``python -I -m app.retrieval.parse_worker`` with
a scrubbed environment; never imported by the application. The protocol:

* stdin - one line of JSON (format, charset, limits), then the raw bytes;
* stdout - one JSON object: ``{"ok": true, "document": ...}`` or
  ``{"ok": false, "error": {"code": ..., "message": ..., "context": ...}}``;
* exit status 0 whenever a result was written. Anything else - a crash, a kill
  at the deadline, the kernel enforcing the memory ceiling - leaves stdout
  empty, and the parent classifies it as an unreadable document.

Before it reads a byte of input the process removes its own ability to open a
network connection, and on POSIX it lowers its address-space and CPU ceilings.
"""

from __future__ import annotations

import json
import logging
import sys
from typing import Any, NoReturn

#: The header is a few hundred bytes; a longer first line is not a header.
_MAX_HEADER_BYTES = 64 * 1024


def refuse_network() -> None:
    """Make every Python-level connection attempt fail.

    Defence in depth, not a sandbox: native code could still make the syscall.
    But no reader here has a reason to touch the network - trafilatura is
    called with fetching off and pypdf never fetches - so the only code that
    would try is code that should not be running.
    """
    import socket

    def refuse(*_args: Any, **_kwargs: Any) -> NoReturn:
        raise OSError("Network access is disabled in the document parser process.")

    socket.socket.connect = refuse  # type: ignore[method-assign]
    socket.socket.connect_ex = refuse  # type: ignore[method-assign]
    socket.create_connection = refuse
    socket.getaddrinfo = refuse


def limit_resources(max_memory_bytes: int, cpu_seconds: int) -> None:
    """Lower the kernel's ceilings, where the platform has them.

    POSIX only. On Windows the parent's deadline is the one bound, which is why
    that deadline kills the process rather than abandoning it.
    """
    if sys.platform != "win32":
        import resource

        if max_memory_bytes > 0:
            resource.setrlimit(resource.RLIMIT_AS, (max_memory_bytes, max_memory_bytes))
        if cpu_seconds > 0:
            resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))


def main() -> int:
    refuse_network()
    # pypdf logs a warning per malformed object, and a hostile file can have
    # thousands. Here stderr is a diagnostic tail, not a log sink.
    logging.disable(logging.WARNING)

    stdin = sys.stdin.buffer
    try:
        header = json.loads(stdin.readline(_MAX_HEADER_BYTES))
        limit_resources(int(header.get("max_memory_bytes", 0)), int(header.get("cpu_seconds", 0)))
        result = _parse(header, stdin.read())
    except MemoryError:
        result = _failure(
            "document_too_large", "That document needs more memory to read than is allowed."
        )
    except Exception as exc:  # the parent classifies; the child must still answer
        result = _failure("document_unreadable", None, error=type(exc).__name__)

    sys.stdout.buffer.write(json.dumps(result, ensure_ascii=False).encode("utf-8"))
    sys.stdout.buffer.flush()
    return 0


def _parse(header: dict[str, Any], data: bytes) -> dict[str, Any]:
    # Imported only after the process is hardened, so an import-time side
    # effect in a reader's dependency runs under the same restrictions.
    from app.core.enums import DocumentFormat
    from app.retrieval.errors import IngestionError
    from app.retrieval.parsers import ParseLimits, parse_document

    try:
        document = parse_document(
            data,
            fmt=DocumentFormat(str(header["format"])),
            charset=header.get("charset") or None,
            source_label=str(header["source_label"]),
            limits=ParseLimits(
                max_pdf_pages=int(header["max_pdf_pages"]),
                max_chars=int(header["max_chars"]),
            ),
        )
    except IngestionError as exc:
        context = {
            key: value
            for key, value in exc.context.items()
            if value is None or isinstance(value, str | int | float | bool)
        }
        return _failure(exc.code, exc.message, **context)
    return {"ok": True, "document": document.to_wire()}


def _failure(code: str, message: str | None, **context: object) -> dict[str, Any]:
    return {"ok": False, "error": {"code": code, "message": message, "context": context}}


if __name__ == "__main__":
    raise SystemExit(main())
