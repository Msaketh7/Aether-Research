"""Parsing in a child process that can be killed (threat model 3.6).

A PDF is a program for a page-description interpreter, and the readers for it -
like those for HTML - have a long history of bugs on crafted input. Such a bug
shows up as a hang, a memory blow-up, or (rarely, and worst) code execution.
Running the parser inside the worker would make each of those the worker's
problem: a research run that never finishes, an out-of-memory kill that takes
every other run in the process with it, or attacker code running with the
worker's database URL and provider keys in its environment.

So each document is parsed in a fresh Python process that

* is **killed** at a deadline, not merely abandoned - a thread that times out
  keeps burning CPU; a process that times out stops;
* runs with a **scrubbed environment** - no API keys, no database URL, nothing
  the settings layer reads - so code execution in the parser finds no
  credentials to steal;
* refuses Python-level **network access** before it reads a byte, and on POSIX
  runs under an **address-space and CPU ceiling**;
* speaks a narrow protocol: the document's bytes in on stdin, one JSON object
  out on stdout, both bounded.

The price is a process start per document - about a second on a laptop, most
of it importing the readers - which is small beside the parse and embedding
time of any document worth ingesting.
"""

from __future__ import annotations

import asyncio
import json
import math
import subprocess
import sys
import tempfile
import time
from collections.abc import Sequence
from typing import Protocol

from app.core.config import parser_environment
from app.core.enums import DocumentFormat
from app.core.logging import get_logger
from app.retrieval.errors import (
    PARSE_ERRORS,
    DocumentTooLarge,
    DocumentUnreadable,
    IngestionError,
    ParseTimeout,
)
from app.retrieval.parsed import ParsedDocument
from app.retrieval.parsers import ParseLimits, parse_document

logger = get_logger(__name__)

#: The child's entry point. Run under ``-I`` (isolated mode), which ignores
#: every ``PYTHON*`` variable and the working directory, so nothing in the
#: environment can change which code the child executes.
WORKER_MODULE = "app.retrieval.parse_worker"

#: How much of the child's stderr reaches the log. stderr is where a hostile
#: PDF's thousand parser warnings would go, so only a tail is kept.
_STDERR_TAIL_BYTES = 2000

#: Headroom for the JSON envelope around the text.
_ENVELOPE_BYTES = 1024 * 1024


class DocumentParser(Protocol):
    """Turns a document's bytes into normalised text. Raises ``IngestionError``."""

    async def parse(
        self,
        data: bytes,
        *,
        fmt: DocumentFormat,
        charset: str | None,
        source_label: str,
    ) -> ParsedDocument: ...


class InProcessParser:
    """Parses in this process, on a thread. For tests and tooling, never the worker.

    The same readers and limits as the isolated path and none of its
    protection: a parser hang here hangs the caller, and a parser exploit runs
    with the caller's credentials.
    """

    def __init__(self, limits: ParseLimits) -> None:
        self._limits = limits

    async def parse(
        self,
        data: bytes,
        *,
        fmt: DocumentFormat,
        charset: str | None,
        source_label: str,
    ) -> ParsedDocument:
        return await asyncio.to_thread(
            parse_document,
            data,
            fmt=fmt,
            charset=charset,
            source_label=source_label,
            limits=self._limits,
        )


class IsolatedParser:
    """Parses each document in a scrubbed, bounded, killable child process."""

    def __init__(
        self,
        *,
        limits: ParseLimits,
        timeout_seconds: float,
        max_memory_bytes: int,
        max_concurrent: int,
        command: Sequence[str] | None = None,
    ) -> None:
        self._limits = limits
        self._timeout = timeout_seconds
        self._max_memory = max_memory_bytes
        # Overridable so a test can stand in a child that hangs, crashes or
        # floods its output, which is how the bounds are proved.
        self._command = list(command) if command else [sys.executable, "-I", "-m", WORKER_MODULE]
        self._semaphore = asyncio.Semaphore(max_concurrent)
        # JSON can spend six bytes on one character (a backslash-u escape).
        # Output past this is not a document the limits let through.
        self._max_output_bytes = limits.max_chars * 6 + _ENVELOPE_BYTES

    @property
    def command(self) -> tuple[str, ...]:
        """The argv every parse runs: fixed, no shell, this repository's own module."""
        return tuple(self._command)

    async def parse(
        self,
        data: bytes,
        *,
        fmt: DocumentFormat,
        charset: str | None,
        source_label: str,
    ) -> ParsedDocument:
        header = {
            "format": fmt.value,
            "charset": charset,
            "source_label": source_label,
            "max_pdf_pages": self._limits.max_pdf_pages,
            "max_chars": self._limits.max_chars,
            "max_memory_bytes": self._max_memory,
            # A second barrier behind the wall-clock deadline, enforced by the
            # kernel rather than by this process.
            "cpu_seconds": math.ceil(self._timeout) + 5,
        }
        payload = json.dumps(header).encode("utf-8") + b"\n" + data

        started = time.perf_counter()
        async with self._semaphore:
            completed = await asyncio.to_thread(self._run, payload)
        try:
            document = self._result(completed, source_label=source_label)
        except IngestionError as exc:
            logger.info(
                "document parse refused",
                extra={
                    "format": fmt.value,
                    "bytes": len(data),
                    "code": exc.code,
                    "latency_ms": int((time.perf_counter() - started) * 1000),
                },
            )
            raise
        logger.info(
            "document parsed",
            extra={
                "format": fmt.value,
                "bytes": len(data),
                "chars": len(document.text),
                "method": document.extraction_method,
                "latency_ms": int((time.perf_counter() - started) * 1000),
            },
        )
        return document

    def _run(self, payload: bytes) -> subprocess.CompletedProcess[bytes]:
        try:
            return subprocess.run(  # noqa: S603 - fixed argv, no shell, scrubbed environment
                self._command,
                input=payload,
                capture_output=True,
                timeout=self._timeout,
                # The allowlist lives in the settings module, the one reader
                # of the environment (see parser_environment).
                env=parser_environment(),
                cwd=tempfile.gettempdir(),
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            # subprocess.run has already killed and reaped the child by the
            # time this is raised, which is the whole reason for a process.
            raise ParseTimeout(context={"timeout_seconds": self._timeout}) from exc

    def _result(
        self, completed: subprocess.CompletedProcess[bytes], *, source_label: str
    ) -> ParsedDocument:
        if len(completed.stdout) > self._max_output_bytes:
            raise DocumentTooLarge(
                context={"output_bytes": len(completed.stdout), "limit": self._max_output_bytes}
            )
        try:
            result = json.loads(completed.stdout)
        except ValueError:
            result = None

        if not isinstance(result, dict):
            logger.warning(
                "document parser process produced no result",
                extra={
                    "exit_code": completed.returncode,
                    "stderr_tail": completed.stderr[-_STDERR_TAIL_BYTES:].decode(
                        "utf-8", "replace"
                    ),
                },
            )
            raise DocumentUnreadable(
                "The document parser stopped before producing a result.",
                context={"exit_code": completed.returncode},
            )

        document = result.get("document")
        if result.get("ok") is True and isinstance(document, dict):
            return ParsedDocument.from_wire(document, source_label=source_label)
        raise _reported_error(result.get("error"))


def _reported_error(error: object) -> IngestionError:
    """Re-raise, in this process, the error the child reported.

    The class comes from a fixed table, never from the child's output, so a
    child that writes an unexpected code gets the generic "unreadable" answer.
    """
    if not isinstance(error, dict):
        return DocumentUnreadable(context={"reported_by": "parser_process"})
    error_class = PARSE_ERRORS.get(str(error.get("code")), DocumentUnreadable)
    message = error.get("message")
    raw_context = error.get("context")
    context: dict[str, object] = {"reported_by": "parser_process"}
    if isinstance(raw_context, dict):
        context.update({str(key): value for key, value in raw_context.items()})
    return error_class(
        message if isinstance(message, str) and 0 < len(message) <= 300 else None,
        context=context,
    )
