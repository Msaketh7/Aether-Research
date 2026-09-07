"""``search_github`` - repository and code search on GitHub.

Useful for the technology questions the product is aimed at: who is actually
building on a library, how active a project is, whether a company's SDK is
maintained. Signals like stars and last-push date are weak evidence individually
and decent corroboration in aggregate.

Two notes:

* **The token is optional but strongly advisable.** Unauthenticated GitHub
  search allows about 10 requests per minute, which a single research run will
  exhaust. With ``GITHUB_TOKEN`` it is 30. The tool works either way and says
  which mode it is in, so a run that starts failing on rate limits has an
  obvious cause.
* **Code search requires authentication entirely.** Rather than let that surface
  as a confusing 422 mid-run, it is checked up front and refused with the
  setting named.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urlencode

from pydantic import Field

from app.core.enums import ToolName
from app.core.logging import get_logger
from app.sources.base import ToolInput
from app.sources.errors import UpstreamRejected
from app.sources.http import SafeHttpClient
from app.sources.sanitize import sanitize_text

logger = get_logger(__name__)

TOOL_NAME = ToolName.GITHUB_API

API_ROOT = "https://api.github.com"
#: Pinned. GitHub's API is versioned by header and an unpinned client changes
#: behaviour under you when they ship a new default.
API_VERSION = "2022-11-28"


class SearchGithubInput(ToolInput):
    query: str = Field(min_length=2, max_length=250)
    kind: Literal["repositories", "code"] = "repositories"
    max_results: int = Field(default=10, ge=1, le=50)
    sort: Literal["stars", "forks", "updated", "best-match"] = "best-match"
    language: str | None = Field(default=None, max_length=40)


@dataclass(frozen=True, slots=True)
class GithubRepository:
    full_name: str
    url: str
    description: str
    stars: int
    forks: int
    language: str | None
    #: ISO timestamps. ``None`` means GitHub did not report it, not "never".
    pushed_at: str | None
    updated_at: str | None
    is_archived: bool
    license: str | None
    topics: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class GithubCodeHit:
    repository: str
    path: str
    url: str


@dataclass(frozen=True, slots=True)
class SearchGithubOutput:
    query: str
    kind: str
    repositories: tuple[GithubRepository, ...] = ()
    code_hits: tuple[GithubCodeHit, ...] = ()
    total_available: int | None = None
    authenticated: bool = False


async def search_github(
    request: SearchGithubInput,
    *,
    client: SafeHttpClient,
    token: str | None,
) -> SearchGithubOutput:
    """Search GitHub for repositories or code."""
    if request.kind == "code" and not token:
        raise UpstreamRejected(
            "GitHub code search requires authentication. Set GITHUB_TOKEN.",
            context={"kind": request.kind},
        )

    query = request.query
    if request.language:
        query = f"{query} language:{request.language}"

    params: list[tuple[str, str]] = [("q", query), ("per_page", str(request.max_results))]
    if request.sort != "best-match":
        params.append(("sort", request.sort))

    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": API_VERSION,
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    body = await client.get_json(
        f"{API_ROOT}/search/{request.kind}?{urlencode(params)}", headers=headers
    )
    if not isinstance(body, Mapping):
        raise UpstreamRejected("GitHub returned an unexpected response shape.")

    total = body.get("total_count")
    items = _items(body.get("items"))

    if request.kind == "repositories":
        return SearchGithubOutput(
            query=request.query,
            kind=request.kind,
            repositories=tuple(_repository(item) for item in items),
            total_available=int(total) if isinstance(total, int) else None,
            authenticated=token is not None,
        )

    return SearchGithubOutput(
        query=request.query,
        kind=request.kind,
        code_hits=tuple(_code_hit(item) for item in items),
        total_available=int(total) if isinstance(total, int) else None,
        authenticated=token is not None,
    )


def _repository(item: Mapping[str, Any]) -> GithubRepository:
    licence = item.get("license")
    topics = item.get("topics")

    return GithubRepository(
        full_name=str(item.get("full_name", "")),
        url=str(item.get("html_url", "")),
        # A repository description is user-authored text from the open internet.
        description=sanitize_text(str(item.get("description") or ""))[:1000],
        stars=int(item.get("stargazers_count") or 0),
        forks=int(item.get("forks_count") or 0),
        language=_maybe_str(item.get("language")),
        pushed_at=_maybe_str(item.get("pushed_at")),
        updated_at=_maybe_str(item.get("updated_at")),
        is_archived=bool(item.get("archived", False)),
        license=(_maybe_str(licence.get("spdx_id")) if isinstance(licence, Mapping) else None),
        topics=tuple(str(topic) for topic in topics) if isinstance(topics, list) else (),
    )


def _code_hit(item: Mapping[str, Any]) -> GithubCodeHit:
    repository = item.get("repository")
    return GithubCodeHit(
        repository=str(repository.get("full_name", "")) if isinstance(repository, Mapping) else "",
        path=str(item.get("path", "")),
        url=str(item.get("html_url", "")),
    )


def _items(value: object) -> list[Mapping[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def _maybe_str(value: object) -> str | None:
    return str(value) if isinstance(value, str) and value else None


def summarize(output: SearchGithubOutput) -> Mapping[str, object]:
    return {
        "kind": output.kind,
        "results": len(output.repositories) + len(output.code_hits),
        "total_available": output.total_available,
        "authenticated": output.authenticated,
    }
