"""The pipelines hold to the rules they were written under.

Phase 24's five workflows are configuration that only fails when it runs, and
two of them - `deploy.yml` and `eval.yml` - are configuration nobody wants to
find out about by running. `actionlint` and `shellcheck` cover the static half
in CI: an expression that cannot evaluate, a `needs` on a job that is not
there, a shell script that will not parse. What they cannot check is the half
that is a *decision*, and that is what is here.

Each of these has a specific bad afternoon behind it. A workflow with no
`permissions` block gets whatever the repository default is, which for an older
repository is write access to everything, handed to every third-party action in
it. A deploy workflow reachable from `pull_request` is a deploy any fork can
trigger. An action pinned to `@main` is code somebody else can change between
one run and the next. And a job added to `ci.yml` without being added to the
branch protection rule is a check that is optional and looks required.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[3]
WORKFLOWS = REPO / ".github" / "workflows"

EXPECTED = {"ci", "test", "build", "eval", "deploy"}


def _workflows() -> dict[str, dict]:
    loaded = {}
    for path in sorted(WORKFLOWS.glob("*.yml")):
        loaded[path.stem] = yaml.safe_load(path.read_text(encoding="utf-8"))
    return loaded


def _triggers(workflow: dict) -> dict:
    """The `on:` block.

    YAML 1.1 reads a bare `on` as the boolean True, which PyYAML implements and
    GitHub does not - so the key in a parsed workflow is `True`, not `"on"`.
    Reading `workflow["on"]` returns nothing and every trigger assertion below
    would pass against a workflow with no triggers at all.
    """
    return workflow.get(True) or workflow.get("on") or {}


def _jobs(workflow: dict) -> dict[str, dict]:
    return workflow.get("jobs", {})


def _steps(workflow: dict) -> list[tuple[str, dict]]:
    return [(name, step) for name, job in _jobs(workflow).items() for step in job.get("steps", [])]


def test_the_five_workflows_the_build_plan_names_exist():
    """Phase 24 names ci, test, eval, build and deploy. This is that list, as code."""
    assert set(_workflows()) >= EXPECTED


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_every_workflow_declares_its_permissions(name: str):
    """No block means the repository default, which may be write-all.

    A job with write-all hands that token to every action it runs. Declaring
    `contents: read` at the top and widening it per job is the difference
    between a compromised action reading this repository and rewriting it.
    """
    workflow = _workflows()[name]
    permissions = workflow.get("permissions")

    assert permissions is not None, f"{name}.yml declares no top-level permissions"
    assert permissions == "read-all" or permissions.get("contents") == "read", (
        f"{name}.yml's default permissions are wider than read"
    )


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_every_action_is_pinned(name: str):
    """`@main` is code that can change between one run and the next.

    A local `./.github/workflows/...` reference is exempt: it is this
    repository at this commit.
    """
    for job, step in _steps(_workflows()[name]):
        uses = step.get("uses")
        if not uses or uses.startswith("./"):
            continue
        assert "@" in uses, f"{name}.yml/{job} uses {uses} with no version"
        reference = uses.rsplit("@", 1)[1]
        assert reference not in {"main", "master", "latest", "HEAD"}, (
            f"{name}.yml/{job} pins {uses} to a moving reference"
        )


def test_the_deploy_workflow_cannot_be_triggered_by_a_pull_request():
    """A deploy reachable from `pull_request` is a deploy any fork can start.

    `workflow_dispatch` needs write access to the repository and `release`
    needs a published release; neither is something an outside contributor can
    cause by opening a pull request.
    """
    triggers = _triggers(_workflows()["deploy"])

    # Not vacuous: a parse that found no triggers at all would satisfy every
    # assertion below, and that is exactly what reading `workflow["on"]`
    # returns - see `_triggers`.
    assert triggers, "deploy.yml parsed with no triggers"
    assert "pull_request" not in triggers
    assert "pull_request_target" not in triggers
    assert set(triggers) <= {"workflow_dispatch", "release"}, (
        f"deploy.yml can be triggered by {sorted(triggers)}"
    )


def test_the_deploy_job_runs_inside_a_github_environment():
    """Approval, the deployment history and the environment-scoped variables.

    Without `environment:` a production deploy needs nobody, records nothing,
    and reads its configuration from repository-wide variables that staging
    shares.
    """
    deploy = _jobs(_workflows()["deploy"])["deploy"]

    assert deploy.get("environment"), "the deploy job declares no environment"


def test_the_deploy_workflow_captures_what_it_would_roll_back_to_before_it_changes_anything():
    """A rollback target recorded after the change is not a rollback target.

    The step that reads the running task definitions must come before the step
    that registers new ones, or after a failure the workflow would "roll back"
    to the revision it just deployed.
    """
    steps = [name for name, step in [(s.get("name", ""), s) for s in _deploy_steps()]]

    assert "Record the running revisions" in steps
    assert steps.index("Record the running revisions") < steps.index("Register them")
    assert steps.index("Register them") < steps.index("Roll the services")


def _deploy_steps() -> list[dict]:
    return _jobs(_workflows()["deploy"])["deploy"]["steps"]


def test_the_migration_runs_before_the_services_roll():
    """Not on service startup: two replicas racing `alembic upgrade` deadlock.

    And not after, either - the new code is deployed expecting the new schema.
    """
    steps = [step.get("name", "") for step in _deploy_steps()]

    assert steps.index("Run the migrations") < steps.index("Roll the services")


def test_the_smoke_test_runs_after_the_services_roll():
    """It asks whether the deployment works, so it has to be the new one."""
    steps = [step.get("name", "") for step in _deploy_steps()]

    assert steps.index("Roll the services") < steps.index("Smoke test")
    assert steps.index("Smoke test") < steps.index("Put the previous revisions back")


def test_the_images_are_scanned_before_they_are_pushed():
    """A gate behind the thing it guards is not a gate.

    A vulnerable image already in a registry is one somebody can deploy,
    whatever the build said afterwards.
    """
    steps = [step.get("name", "") for step in _jobs(_workflows()["build"])["images"]["steps"]]

    last_scan = max(steps.index(name) for name in steps if name.startswith("Scan the"))
    first_push = min(steps.index(name) for name in steps if name.startswith("Push the"))

    assert last_scan < first_push


def test_the_aggregate_check_depends_on_every_other_job():
    """Otherwise a job added to ci.yml is optional and looks required.

    Branch protection names one check. `ci` is that check, and it is only
    honest while it waits for everything beside it.
    """
    jobs = _jobs(_workflows()["ci"])
    required = set(jobs) - {"ci"}

    assert set(jobs["ci"]["needs"]) == required, (
        f"the aggregate check does not wait for {sorted(required - set(jobs['ci']['needs']))}"
    )


def test_the_evaluation_is_not_an_unguarded_cost_on_every_merge():
    """Each case is a real research run against real providers, and costs money.

    The push trigger exists because the build plan asks for it, and it is
    guarded: a job that checks for credentials and for an explicit repository
    variable before anything is spent. Without the guard this is a charge per
    merge, which is the kind of thing somebody turns off at the worst moment.
    """
    workflow = _workflows()["eval"]
    guard = _jobs(workflow)["configured"]
    evaluate = _jobs(workflow)["evaluate"]

    assert "RUN_EVALUATION_ON_MAIN" in yaml.dump(guard)
    assert evaluate["needs"] == "configured" or "configured" in evaluate["needs"]
    assert "needs.configured.outputs.ready" in evaluate["if"]


def test_the_workflows_that_are_called_exist_and_are_callable():
    """A `uses: ./...` naming a workflow with no `workflow_call` trigger fails at run."""
    checked = 0
    for name, workflow in _workflows().items():
        for job, spec in _jobs(workflow).items():
            uses = spec.get("uses")
            if not uses or not uses.startswith("./"):
                continue
            # removeprefix, not lstrip: lstrip takes a character set, so
            # "./.github/..." loses the dot of ".github" too and the path
            # resolves to a directory that does not exist.
            called = REPO / uses.removeprefix("./")
            assert called.is_file(), f"{name}.yml/{job} calls {uses}, which is not there"
            assert "workflow_call" in _triggers(
                yaml.safe_load(called.read_text(encoding="utf-8"))
            ), f"{uses} is called by {name}.yml/{job} but is not callable"
            checked += 1

    # ci.yml calls test.yml and deploy.yml calls build.yml. Nothing found means
    # the parse went wrong, not that the rule holds.
    assert checked >= 2, f"only {checked} reusable workflow calls were found"
