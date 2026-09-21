"""Deployment-safety gates.

Each test here corresponds to a finding from the pre-release security audit.
They are separated from the feature suites on purpose: these assert that a
*misconfiguration* fails safely, which is a different question from whether a
feature works, and it is the question nobody thinks to re-check after a
refactor.

The shared theme is the direction of failure. Every gate is written so that a
value nobody anticipated - a new environment name, an unfamiliar CORS setting -
lands on the closed side.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.auth.principal import (
    DEV_PRINCIPAL,
    DEVELOPMENT_ENVIRONMENTS,
    development_identity_allowed,
    resolve_development_principal,
)
from app.core.config import Settings
from app.core.errors import Unauthenticated
from tests.support.settings import settings_for

# --- the development identity ---------------------------------------------


@pytest.mark.parametrize("environment", ["staging", "production"])
def test_the_development_identity_is_refused_outside_development(environment: str):
    """The audit finding: gating on "not production" left `staging` open.

    A staging deployment is usually internet-reachable and often carries a copy
    of real data, so `X-Aether-User: <any uuid>` there is a complete
    authentication bypass - and it is the environment people forget to lock
    down.
    """
    settings = settings_for(environment)  # type: ignore[arg-type]

    assert not development_identity_allowed(settings)
    with pytest.raises(Unauthenticated) as raised:
        resolve_development_principal(settings, None)

    assert raised.value.code == "unauthenticated"


@pytest.mark.parametrize("environment", ["staging", "production"])
def test_the_impersonation_header_is_refused_outside_development(environment: str):
    """Supplying the header must not be a way around the gate."""
    settings = settings_for(environment)  # type: ignore[arg-type]

    with pytest.raises(Unauthenticated):
        resolve_development_principal(settings, "00000000-0000-4000-8000-000000000042")


@pytest.mark.parametrize("environment", sorted(DEVELOPMENT_ENVIRONMENTS))
def test_development_environments_still_resolve_a_principal(environment: str):
    """The gate must not break the development workflow it is protecting."""
    settings = settings_for(environment)  # type: ignore[arg-type]

    assert resolve_development_principal(settings, None) == DEV_PRINCIPAL


def test_the_gate_is_an_allowlist_so_a_new_environment_is_closed():
    """The property that matters more than today's environment list.

    A `Environment` literal gains a value one day. Under an allowlist it is
    closed until someone decides otherwise; under a "not production" check it
    would be open the moment it is added, and nobody would notice.
    """
    from typing import get_args

    from app.core.config import Environment

    declared = set(get_args(Environment))
    assert DEVELOPMENT_ENVIRONMENTS.issubset(declared)
    assert declared - DEVELOPMENT_ENVIRONMENTS, "there must be a closed environment to test"
    for environment in declared - DEVELOPMENT_ENVIRONMENTS:
        with pytest.raises(Unauthenticated):
            resolve_development_principal(  # type: ignore[arg-type]
                settings_for(environment), None
            )


def test_the_dev_identity_flag_can_only_close_the_gate():
    """``DEV_IDENTITY_ENABLED`` narrows the allowlist and cannot widen it.

    It exists so the tests that exercise real sign-in can switch the
    affordance off without pretending to be another environment. A flag that
    could also switch it *on* would be a second way into the development
    identity, which is the whole class of finding this file records.
    """
    assert development_identity_allowed(settings_for("local"))
    assert not development_identity_allowed(settings_for("local", dev_identity_enabled=False))
    for environment in ("staging", "production"):
        settings = settings_for(environment, dev_identity_enabled=True)  # type: ignore[arg-type]
        assert not development_identity_allowed(settings)


def test_the_cookie_is_secure_wherever_the_dev_identity_is_refused():
    """Two gates, one list. ``Settings`` restates the development environments
    rather than importing ``app.auth.principal`` - it is a leaf, and importing
    back into it would invert the dependency. This is the test that keeps the
    restatement honest."""
    from typing import get_args

    from app.core.config import Environment

    for environment in get_args(Environment):
        settings = settings_for(environment)
        insecure_cookie = not settings.session_cookie_is_secure
        development = environment in DEVELOPMENT_ENVIRONMENTS
        assert insecure_cookie == development, environment


# --- CORS ------------------------------------------------------------------


@pytest.mark.parametrize("raw", ["*", "http://localhost:3000,*", '["*"]'])
def test_a_wildcard_cors_origin_is_refused_at_startup(raw: str, monkeypatch):
    """Starlette echoes the request Origin when `allow_origins=["*"]` is paired
    with `allow_credentials=True`, so every site a user visits could make
    authenticated requests. Browsers do not stop it - the response looks like a
    specific-origin grant.

    `CORS_ALLOW_ORIGINS=*` is what someone types when CORS is "not working", so
    it must fail loudly rather than quietly succeed.
    """
    monkeypatch.setenv("CORS_ALLOW_ORIGINS", raw)

    with pytest.raises(ValidationError, match="cannot be"):
        settings_for("local")


def test_specific_origins_are_still_accepted(monkeypatch):
    monkeypatch.setenv("CORS_ALLOW_ORIGINS", "https://app.example.com,https://admin.example.com")

    settings = settings_for("local")

    assert settings.cors_allow_origins == [
        "https://app.example.com",
        "https://admin.example.com",
    ]


# --- no unguarded escape hatches ------------------------------------------

#: The one module allowed to start a process (ADR 0012). It runs this
#: repository's own parser module with a fixed argv, no shell and a scrubbed
#: environment - to *contain* hostile documents, the opposite of an escape
#: hatch. test_the_one_process_launch_is_fixed_and_isolated holds it to that.
SANDBOXED_PROCESS_MODULES = frozenset({"retrieval/isolation.py"})


def test_production_code_contains_no_asserts():
    """`python -O` strips `assert`, so an assert is not a check - it is a check
    that disappears exactly where uptime matters most."""
    import pathlib
    import re

    app_root = pathlib.Path(__file__).resolve().parents[1] / "app"
    offenders = [
        f"{path.relative_to(app_root)}:{number}"
        for path in app_root.rglob("*.py")
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if re.match(r"^\s*assert\s", line)
    ]

    assert offenders == []


def test_no_dynamic_execution_primitives_in_production_code():
    """The build plan is explicit: no arbitrary shell commands. This also
    covers the neighbours - `eval`, `exec`, `pickle` on untrusted input - that
    turn a parsing bug into remote code execution."""
    import pathlib
    import re

    app_root = pathlib.Path(__file__).resolve().parents[1] / "app"
    forbidden = re.compile(r"\b(eval|exec)\s*\(|\bpickle\.|subprocess\.|os\.system\(|shell=True")
    # The sandboxed parser may start a process; nothing else may. Exempted by
    # file rather than by pattern, so a second use anywhere still fails - and
    # eval, exec, pickle and shell=True stay forbidden in that file too.
    without_subprocess = re.compile(r"\b(eval|exec)\s*\(|\bpickle\.|os\.system\(|shell=True")
    offenders = [
        f"{path.relative_to(app_root)}:{number}"
        for path in app_root.rglob("*.py")
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if (
            without_subprocess
            if path.relative_to(app_root).as_posix() in SANDBOXED_PROCESS_MODULES
            else forbidden
        ).search(line)
    ]

    assert offenders == []


def test_the_one_process_launch_is_fixed_and_isolated():
    """What the exemption above relies on, asserted rather than trusted: the
    parser runs this repository's own module, in isolated mode, with an argv
    that nothing a caller supplies can change."""
    import sys

    from app.retrieval.isolation import WORKER_MODULE, IsolatedParser
    from app.retrieval.parsers import ParseLimits

    parser = IsolatedParser(
        limits=ParseLimits(max_pdf_pages=1, max_chars=1),
        timeout_seconds=1.0,
        max_memory_bytes=1,
        max_concurrent=1,
    )
    assert parser.command == (sys.executable, "-I", "-m", WORKER_MODULE)


def test_settings_are_the_only_reader_of_the_environment():
    """The threat model's claim that secrets are read through a typed layer is
    only true if nothing reads around it."""
    import pathlib
    import re

    app_root = pathlib.Path(__file__).resolve().parents[1] / "app"
    reader = re.compile(r"os\.environ|os\.getenv")
    offenders = [
        f"{path.relative_to(app_root)}:{number}"
        for path in app_root.rglob("*.py")
        if path.name != "config.py"
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if reader.search(line) and not line.lstrip().startswith("#")
    ]

    assert offenders == []


def test_every_declared_secret_is_a_secret_str():
    """A plain `str` for a credential is one f-string away from a log line."""
    from pydantic import SecretStr

    secret_ish = ("api_key", "secret", "token", "password")
    plain: list[str] = []

    for name, field in Settings.model_fields.items():
        if not any(marker in name for marker in secret_ish):
            continue
        # A number cannot carry a credential: `chunk_size_tokens` counts
        # tokens, it is not one. Only a field that can hold text must be a
        # SecretStr.
        if field.annotation in (int, float, bool):
            continue
        annotation = str(field.annotation)
        if SecretStr.__name__ not in annotation:
            plain.append(f"{name}: {annotation}")

    assert plain == []
