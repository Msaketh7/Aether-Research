"""Load the HTTP surface with Locust (Phase 21).

    make loadtest-api                       # against a local API on :8000
    make loadtest-api USERS=50 HOST=https://staging.example.com

Locust is not in the lockfile. It is fetched into a throwaway environment by
``uv run --with locust``, the same arrangement ``make audit`` uses for
``pip-audit``: a tool that runs a handful of times per release should not be a
dependency every developer and every CI job installs.

**This is the other half of the load test, not a second version of it.**
``scripts/loadtest.py`` measures the pipeline - what the worker gets through
when a hundred research jobs are offered at once. This measures the API in
front of it: how a person's dashboard, run list, sources, evidence and report
behave while all of that is happening. They are different ceilings. The
pipeline runs out of CPU inside a worker; the surface runs out of database
connections inside a request thread, and the whole architecture (ADR 0001)
rests on the claim that the second does not happen because of the first.

**A user here does what the frontend does.** Sign in once, then loop over the
reads the product actually issues - the dashboard's stats, a page of runs,
and, for a run already in the list, its sources, evidence and report. Starting
runs is a separate, rarer task, because the frontend's ratio is roughly that:
one submission and then minutes of polling and reading.

**Writes are opt-in.** ``AETHER_LOAD_START_RUNS=1`` lets the load submit
research. Off by default so that pointing this at a deployment cannot spend
its model budget by accident - a load generator that costs money when someone
runs it with the wrong host is a trap, not a tool.
"""

from __future__ import annotations

import os
import random
import uuid
from typing import Any

from locust import HttpUser, between, events, task
from locust.env import Environment

API = "/api/v1"

#: Whether this load may start research. Off unless asked; see the module note.
START_RUNS = os.environ.get("AETHER_LOAD_START_RUNS", "").lower() in {"1", "true", "yes"}

#: Password used for the accounts this load registers. Not a secret: these
#: accounts exist for the duration of a load test against a disposable
#: deployment, and the alternative - reading a real credential out of the
#: environment of a machine running a load generator - is worse.
PASSWORD = "loadtest-Passw0rd!"  # noqa: S105

#: The domain the generated addresses use. Not `.invalid` or `.test`, which
#: would be the obvious choices: `email-validator` rejects every special-use
#: domain, so registration would fail with a 422 - and because the development
#: identity answers an unauthenticated request in `local` and `test`, the load
#: would then quietly run as one shared user against one shared rate-limit
#: bucket, measuring contention that no real deployment has. That happened on
#: the first run of this file.
DOMAIN = "loadtest.example.com"


@events.quitting.add_listener
def _fail_on_errors(environment: Environment, **_: Any) -> None:
    """Exit non-zero when the run did not meet its own bar.

    A load test whose process exits zero regardless of what it measured cannot
    be put in a pipeline. Two conditions, both about the surface rather than
    about speed: any failed request at all, and a 95th percentile past two
    seconds - which for reads that are a single indexed query is already far
    past healthy.
    """
    statistics = environment.stats
    if statistics.total.num_failures:
        environment.process_exit_code = 1
        return
    p95 = statistics.total.get_response_time_percentile(0.95)
    if p95 is not None and p95 > 2000:
        environment.process_exit_code = 1


class ResearchReader(HttpUser):
    """One signed-in person, reading their research the way the frontend does."""

    weight = 9
    wait_time = between(0.5, 2.0)

    def on_start(self) -> None:
        """Register an account and sign in, keeping the session cookie.

        A fresh account per simulated user rather than one shared login: every
        read on this surface is scoped by ``user_id``, so a thousand users
        sharing one account would load one index entry and prove nothing about
        how the scoping behaves when the table is wide.
        """
        self.email = f"load-{uuid.uuid4().hex[:12]}@{DOMAIN}"
        self.run_ids: list[str] = []
        self.signed_in = False
        # Registering signs the new account in and returns the cookie, so this
        # is one credential spend, not two. Calling /auth/login afterwards was
        # the second thing this file got wrong: it doubled the load on the
        # bucket that exists to bound brute force, and every user that lost the
        # race then read the surface unauthenticated.
        with self.client.post(
            f"{API}/auth/register",
            json={"email": self.email, "password": PASSWORD, "name": "Load test"},
            name="POST /auth/register",
            catch_response=True,
        ) as response:
            if response.status_code in (200, 201):
                self.signed_in = True
                response.success()
            elif response.status_code == 429:
                # The credential bucket, which a load generator shares across
                # all of its users because they share its address. Real, and
                # not this user's fault.
                response.success()
            else:
                response.failure(f"register returned {response.status_code}")

    @task(5)
    def dashboard(self) -> None:
        self._read(f"{API}/research/stats", "GET /research/stats")

    @task(5)
    def list_runs(self) -> None:
        response = self._read(f"{API}/research?limit=20", "GET /research")
        if response is None:
            return
        try:
            items = response.json().get("items") or []
        except ValueError:
            return
        self.run_ids = [str(item["id"]) for item in items if "id" in item]

    @task(4)
    def read_one_run(self) -> None:
        """A run's detail, and then one of the tabs the product opens on it."""
        run_id = self._a_run()
        if run_id is None:
            return
        self._read(f"{API}/research/{run_id}", "GET /research/{id}")
        page = random.choice(("sources", "evidence", "activity", "report"))  # noqa: S311
        # A run still being researched has no report yet. That 404 is the
        # product behaving correctly, and counting it as a failed request
        # would turn a load test into a test of how far the runs had got.
        self._read(f"{API}/research/{run_id}/{page}", f"GET /research/{{id}}/{page}", allow=(404,))

    @task(1)
    def settings(self) -> None:
        self._read(f"{API}/settings", "GET /settings")

    @task(1)
    def start_run(self) -> None:
        """Submit research - only when this load was explicitly allowed to."""
        if not START_RUNS or not self.signed_in:
            return
        with self.client.post(
            f"{API}/research",
            json={
                "question": (
                    "Compare the major AI inference infrastructure companies on "
                    "pricing, funding and recent announcements."
                ),
                "mode": "quick",
            },
            name="POST /research",
            catch_response=True,
        ) as response:
            if response.status_code in (200, 201, 202, 429):
                response.success()
            else:
                response.failure(f"create returned {response.status_code}")

    def _read(self, path: str, name: str, *, allow: tuple[int, ...] = ()) -> Any:
        """One GET, with throttling reported rather than counted as an error.

        A 429 is the rate limiter doing its job, and at enough simulated users
        it will happen - that is a finding, not a fault. It is recorded under
        its own name so the statistics table shows exactly which endpoint began
        shedding and at what load, instead of burying it in a failure count
        that says only that something went wrong.
        """
        if not self.signed_in:
            # This user never got a session, because the credential bucket
            # refused it. Reading anyway would fill the report with 401s that
            # say nothing about the surface under test.
            return None
        with self.client.get(path, name=name, catch_response=True) as response:
            if response.status_code == 429:
                response.success()
                self.environment.events.request.fire(
                    request_type="GET",
                    name=f"{name} [429 throttled]",
                    response_time=response.elapsed.total_seconds() * 1000,
                    response_length=len(response.content or b""),
                    exception=None,
                    context={},
                )
                return None
            if response.status_code == 200 or response.status_code in allow:
                response.success()
                return response if response.status_code == 200 else None
            response.failure(f"{name} returned {response.status_code}")
            return None

    def _a_run(self) -> str | None:
        return random.choice(self.run_ids) if self.run_ids else None  # noqa: S311


class HealthProbe(HttpUser):
    """The probes an orchestrator issues, which must answer while everything else is busy.

    A separate user class, because the question is not how fast ``/health`` is
    on an idle box. It is whether a readiness probe still answers when every
    request thread is occupied - and a probe that starts timing out under load
    is what turns a slow deployment into a restarting one. They live at the
    root rather than under ``/api/v1``, outside the rate limiter, which is the
    arrangement being tested as much as the latency is.

    One probe user for every nine readers: a real deployment has a handful of
    probes and as many clients as it has clients, and weighting them equally
    would spend half the load on the cheapest endpoint in the system.
    """

    weight = 1
    wait_time = between(1.0, 3.0)

    @task
    def health(self) -> None:
        self.client.get("/health", name="GET /health")

    @task
    def ready(self) -> None:
        self.client.get("/ready", name="GET /ready")
