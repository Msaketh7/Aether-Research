"""The isolated parser: the same answers as in-process, and bounded when the child misbehaves.

Each bound is proved by standing in a child that breaks it - one that hangs,
one that dies, one that floods its output, one that reports an error the parent
does not know. The real child is exercised too, end to end, including the two
properties that matter if a parser is ever exploited: no secrets in its
environment, and no network.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time

import pytest

from app.core.config import parser_environment
from app.core.enums import DocumentFormat
from app.retrieval.errors import (
    DocumentEncrypted,
    DocumentTooLarge,
    DocumentUnreadable,
    ParseTimeout,
)
from app.retrieval.isolation import InProcessParser, IsolatedParser
from app.retrieval.parsers import ParseLimits
from tests.support.documents import encrypt_pdf, make_pdf

LIMITS = ParseLimits(max_pdf_pages=50, max_chars=200_000)
PYTHON = [sys.executable, "-I", "-c"]


def isolated(**overrides: object) -> IsolatedParser:
    options: dict[str, object] = {
        "limits": LIMITS,
        "timeout_seconds": 60.0,
        "max_memory_bytes": 2 * 1024**3,
        "max_concurrent": 2,
    }
    options.update(overrides)
    return IsolatedParser(**options)  # type: ignore[arg-type]


async def parse_with(parser: IsolatedParser | InProcessParser, data: bytes):
    return await parser.parse(
        data, fmt=DocumentFormat.PDF, charset=None, source_label="upload://a.pdf"
    )


async def test_the_isolated_parser_returns_what_the_in_process_parser_does():
    pdf = make_pdf(
        ["First page of the filing.", "Second page, with more text."],
        title="Annual report",
        author="Acme",
    )
    remote = await parse_with(isolated(), pdf)
    local = await parse_with(InProcessParser(LIMITS), pdf)

    assert remote.text.expose() == local.text.expose()
    assert remote.pages == local.pages
    assert (remote.title, remote.author) == ("Annual report", "Acme")
    assert remote.metadata["page_count"] == 2


async def test_a_document_error_in_the_child_is_raised_here_as_the_same_class():
    pdf = encrypt_pdf(make_pdf(["secret"]))
    with pytest.raises(DocumentEncrypted) as caught:
        await parse_with(isolated(), pdf)
    assert caught.value.context["reported_by"] == "parser_process"


async def test_a_child_that_hangs_is_killed_at_the_deadline():
    parser = isolated(timeout_seconds=1.5, command=[*PYTHON, "import time; time.sleep(60)"])
    started = time.perf_counter()
    with pytest.raises(ParseTimeout):
        await parse_with(parser, b"%PDF-1.4")
    assert time.perf_counter() - started < 15


async def test_a_child_that_dies_without_answering_is_an_unreadable_document():
    parser = isolated(command=[*PYTHON, "import sys; sys.exit(3)"])
    with pytest.raises(DocumentUnreadable) as caught:
        await parse_with(parser, b"%PDF-1.4")
    assert caught.value.context["exit_code"] == 3


async def test_output_past_the_bound_is_refused_not_parsed():
    parser = isolated(
        limits=ParseLimits(max_pdf_pages=1, max_chars=10),
        command=[*PYTHON, "import sys; sys.stdout.write('x' * (2 * 1024 * 1024))"],
    )
    with pytest.raises(DocumentTooLarge):
        await parse_with(parser, b"%PDF-1.4")


async def test_an_error_code_the_parent_does_not_know_is_not_trusted():
    """The error class comes from a fixed table, never from the child's output."""
    payload = json.dumps({"ok": False, "error": {"code": "internal_error", "message": "x"}})
    parser = isolated(command=[*PYTHON, f"import sys; sys.stdout.write({payload!r})"])
    with pytest.raises(DocumentUnreadable):
        await parse_with(parser, b"%PDF-1.4")


def test_the_child_environment_withholds_every_secret(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@db/aether")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "not-real")

    environment = parser_environment()

    assert not {"OPENAI_API_KEY", "DATABASE_URL", "AWS_SECRET_ACCESS_KEY"} & set(environment)
    assert "PATH" in environment


def test_the_real_child_can_neither_see_secrets_nor_open_a_connection(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")
    probe = (
        "import os, socket\n"
        "from app.retrieval.parse_worker import refuse_network\n"
        "print('OPENAI_API_KEY' in os.environ)\n"
        "refuse_network()\n"
        "socket.create_connection(('127.0.0.1', 9), timeout=1)\n"
    )
    completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [*PYTHON, probe],
        capture_output=True,
        env=parser_environment(),
        timeout=60,
        check=False,
    )
    assert completed.stdout.decode().strip() == "False"
    assert completed.returncode != 0
    assert b"Network access is disabled" in completed.stderr
