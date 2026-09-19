#!/usr/bin/env python3
"""Ask a deployment whether it works.

A service whose tasks are running is not the same claim as a deployment that
works, and ECS only makes the first one. This makes the second, from outside,
over the internet, the way a browser would - which is the only vantage point
that tests the load balancer, the routing rule and the two services together.

Stdlib only, on purpose. It runs from a deploy job that has no virtualenv, and
a smoke test that needs its own dependency tree installed first is a smoke test
that can fail for reasons that have nothing to do with the deployment.

Each check states a *property of this system*, not a status code:

- the frontend renders a page that needs no session and no API;
- the API is reachable **at the same origin as the frontend**, which is what
  makes the built image's relative base URL work, the session cookie
  first-party and CORS irrelevant;
- the API is the one in this repository, identified by its error envelope and
  its own security headers rather than by "something answered";
- the versioned surface refuses an unauthenticated caller, so the deployment
  did not come up with the development identity enabled;
- the probes are *not* routed publicly, so readiness - which reports whether
  Postgres, Redis and S3 are reachable - is not readable by anyone who asks.

    python scripts/smoke.py --base-url https://aether.example.com
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urljoin

USER_AGENT = "AetherResearch-Smoke/1.0"


@dataclass(frozen=True)
class Response:
    status: int
    headers: dict[str, str]
    body: bytes

    def json(self) -> object:
        try:
            return json.loads(self.body)
        except ValueError:
            return None

    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")


def fetch(url: str, *, timeout: float) -> Response:
    """GET a URL, treating an HTTP error status as a response rather than a raise.

    ``urlopen`` raises on 4xx and 5xx, and every interesting check here is a
    4xx: an unauthenticated request that is *supposed* to be refused, and a
    path that is *supposed* not to exist.
    """
    # S310: the scheme is checked in `main` before any check runs, and a
    # deployment URL that is not http(s) is a usage error rather than a
    # failed deployment.
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})  # noqa: S310
    try:
        with urllib.request.urlopen(request, timeout=timeout) as answer:  # noqa: S310
            return Response(answer.status, dict(answer.headers), answer.read())
    except urllib.error.HTTPError as error:
        return Response(error.code, dict(error.headers), error.read())


@dataclass(frozen=True)
class Check:
    name: str
    run: Callable[[str, float], None]


def _the_frontend_renders(base: str, timeout: float) -> None:
    answer = fetch(urljoin(base, "/login"), timeout=timeout)
    if answer.status != 200:
        raise AssertionError(f"/login answered {answer.status}, not 200")
    if "<html" not in answer.text().lower():
        raise AssertionError("/login answered 200 but did not return a page")


def _the_api_is_at_the_same_origin(base: str, timeout: float) -> None:
    """The routing rule, which is the assumption the built web image is built on.

    `NEXT_PUBLIC_API_BASE_URL` is baked in as `/api/v1`, so if `/api/*` does
    not reach the API from this origin the frontend calls itself and every
    request 404s - with a bundle that cannot be reconfigured without rebuilding
    it.
    """
    answer = fetch(urljoin(base, "/api/v1/research"), timeout=timeout)
    if answer.status in (404, 405) or answer.status >= 500:
        raise AssertionError(
            f"/api/v1/research answered {answer.status}: the API is not routed at this origin"
        )


def _the_api_is_ours(base: str, timeout: float) -> None:
    """Identified by the contract it keeps, not by the fact that it answered.

    A misconfigured listener rule, a stale target group or somebody else's
    service can all return a status code. The error envelope and the request id
    header are this application's.
    """
    answer = fetch(urljoin(base, "/api/v1/research"), timeout=timeout)

    body = answer.json()
    if not isinstance(body, dict) or "error" not in body:
        raise AssertionError(f"the API's error envelope is missing: {answer.text()[:200]!r}")

    headers = {name.lower() for name in answer.headers}
    for required in ("x-request-id", "x-content-type-options"):
        if required not in headers:
            raise AssertionError(f"the response carries no {required} header")


def _unauthenticated_callers_are_refused(base: str, timeout: float) -> None:
    """The check that would catch a deployment running as a developer.

    `DEV_IDENTITY_ENABLED` and the `local`/`test` allowlist both have to be
    wrong for this to pass a caller through, and this is where that would be
    visible - as a 200 on a listing that should need a session.
    """
    for path in ("/api/v1/research", "/api/v1/settings"):
        answer = fetch(urljoin(base, path), timeout=timeout)
        if answer.status != 401:
            raise AssertionError(
                f"{path} answered {answer.status} to an unauthenticated caller, not 401"
            )
        body = answer.json()
        code = body.get("error", {}).get("code") if isinstance(body, dict) else None
        if code != "unauthenticated":
            raise AssertionError(f"{path} refused with code {code!r}, not 'unauthenticated'")


def _the_probes_are_not_public(base: str, timeout: float) -> None:
    """The probes and the metrics endpoint are not routed to the internet.

    Readiness names every hard dependency and says whether each is reachable.
    That is an answer for a load balancer inside the VPC, not for anyone who
    asks: published, it is a free map of the deployment and its current state.
    `/metrics` is worse - request rates, model spend, queue depth. The load
    balancer routes only `/api/*` to the API, so all three of these reach the
    frontend, which does not serve them.

    Each path is recognised by the *shape of its own response*, not by a
    status code and not by one shared key. `/health` answers `{"status": ...}`
    and `/ready` answers `{"ready": ..., "dependencies": [...]}` - a single
    check for "status" would pass a publicly routed `/ready`, which is the one
    that matters most.

    Readiness is checked regardless of status code, because a 503 from it
    carries the same map as a 200 and is if anything more interesting to a
    stranger.
    """
    exposed: list[str] = []

    health = fetch(urljoin(base, "/health"), timeout=timeout)
    body = health.json()
    if health.status == 200 and isinstance(body, dict) and {"status", "version"} <= set(body):
        exposed.append("/health")

    ready = fetch(urljoin(base, "/ready"), timeout=timeout)
    body = ready.json()
    if isinstance(body, dict) and {"ready", "dependencies"} <= set(body):
        exposed.append("/ready")

    # Prometheus exposition is recognisable on sight.
    metrics = fetch(urljoin(base, "/metrics"), timeout=timeout)
    if metrics.status == 200 and "# TYPE" in metrics.text():
        exposed.append("/metrics")

    # Collected rather than raised on the first one, so the failure names
    # everything that is exposed instead of the first thing checked.
    if exposed:
        raise AssertionError(f"answering publicly: {', '.join(exposed)}")


CHECKS = (
    Check("the frontend renders", _the_frontend_renders),
    Check("the API is at the same origin", _the_api_is_at_the_same_origin),
    Check("the API is this application", _the_api_is_ours),
    Check("unauthenticated callers are refused", _unauthenticated_callers_are_refused),
    Check("the probes are not public", _the_probes_are_not_public),
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="scripts/smoke.py",
        description="Check that a deployment of Aether Research works.",
    )
    parser.add_argument("--base-url", required=True, help="Origin of the deployment.")
    parser.add_argument("--timeout", type=float, default=15.0, help="Per-request timeout.")
    args = parser.parse_args(argv)

    base = args.base_url.rstrip("/") + "/"
    if not base.startswith(("http://", "https://")):
        print(f"--base-url must be an absolute URL, got {args.base_url!r}", file=sys.stderr)
        return 2

    print(f"smoke testing {base}")
    failures = 0
    for check in CHECKS:
        try:
            check.run(base, args.timeout)
        except AssertionError as failure:
            failures += 1
            print(f"  FAIL  {check.name}: {failure}")
        except OSError as failure:
            # A connection that could not be made at all. Reported the same
            # way, because from here it is the same answer: the deployment does
            # not work.
            failures += 1
            print(f"  FAIL  {check.name}: could not reach the deployment: {failure}")
        else:
            print(f"  ok    {check.name}")

    if failures:
        print(f"\n{failures} of {len(CHECKS)} checks failed")
        return 1

    print(f"\nall {len(CHECKS)} checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
