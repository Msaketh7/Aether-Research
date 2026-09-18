"""The suite's own guard: every scenario the brief names has a test.

A list of required scenarios that lives only in a document goes stale the first
time one of its tests is renamed away, deleted, or narrowed until it no longer
covers what it claims - and nobody notices, because the suite stays green. So
the list is code, each test claims its entry, and this fails the build when the
two disagree in either direction.

Both directions matter. A scenario with no test is a requirement nobody is
checking; a claim on a scenario that is not in the brief is a test asserting
something the phase never asked for, which is how a suite drifts into measuring
its own conveniences.
"""

from __future__ import annotations

import pytest

from tests.scenarios.catalogue import CLAIMED, Scenario, import_scenario_modules

#: Marked like the scenarios themselves, so `make test-scenarios` runs the guard
#: with the thing it guards. A coverage check that only ran under the full suite
#: would be absent from exactly the command someone uses to ask "are the fifteen
#: still covered".
pytestmark = pytest.mark.scenario


@pytest.fixture(scope="module", autouse=True)
def claims() -> None:
    """Import every scenario module, so every claim has been made.

    At import time rather than at collection: a registry filled by a pytest hook
    would be complete only when the whole package is collected, so running one
    test with ``-k`` would report fourteen missing scenarios.
    """
    import_scenario_modules()


def test_every_named_scenario_has_at_least_one_test():
    missing = [scenario.value for scenario in Scenario if scenario not in CLAIMED]
    assert not missing, (
        "Phase 19 requires these scenarios to be covered and nothing claims them: "
        + "; ".join(missing)
    )


def test_nothing_claims_a_scenario_the_brief_does_not_name():
    assert set(CLAIMED) <= set(Scenario)


def test_the_catalogue_is_the_fifteen_the_brief_lists():
    """A count, deliberately. Adding a sixteenth is a change to the phase's
    scope and should be a decision, not a side effect of writing a test."""
    assert len(Scenario) == 15


def test_each_claim_names_a_test_that_exists():
    """A stale entry - a test renamed, its registration left behind - would
    otherwise report a scenario as covered by nothing."""
    import importlib

    for scenario, tests in CLAIMED.items():
        for qualified in tests:
            module_name, _, test_name = qualified.partition("::")
            module = importlib.import_module(module_name)
            assert hasattr(module, test_name), f"{scenario.value} claims a missing {qualified}"
