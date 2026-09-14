"""Run and graph ceilings refuse values that could not bound anything.

A ceiling of zero is not "unlimited": it is a run that stops before it starts.
Each refused value is a deployment that would start, accept work, and fail on
all of it - so it is refused at startup instead.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.agents.schemas import MAX_PLANNED_SUBTASKS
from app.core.config import Settings
from app.core.enums import ResearchMode
from app.research.schemas import RunLimits


@pytest.mark.parametrize(
    "name",
    [
        "max_research_iterations",
        "max_sources",
        "max_search_queries",
        "max_runtime_seconds",
        "max_subtasks_per_iteration",
        "graph_max_concurrency",
        "checkpoint_pool_size",
    ],
)
def test_a_ceiling_of_zero_is_refused(name):
    with pytest.raises(ValidationError, match=name.upper()):
        Settings(app_env="test", **{name: 0})


def test_the_cost_ceiling_must_be_positive():
    with pytest.raises(ValidationError, match="MAX_ESTIMATED_COST_USD"):
        Settings(app_env="test", max_estimated_cost_usd=0)


def test_the_node_timeout_must_be_positive():
    with pytest.raises(ValidationError, match="GRAPH_NODE_TIMEOUT_SECONDS"):
        Settings(app_env="test", graph_node_timeout_seconds=0)


def test_a_round_cannot_dispatch_more_subtasks_than_a_plan_may_propose():
    """Settings restates the plan ceiling rather than importing it; this is the
    assertion that keeps the two in step."""
    assert (
        Settings(
            app_env="test", max_subtasks_per_iteration=MAX_PLANNED_SUBTASKS
        ).max_subtasks_per_iteration
        == MAX_PLANNED_SUBTASKS
    )
    with pytest.raises(ValidationError, match="MAX_SUBTASKS_PER_ITERATION"):
        Settings(app_env="test", max_subtasks_per_iteration=MAX_PLANNED_SUBTASKS + 1)


def test_every_mode_freezes_the_search_ceiling_with_the_other_limits():
    """Before Phase 9 it was the one ceiling a deployment could change under a queued run."""
    settings = Settings(app_env="test", max_search_queries=17)
    for mode in ResearchMode:
        assert RunLimits.for_mode(mode, settings).max_search_queries == 17
