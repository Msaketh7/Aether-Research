"""``search_sec`` - SEC EDGAR full-text search over company filings.

Primary sources matter more than commentary about them. A 10-K is what a company
told its regulator under penalty of perjury; an article about the 10-K is
somebody's reading of it. The credibility model treats filings as `is_primary`,
and this is the tool that finds them.

Two obligations specific to EDGAR, both non-negotiable:

* **A descriptive User-Agent with a contact address.** SEC's access terms
  require it and they enforce it - anonymous scrapers get blocked. It comes from
  ``SEC_USER_AGENT`` and the setting exists for exactly this reason.
* **Rate limiting.** SEC asks for no more than 10 requests per second. The
  executor's concurrency cap plus retry-with-backoff keeps this tool well under
  that, and the alternative is a block that affects every user of the deployment.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

from pydantic import Field, field_validator

from app.core.enums import ToolName
from app.core.logging import get_logger
from app.sources.base import ToolInput
from app.sources.errors import UpstreamRejected
from app.sources.http import SafeHttpClient
from app.sources.sanitize import sanitize_text

logger = get_logger(__name__)

TOOL_NAME = ToolName.SEC_API

#: EDGAR's full-text search, the JSON API behind the search UI on sec.gov.
FTS_URL = "https://efts.sec.gov/LATEST/search-index"

#: Form types worth searching by default. An unbounded form filter tends to
#: drown a query in 8-K noise.
COMMON_FORMS = ("10-K", "10-Q", "8-K", "20-F", "40-F", "S-1", "DEF 14A")


class SearchSecInput(ToolInput):
    query: str = Field(min_length=2, max_length=400)
    #: Form types to restrict to, e.g. ("10-K", "10-Q").
    forms: tuple[str, ...] = ()
    #: ISO dates. Bounded because an unbounded EDGAR query is very slow.
    date_from: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    date_to: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    max_results: int = Field(default=10, ge=1, le=50)

    @field_validator("forms")
    @classmethod
    def _known_forms(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """Reject a form type EDGAR will not recognise.

        A typo otherwise returns zero results, which reads as "this company has
        never filed a 10-K" rather than "you asked for a 10K".
        """
        for form in value:
            if not form or len(form) > 20:
                raise ValueError(f"{form!r} is not a valid form type")
        return value


@dataclass(frozen=True, slots=True)
class SecFiling:
    """One filing. A primary source by definition."""

    accession_number: str
    company_name: str
    cik: str
    form_type: str
    filed_at: str | None
    #: The filing's landing page on sec.gov, which is what a citation points at.
    url: str
    snippet: str


@dataclass(frozen=True, slots=True)
class SearchSecOutput:
    query: str
    filings: tuple[SecFiling, ...]
    total_available: int | None


async def search_sec(
    request: SearchSecInput,
    *,
    client: SafeHttpClient,
    user_agent: str,
) -> SearchSecOutput:
    """Search EDGAR full text for filings matching a query."""
    params: list[tuple[str, str]] = [
        ("q", request.query),
        ("from", "0"),
        ("size", str(request.max_results)),
    ]
    if request.forms:
        params.append(("forms", ",".join(request.forms)))
    if request.date_from:
        params.append(("dateRange", "custom"))
        params.append(("startdt", request.date_from))
    if request.date_to:
        params.append(("enddt", request.date_to))

    body = await client.get_json(
        f"{FTS_URL}?{urlencode(params)}",
        headers={"User-Agent": user_agent, "Accept": "application/json"},
    )
    if not isinstance(body, Mapping):
        raise UpstreamRejected("EDGAR returned an unexpected response shape.")

    hits = body.get("hits")
    inner = hits.get("hits") if isinstance(hits, Mapping) else None
    total = _total(hits)

    return SearchSecOutput(
        query=request.query,
        filings=tuple(_filing(hit) for hit in _items(inner)),
        total_available=total,
    )


def _filing(hit: Mapping[str, Any]) -> SecFiling:
    source = hit.get("_source")
    fields: Mapping[str, Any] = source if isinstance(source, Mapping) else {}

    accession = str(hit.get("_id", "")).split(":", 1)[0]
    cik = _first(fields.get("ciks")) or ""
    company = _first(fields.get("display_names")) or ""

    return SecFiling(
        accession_number=accession,
        company_name=sanitize_text(str(company))[:300],
        cik=str(cik),
        form_type=str(fields.get("root_form") or fields.get("file_type") or ""),
        filed_at=_maybe_str(fields.get("file_date")),
        url=_filing_url(cik, accession),
        snippet=sanitize_text(str(fields.get("file_description") or ""))[:500],
    )


def _filing_url(cik: str, accession: str) -> str:
    """Build the filing's index page URL.

    Constructed rather than taken from the response: a URL that comes back from
    an API is an input, and building it from the two identifiers means a
    manipulated response cannot point a citation at somebody else's domain.
    """
    if not cik or not accession:
        return "https://www.sec.gov/edgar/search/"
    plain = accession.replace("-", "")
    numeric_cik = cik.lstrip("0") or "0"
    return f"https://www.sec.gov/Archives/edgar/data/{numeric_cik}/{plain}/{accession}-index.htm"


def _items(value: object) -> list[Mapping[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def _first(value: object) -> str | None:
    if isinstance(value, list) and value:
        return str(value[0])
    if isinstance(value, str):
        return value
    return None


def _maybe_str(value: object) -> str | None:
    return str(value) if isinstance(value, str) and value else None


def _total(hits: object) -> int | None:
    """``None`` when EDGAR does not report a total - not zero."""
    if not isinstance(hits, Mapping):
        return None
    total = hits.get("total")
    if isinstance(total, Mapping):
        value = total.get("value")
        return int(value) if isinstance(value, int) else None
    return int(total) if isinstance(total, int) else None


def summarize(output: SearchSecOutput) -> Mapping[str, object]:
    return {
        "results": len(output.filings),
        "total_available": output.total_available,
        "forms": sorted({filing.form_type for filing in output.filings if filing.form_type}),
    }


def default_forms() -> Sequence[str]:
    return COMMON_FORMS
