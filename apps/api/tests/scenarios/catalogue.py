"""The fifteen scenarios Phase 19 names, and the decorator that claims one.

The phase brief lists the situations this system must be shown to survive. A
list in a document is a list in a document: it goes stale the first time a test
is renamed, deleted or quietly narrowed, and nobody notices because the suite
stays green. So the list lives here as code, every scenario test claims its
entry by name, and ``test_catalogue.py`` fails the build when the two disagree.

Claiming is by decorator and happens at **import** time, not at collection
time. That matters: a registry filled by a pytest hook would be complete only
when the whole package is collected, so running one test with ``-k`` - which is
what anyone debugging does on this machine - would report fourteen missing
scenarios. Importing the modules is what makes the check independent of which
tests were selected.

A scenario may be claimed by more than one test, and a test may claim more than
one scenario: a run that exhausts its budget also reaches a limit, and saying so
twice is cheaper than pretending the two are unrelated.
"""

from __future__ import annotations

import importlib
import pkgutil
from collections.abc import Callable
from enum import StrEnum
from typing import TypeVar

import pytest


class Scenario(StrEnum):
    """What the brief requires be covered, in the order it lists them."""

    SUCCESSFUL_DEEP_RESEARCH = "successful deep research"
    SEARCH_PROVIDER_TIMEOUT = "search provider timeout"
    LLM_TIMEOUT = "LLM timeout"
    LLM_RATE_LIMIT = "LLM rate limit"
    WORKER_RESTART = "worker restart"
    DUPLICATE_URL = "duplicate URL"
    CONTRADICTORY_SOURCES = "contradictory sources"
    NO_USEFUL_SOURCES = "no useful sources"
    EMPTY_SEARCH_RESULTS = "empty search results"
    BUDGET_EXCEEDED = "budget exceeded"
    MAX_ITERATIONS_REACHED = "maximum iterations reached"
    PROMPT_INJECTION = "malicious prompt injection in source content"
    SSRF_ATTEMPT = "SSRF attempt"
    USER_CANCELLATION = "user cancellation"
    RESUME_AFTER_INTERRUPTION = "research resume after interruption"


#: Scenario -> the tests that claim it, as ``module::name``. Filled on import.
CLAIMED: dict[Scenario, list[str]] = {}

F = TypeVar("F", bound=Callable[..., object])


def covers(*scenarios: Scenario) -> Callable[[F], F]:
    """Mark a test as covering one or more scenarios, and record that it does.

    The marker is what ``-m scenario`` selects, so the fifteen can be run
    without the rest of the suite. Its arguments are the scenario names, which
    is how a test says which of them it answers for when its own name does not
    make that obvious. The registration is the other half, and the one that
    matters: it is what makes the *absence* of a test a failure rather than a
    silence.
    """

    def decorate(test: F) -> F:
        for scenario in scenarios:
            CLAIMED.setdefault(scenario, []).append(f"{test.__module__}::{test.__name__}")
        marked = pytest.mark.scenario(*(scenario.name.lower() for scenario in scenarios))(test)
        return marked  # type: ignore[return-value]

    return decorate


def import_scenario_modules() -> list[str]:
    """Import every ``test_*`` module in this package, returning their names.

    The coverage check needs every claim to have been made before it counts
    them, and only importing the modules guarantees that.
    """
    package = importlib.import_module(__package__ or "tests.scenarios")
    names = [
        info.name
        for info in pkgutil.iter_modules(package.__path__)
        if info.name.startswith("test_") and info.name != "test_catalogue"
    ]
    for name in names:
        importlib.import_module(f"{package.__name__}.{name}")
    return names
