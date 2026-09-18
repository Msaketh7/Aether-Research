"""The test harness's own Postgres plumbing.

Ordinarily the suite's support code is proved by the tests that use it. Two
pieces here are not, because their failure is invisible to every other test and
shows up as a slow machine hours later: the sweep that clears up clusters a
killed run left behind, and the per-worker database that keeps parallel workers
from truncating each other's rows.

Nothing here starts a real cluster - these are the decisions around one, and the
tests that need a server have it already.
"""

from __future__ import annotations

import os
from pathlib import Path

from tests.support.postgres import (
    OWNER_FILE,
    cluster_owner,
    per_worker_url,
    process_alive,
    sweep_abandoned_clusters,
)

#: A pid that is not running. Chosen high and odd; the test asserts the fact
#: rather than trusting it, so a machine that really is using it just retries.
DEAD_PID = 999_999


def abandoned_cluster(root: Path, name: str, *, owner: int | None) -> Path:
    """A directory shaped like a throwaway cluster, without a server in it."""
    workdir = root / name
    (workdir / "data").mkdir(parents=True)
    if owner is not None:
        (workdir / OWNER_FILE).write_text(str(owner), encoding="utf-8")
    return workdir


# --- who owns a cluster ---------------------------------------------------------


def test_this_process_reads_as_alive_and_a_spent_pid_does_not():
    """The probe must never signal what it is asking about: on Windows
    `os.kill(pid, 0)` is implemented as TerminateProcess, so the obvious
    version of this check would kill the server it was inspecting."""
    assert process_alive(os.getpid()) is True
    assert process_alive(DEAD_PID) is False


def test_a_cluster_without_an_owner_file_reports_none(tmp_path):
    """Clusters created before owner tracking existed, which the sweep falls
    back to judging by age."""
    workdir = abandoned_cluster(tmp_path, "aether-pg-old", owner=None)

    assert cluster_owner(workdir) is None


def test_a_cluster_records_the_process_that_made_it(tmp_path):
    workdir = abandoned_cluster(tmp_path, "aether-pg-mine", owner=4242)

    assert cluster_owner(workdir) == 4242


# --- the sweep ------------------------------------------------------------------


def test_the_sweep_removes_a_cluster_whose_test_process_is_gone(tmp_path, monkeypatch):
    """The case that actually happens: a run killed on a timeout. `pg_ctl`
    starts a detached postmaster, so the server outlives its killer and the
    cluster is still listening - which is why the test is whether the *owner*
    is alive, not whether the cluster is."""
    monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_path))
    dead = abandoned_cluster(tmp_path, "aether-pg-dead", owner=DEAD_PID)
    mine = abandoned_cluster(tmp_path, "aether-pg-live", owner=os.getpid())

    sweep_abandoned_clusters()

    assert not dead.exists(), "a cluster whose run is over is cleared up"
    assert mine.exists(), "a cluster whose run is still going is left alone"


def test_the_sweep_leaves_a_recent_cluster_that_records_no_owner(tmp_path, monkeypatch):
    """Age is the fallback, and it is deliberately long: a suite running in
    another terminal must never be swept out from under itself."""
    monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_path))
    recent = abandoned_cluster(tmp_path, "aether-pg-recent", owner=None)

    sweep_abandoned_clusters()

    assert recent.exists()


def test_the_sweep_ignores_directories_that_are_not_ours(tmp_path, monkeypatch):
    """It runs against the shared temp directory, so the glob is the only thing
    standing between this and somebody else's data."""
    monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_path))
    unrelated = tmp_path / "important-work"
    unrelated.mkdir()

    sweep_abandoned_clusters()

    assert unrelated.exists()


# --- parallel workers -----------------------------------------------------------


def test_each_worker_gets_a_database_of_its_own():
    """Sharing one would not be slow, it would be wrong: the suite truncates
    every table between tests, so two workers would delete each other's rows."""
    url = "postgresql+asyncpg://postgres@127.0.0.1:5432/aether_test"

    assert per_worker_url(url, "gw0").endswith("/aether_test_gw0")
    assert per_worker_url(url, "gw3").endswith("/aether_test_gw3")
    # Everything before the database name is untouched, so the workers share a
    # server and differ only in where they write.
    assert per_worker_url(url, "gw0").startswith("postgresql+asyncpg://postgres@127.0.0.1:5432/")
