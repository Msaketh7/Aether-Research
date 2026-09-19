"""The post-deploy smoke test, against a real server on a real socket.

``scripts/smoke.py`` is the last step of a deployment and the only thing that
asks whether one *works* rather than whether its tasks are running. It has
never been run against a deployment, because there is none - so this is what
stands in for that: the real application, served over real HTTP on a real port,
with the checks run against it exactly as the deploy job would run them.

The script is stdlib-only and speaks ``urllib``, so an ASGI transport would not
exercise it. That is the point of starting a server.

Two directions are checked, and the second matters more. A check that passes
when the deployment is healthy is worth little on its own; what makes a smoke
test useful is that it *fails* when the thing it names is wrong. So the
unauthenticated check is run against a server with the development identity
enabled - the misconfiguration it exists to catch - and asserted to fail.
"""

from __future__ import annotations

import importlib.util
import socket
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest

from app.core.config import Settings

REPO = Path(__file__).resolve().parents[3]


def _load_smoke() -> ModuleType:
    """Import `scripts/smoke.py`, which is not part of any package.

    It lives at the repository root rather than under `app/` because it
    describes a whole deployment - both services - and because it must run in a
    job that has installed nothing.
    """
    path = REPO / "scripts" / "smoke.py"
    spec = importlib.util.spec_from_file_location("aether_smoke", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    # Registered before it is executed, because `@dataclass` resolves the
    # defining class's module out of `sys.modules` while the class body runs.
    # Without this the import fails inside dataclasses with an AttributeError
    # about NoneType, a long way from the cause.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


smoke = _load_smoke()


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class _Server:
    """A uvicorn running in a thread, for the length of one test."""

    def __init__(self, app: object, port: int) -> None:
        import uvicorn

        config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
        self._server = uvicorn.Server(config)
        # Signals can only be installed on the main thread, and this is not it.
        self._server.install_signal_handlers = lambda: None  # type: ignore[method-assign]
        self._thread = threading.Thread(target=self._server.run, daemon=True)
        self.base = f"http://127.0.0.1:{port}"

    def __enter__(self) -> _Server:
        self._thread.start()
        # Two minutes, and it is a hang detector rather than a measurement.
        # Starting this server imports the whole application - about six
        # seconds of Pydantic model construction on a warm machine - and the
        # suite runs four-wide under xdist on a CPU that never turbos, so the
        # first deadline written here (30 s) failed in the full run and passed
        # every time the module was run alone. Tightening it again would turn
        # load into a red suite; the same reasoning is why
        # tests/scenarios/world.py uses 90.
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            if self._server.started:
                return self
            # A server that raised during startup leaves a dead thread and
            # would otherwise be reported as a timeout two minutes later, with
            # the actual exception nowhere in the failure.
            if not self._thread.is_alive():
                raise RuntimeError("the test server stopped while starting; see the captured log")
            time.sleep(0.05)
        raise RuntimeError("the test server did not start within 120s")

    def __exit__(self, *exc: object) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=30)


def _settings(**overrides: object) -> Settings:
    """Test settings, but with the deployment's authentication posture.

    `APP_ENV=test` keeps the in-memory queue, cache and storage backends, so
    this needs no Redis and no S3. `dev_identity_enabled=False` is what a real
    deployment sets, and it is the setting the interesting check is about.
    """
    return Settings(
        app_env="test",
        dev_identity_enabled=False,
        metrics_enabled=True,
        **overrides,  # type: ignore[arg-type]
    )


# Module-scoped: starting one of these imports the application and costs
# several seconds on this machine, and none of the tests below change the
# server's state - they only read from it.
@pytest.fixture(scope="module")
def served() -> Iterator[_Server]:
    from app.main import create_app

    with _Server(create_app(_settings()), _free_port()) as server:
        yield server


def test_the_api_checks_pass_against_the_real_application(served: _Server):
    """Everything the script can assert about an API-only origin.

    Not the frontend check: nothing is serving the frontend here, and the
    routing rule that puts both behind one origin is a load balancer's, not the
    application's.
    """
    for check in smoke.CHECKS:
        if check.name == "the frontend renders":
            continue
        if check.name == "the probes are not public":
            # True of a deployment and false here on purpose: the load balancer
            # is what withholds /health and /ready from the internet, and there
            # is no load balancer in this test. See the test below.
            continue
        check.run(served.base + "/", 10.0)


def test_the_unauthenticated_check_fails_when_the_development_identity_is_on():
    """The misconfiguration this check exists for, reproduced.

    A deployment that came up with `APP_ENV` unset would serve every request as
    a developer. This asserts the check notices - without it the smoke test
    would be four assertions that pass either way.
    """
    from app.main import create_app

    app = create_app(Settings(app_env="local", dev_identity_enabled=True))

    with (
        _Server(app, _free_port()) as server,
        pytest.raises(AssertionError, match="unauthenticated caller"),
    ):
        smoke._unauthenticated_callers_are_refused(server.base + "/", 10.0)


def test_the_probe_check_notices_every_publicly_routed_probe(served: _Server):
    """The other inversion: here the probes *are* public, and it names all of them.

    In a deployment the load balancer routes only `/api/*` to the API, so none
    of these reach it. Served directly, all three do.

    Each path has to be asserted, not just the first one. The check originally
    looked for a `"status"` key on both probes - which `/health` has and
    `/ready` does not, because readiness answers `{"ready", "dependencies"}` -
    so the `/ready` branch was dead and a deployment that published the state
    of every dependency would have passed. It was only visible because
    `/health` failed first and hid it.
    """
    with pytest.raises(AssertionError) as failure:
        smoke._the_probes_are_not_public(served.base + "/", 10.0)

    for path in ("/health", "/ready", "/metrics"):
        assert path in str(failure.value), f"{path} was not reported as exposed"


def test_the_frontend_check_fails_when_nothing_serves_the_frontend(served: _Server):
    with pytest.raises(AssertionError, match="/login"):
        smoke._the_frontend_renders(served.base + "/", 10.0)


def test_an_unreachable_deployment_is_a_failure_and_not_a_crash():
    """`main` reports; it does not raise. A deploy job reads the exit code."""
    port = _free_port()  # nothing is listening on it

    assert smoke.main(["--base-url", f"http://127.0.0.1:{port}", "--timeout", "2"]) == 1


def test_a_base_url_without_a_scheme_is_refused():
    """Reported as a usage error (2), not as a failed deployment (1).

    `urljoin` on a bare hostname produces a relative path, which `urlopen`
    rejects with a ValueError that says nothing useful - and the operator would
    read a failing smoke test as a failing deployment.
    """
    assert smoke.main(["--base-url", "aether.example.com"]) == 2
