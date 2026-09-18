"""A whole system, with a scripted web and a scripted model, and nothing else faked.

Every other suite in this repository tests one layer against doubles for the
layers below it. This one assembles the vertical slice a deployment actually
runs - queue, worker, lease, graph, all nine agents, the toolbelt with its SSRF
guard and retry policy, ingestion, retrieval, the evidence projection, report
assembly, the event stream, the call ledger and real Postgres - and replaces
exactly two things:

* **the model**, by a provider that answers from a script. Behind the *real*
  gateway, so routing, failover, retries and pricing all run, and a run's cost
  is still the sum of what the registry says its calls cost.
* **the socket**, by an ``httpx2`` mock transport. Behind the *real* guarded
  client, so the SSRF guard, the redirect policy, the size ceiling, robots and
  the search provider's own request shaping and response parsing all run.

Those two are where money and the open internet are, and they are the only two
things a test may not have. Everything between them is the system.

**Answers are chosen by schema, not by position.** The graph runs researchers in
parallel, so a script indexed by call number would answer whichever subtask won
the race - green on one machine and red on another. A rule keyed by the schema
the call asked for, optionally narrowed by what the prompt contains, is
deterministic no matter what order the calls arrive in.

Two environment facts shape what is assembled here, both recorded in the repo's
working notes: there is no pgvector locally, so ingestion stores chunks with
their vectors pending and retrieval runs its lexical arm only - which is exactly
the ``gateway=None`` deployment both factories already support; and parsing runs
in process rather than in a child, because a scenario that spawns one process
per page would spend its whole budget on interpreter startup.
"""

from __future__ import annotations

import asyncio
import dataclasses
import datetime as dt
import inspect
import uuid
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import patch

import httpx2 as httpx
from pydantic import BaseModel

from app.agents.factory import ResearchDependencies, build_research_nodes
from app.agents.nodes import ResearchNodes
from app.core.config import Settings
from app.core.enums import ResearchMode
from app.db.session import Database
from app.models.base import (
    Completion,
    CompletionRequest,
    StructuredCompletion,
    StructuredT,
    TokenUsage,
)
from app.models.budget import RunBudgetGuard
from app.models.errors import CapabilityNotSupported, StructuredOutputInvalid
from app.models.gateway import LLMGateway
from app.models.recording import CollectingCallRecorder
from app.models.routing import ModelRouter
from app.retrieval.chunking import Chunker
from app.retrieval.ingestion import DocumentIngestor
from app.retrieval.isolation import InProcessParser
from app.retrieval.parsers import ParseLimits
from app.sources.base import CallRecorder as ToolCallRecorder
from app.sources.base import CollectingToolRecorder
from app.sources.toolbelt import build_toolbelt
from app.sources.urls import IpAddress
from app.storage import ObjectStorage
from tests.support import agents as fake
from tests.support import worker as harness
from tests.support.postgres import ProvisionedDatabase

#: What every scripted hostname resolves to: a routable public address that no
#: packet is ever sent to, since the transport below is a mock. Resolution is
#: stubbed rather than allowed out to a real resolver because a suite whose
#: verdict depends on DNS is a suite that fails on a train. Every other layer of
#: the guard still runs, and the two that refuse an SSRF attempt - the IP
#: literal check and the blocked-hostname list - run *before* this one.
PUBLIC_ADDRESS = "93.184.216.34"

#: Small enough that a fixture page of a few hundred words becomes several
#: chunks, so retrieval has something to rank.
CHUNK_TOKENS = 64
CHUNK_OVERLAP_TOKENS = 8


# --- the scripted web ---------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Page:
    """One page on the scripted internet."""

    url: str
    title: str
    body: str
    snippet: str = ""
    content_type: str = "text/html; charset=utf-8"
    status_code: int = 200

    @property
    def html(self) -> str:
        paragraphs = "\n".join(
            f"<p>{line.strip()}</p>" for line in self.body.strip().split("\n\n") if line.strip()
        )
        return (
            f"<html><head><title>{self.title}</title></head><body>"
            f"<nav>Home About Contact</nav>"
            f"<article><h1>{self.title}</h1>{paragraphs}</article>"
            f"<footer>Copyright 2026.</footer></body></html>"
        )

    def as_search_result(self) -> dict[str, object]:
        return {
            "url": self.url,
            "title": self.title,
            "content": self.snippet or self.body.strip().split("\n")[0][:200],
            "score": 0.9,
        }


@dataclass
class Web:
    """The internet this run can see, and what it does when asked.

    ``search_error`` and ``fetch_error`` are the scripted outages: a status to
    return, or a timeout to hang into. They are transport-level on purpose. A
    scenario that raised ``FetchTimeout`` from a double would prove the graph
    handles an exception someone chose to raise; making the socket time out
    proves the client classifies it as one, which is the half that can break.
    """

    pages: list[Page] = field(default_factory=list)
    #: Query substring -> the pages that query returns. The default answers
    #: every query with every page.
    answers: dict[str, list[Page]] = field(default_factory=dict)
    #: Raised by the mock transport when the search endpoint is called.
    search_error: BaseException | None = None
    #: Query substring -> an exception raised for that query alone, so a
    #: scenario can fail one search of several and watch the others land.
    search_errors: dict[str, BaseException] = field(default_factory=dict)
    #: URL -> an exception the transport raises instead of serving it.
    fetch_errors: dict[str, BaseException] = field(default_factory=dict)
    #: URL -> where it redirects to. The guard follows hops one at a time and
    #: re-validates each, so this is how a scenario aims the second hop
    #: somewhere the first hop could not have gone.
    redirects: dict[str, str] = field(default_factory=dict)
    #: Every request the scripted internet was asked for, in order.
    requested: list[str] = field(default_factory=list)
    searches: list[str] = field(default_factory=list)

    def by_url(self, url: str) -> Page | None:
        return next((page for page in self.pages if page.url == url), None)

    def results_for(self, query: str) -> list[Page]:
        for fragment, pages in self.answers.items():
            if fragment.lower() in query.lower():
                return pages
        return list(self.pages) if not self.answers else []

    @property
    def fetches(self) -> list[str]:
        """The page URLs fetched, excluding robots.txt and the search endpoint."""
        return [
            url
            for url in self.requested
            if not url.endswith("/robots.txt") and "api.tavily.com" not in url
        ]

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.requested.append(url)

        if "api.tavily.com" in url:
            return self._search(request)
        if url.endswith("/robots.txt"):
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")

        error = self.fetch_errors.get(url)
        if error is not None:
            raise error

        destination = self.redirects.get(url)
        if destination is not None:
            return httpx.Response(302, headers={"location": destination})

        page = self.by_url(url)
        if page is None:
            return httpx.Response(404, text="Not found")
        if page.status_code != 200:
            return httpx.Response(page.status_code, text="")
        body = page.html if page.content_type.startswith("text/html") else page.body
        return httpx.Response(
            page.status_code,
            content=body.encode(),
            headers={"content-type": page.content_type},
        )

    def _search(self, request: httpx.Request) -> httpx.Response:
        if self.search_error is not None:
            raise self.search_error
        import json

        query = str(json.loads(request.content or b"{}").get("query", ""))
        self.searches.append(query)
        for fragment, error in self.search_errors.items():
            if fragment.lower() in query.lower():
                raise error
        return httpx.Response(
            200,
            json={"results": [page.as_search_result() for page in self.results_for(query)]},
            headers={"content-type": "application/json"},
        )


@contextmanager
def scripted_dns() -> Iterator[None]:
    """Every hostname resolves to one public address, without leaving the process."""

    async def resolve(host: str, port: int) -> tuple[IpAddress, ...]:
        import ipaddress

        return (ipaddress.ip_address(PUBLIC_ADDRESS),)

    with patch("app.sources.urls.resolve", resolve):
        yield


# --- the scripted model -------------------------------------------------------------

Rule = Callable[[CompletionRequest], BaseModel]


def prompt_text(request: CompletionRequest) -> str:
    """Everything one call was sent, as one string.

    The unit a scenario reasons about: a rule reads it to decide its answer, and
    an assertion reads it to prove that a hostile line never left the delimited
    block it arrived in.
    """
    return "\n".join(message.content for message in request.prompt.messages)


@dataclass
class ScriptedBrain:
    """Answers each structured call from a rule chosen by the schema it asked for.

    ``on`` registers an answer for a schema: a value, a sequence consumed in
    order, or a callable that reads the request. ``when`` narrows a rule to
    calls whose prompt contains a fragment, which is how two subtasks running at
    once get different answers without depending on which one arrives first.
    """

    prompts: list[CompletionRequest] = field(default_factory=list)
    #: The schema each call asked for, in order, parallel to ``prompts``.
    asked: list[type[BaseModel]] = field(default_factory=list)
    prompt_tokens: int = 120
    completion_tokens: int = 60
    _rules: list[tuple[type[BaseModel], str | None, Rule]] = field(default_factory=list)
    _failures: list[tuple[type[BaseModel], list[BaseException | None]]] = field(
        default_factory=list
    )

    @property
    def name(self) -> Any:
        from app.core.enums import LlmProvider

        return LlmProvider.ANTHROPIC

    @property
    def calls(self) -> int:
        return len(self.prompts)

    def user_prompts(self) -> list[str]:
        return [message.content for request in self.prompts for message in request.prompt.messages]

    def calls_for(self, schema: type[BaseModel]) -> int:
        """How many calls asked for this schema - the per-agent call count."""
        return sum(1 for declared in self.asked if declared is schema)

    def prompts_for(self, schema: type[BaseModel]) -> list[str]:
        """The whole user prompt of each call that asked for this schema.

        One string per call, not per message: a test asserting that a hostile
        line never appeared outside the data block has to look at what one call
        was sent, and messages split arbitrarily.
        """
        return [
            prompt_text(request)
            for request, declared in zip(self.prompts, self.asked, strict=False)
            if declared is schema
        ]

    # --- scripting ---------------------------------------------------------------

    def on(
        self,
        schema: type[BaseModel],
        answer: BaseModel | Sequence[BaseModel] | Rule,
        *,
        when: str | None = None,
    ) -> ScriptedBrain:
        """Answer calls for ``schema`` (whose prompt contains ``when``) with ``answer``."""
        rule: Rule
        if isinstance(answer, BaseModel):
            rule = lambda _request, value=answer: value  # noqa: E731
        elif callable(answer):
            rule = answer
        else:
            queue = list(answer)
            rule = lambda _request, q=queue: q.pop(0) if len(q) > 1 else q[0]  # noqa: E731
        # Last registered wins. A scenario starts from a script that answers
        # every call and then says its one sentence - "but the extractor quotes
        # something that is not there" - so the later registration has to be the
        # one that takes effect, whether or not it is narrowed.
        self._rules.insert(0, (schema, when, rule))
        return self

    def hangs(self, schema: type[BaseModel]) -> ScriptedBrain:
        """Never answer calls for ``schema``.

        A node genuinely mid-call is the state a graceful shutdown and a lease
        takeover both have to work from, and the only dependable way to be in it
        is a call that does not come back. The worker that is stopped cancels
        the task, which is what a process ending does to the work it held.
        """

        async def forever(_request: CompletionRequest) -> BaseModel:
            await asyncio.sleep(3600)
            raise AssertionError("unreachable")  # pragma: no cover

        return self.on(schema, forever)

    def fails(self, schema: type[BaseModel], *failures: BaseException | None) -> ScriptedBrain:
        """Raise these, in order, on the next calls for ``schema``.

        A ``None`` in the sequence is a call that succeeds, so a scenario can
        script "rate limited twice, then fine" as the provider would behave.
        """
        self._failures.append((schema, list(failures)))
        return self

    # --- the provider interface -----------------------------------------------------

    async def generate(self, request: CompletionRequest) -> Completion:
        raise CapabilityNotSupported("This scripted model only answers structured calls.")

    async def generate_structured(
        self, request: CompletionRequest, schema: type[StructuredT]
    ) -> StructuredCompletion[StructuredT]:
        self.prompts.append(request)
        self.asked.append(schema)
        self._maybe_fail(schema)
        value = await self._answer(request, schema)
        if not isinstance(value, schema):
            raise StructuredOutputInvalid(
                "The script's answer is not the schema this call asked for.",
                context={"expected": schema.__name__, "scripted": type(value).__name__},
            )
        return StructuredCompletion(
            value=value,
            completion=Completion(
                text="{}",
                provider=self.name,
                model=request.model,
                usage=TokenUsage(
                    prompt_tokens=self.prompt_tokens, completion_tokens=self.completion_tokens
                ),
                finish_reason="stop",
                latency_ms=7,
            ),
        )

    async def stream(self, request: CompletionRequest) -> Any:
        raise CapabilityNotSupported("This scripted model does not stream.")

    async def embed(self, texts: Sequence[str], *, model: str) -> Any:
        raise CapabilityNotSupported("This scripted model has no embeddings.")

    async def count_tokens(self, request: CompletionRequest) -> int:
        raise CapabilityNotSupported("This scripted model cannot count tokens.")

    async def check(self) -> bool:
        return True

    async def close(self) -> None:
        return None

    # --- internals ------------------------------------------------------------------

    def _maybe_fail(self, schema: type[BaseModel]) -> None:
        for declared, remaining in self._failures:
            if declared is schema and remaining:
                failure = remaining.pop(0)
                if failure is not None:
                    raise failure
                return

    async def _answer(self, request: CompletionRequest, schema: type[BaseModel]) -> BaseModel:
        """The first matching rule's answer, awaited if the rule is async.

        Async rules are what let a scenario say "while this call is in flight,
        the user cancels" or "this call never returns" - both of which are
        states the system has to be *in*, not states it can be told about.
        """
        text = prompt_text(request)
        for declared, fragment, rule in self._rules:
            if declared is not schema:
                continue
            if fragment is not None and fragment not in text:
                continue
            answer = rule(request)
            if inspect.isawaitable(answer):
                answer = await answer
            return answer
        raise StructuredOutputInvalid(
            f"The script has no answer for a {schema.__name__} call.",
            context={"schema": schema.__name__},
        )


@dataclass(frozen=True, slots=True)
class Telemetry:
    """The recorder chain ``app.workers.runner`` composes, built the same way.

    Composed rather than replaced because each layer is load-bearing for a
    different scenario: the guard is what refuses a call that would take a run
    past its ceiling, the database recorders are what make ``/activity`` and a
    run's cost answerable from rows, and the collecting recorders are what a
    test asserts on without going back to the database for every count.
    """

    budget: RunBudgetGuard
    #: The model calls this run made, as the innermost recorder saw them.
    calls: CollectingCallRecorder
    #: The tool calls, likewise.
    tools: CollectingToolRecorder
    #: The chain the toolbelt is built with: a row, then a log line, then the
    #: collector above.
    tool_recorder: ToolCallRecorder


def telemetry_for(settings: Settings, database: Database) -> Telemetry:
    from app.db.repositories.trace import SqlAlchemyTraceStore
    from app.observability.ledger import DatabaseCallRecorder, DatabaseToolRecorder

    trace = SqlAlchemyTraceStore(database)
    calls = CollectingCallRecorder()
    tools = CollectingToolRecorder()
    return Telemetry(
        budget=RunBudgetGuard(
            DatabaseCallRecorder(trace, calls),
            require_priced=settings.require_priced_models,
        ),
        calls=calls,
        tools=tools,
        tool_recorder=DatabaseToolRecorder(trace, tools),
    )


def gateway_over(brain: ScriptedBrain, *, settings: Settings, budget: RunBudgetGuard) -> LLMGateway:
    """The real gateway, over the scripted provider, priced from the test registry.

    Every bound is read from configuration, as ``build_gateway`` reads it, so the
    retry loop and the fallback chain a scenario exercises are the ones a
    deployment has. Only the registry differs, because the test registry is what
    declares prices without naming a real vendor's model.

    The guard is both the recorder and the budget, exactly as the worker process
    wires it: that is what makes a ceiling enforced *before* a call rather than
    noticed after the node that made it has already returned.
    """
    from app.core.enums import LlmProvider

    declared = fake.registry(priced=True)
    return LLMGateway(
        registry=declared,
        router=ModelRouter(declared),
        providers={LlmProvider.ANTHROPIC: brain},
        recorder=budget,
        budget=budget,
        max_concurrent_calls=settings.llm_max_concurrent_calls,
        max_attempts_per_model=settings.llm_max_attempts,
        request_timeout_seconds=settings.llm_request_timeout_seconds,
        retry_base_delay_seconds=settings.llm_retry_base_delay_seconds,
        retry_max_delay_seconds=settings.llm_retry_max_delay_seconds,
    )


# --- assembly -----------------------------------------------------------------------


@dataclass
class World:
    """One worker process and the scripted world it researches in."""

    harness: harness.Harness
    web: Web
    brain: ScriptedBrain
    telemetry: Telemetry
    dependencies: ResearchDependencies
    #: The nine nodes this world's workers run. Kept so a replacement process
    #: can be built over the same agents, which is what a redeploy does.
    nodes: ResearchNodes
    settings: Settings
    database: Database

    @property
    def calls(self) -> CollectingCallRecorder:
        """The model calls this world's runs have made."""
        return self.telemetry.calls

    async def close(self) -> None:
        await self.dependencies.close()


def scenario_settings(base: Settings, **overrides: object) -> Settings:
    """Worker timings turned down, a search vendor configured, chunks made small.

    Every value here is a production setting given a test's value. None of them
    selects a different code path, which is the property that makes a scenario
    suite worth anything: the run under test is the run a deployment performs.
    """
    values: dict[str, object] = {
        "search_provider": "tavily",
        "tavily_api_key": "scenario-key",
        "chunk_size_tokens": CHUNK_TOKENS,
        "chunk_overlap_tokens": CHUNK_OVERLAP_TOKENS,
        # One subtask at a time by default: a scenario that wants two says so,
        # and the ones that do not get a deterministic ordering for free.
        "max_subtasks_per_iteration": 1,
        "researcher_results_per_query": 5,
        "worker_max_attempts": 2,
        # A scenario about a retryable failure is about what the run ends up as,
        # so the run has to actually reach its second attempt. The worker harness
        # holds the backoff at thirty seconds - right for a test that wants a
        # paused run to *stay* paused - which here would mean waiting out a
        # production backoff curve in a suite that has fifteen of these.
        "worker_retry_base_delay_seconds": 0.05,
        "worker_retry_max_delay_seconds": 0.1,
        # The same argument one layer down: the gateway's own retry curve is
        # what a rate-limit scenario is about, and half a second per attempt
        # buys nothing a hundredth of a second does not.
        "llm_retry_base_delay_seconds": 0.01,
        "llm_retry_max_delay_seconds": 0.05,
        # The worker harness turns this down to five seconds for scripted nodes
        # that return instantly. A real researcher node fetches, parses, chunks
        # and stores a page, and the first one in a process also pays LlamaIndex's
        # import - about four and a half seconds on its own. Five seconds is
        # therefore not a slow node, it is a node that never finishes, and a
        # suite tuned that way would test the timeout and nothing else.
        #
        # Ninety, not thirty, for the same reason one step further out: these run
        # four-wide under xdist on a CPU that never turbos, and at thirty the
        # researcher node intermittently ran out of time - which the suite then
        # reported as a run that gathered nothing, several layers from the cause.
        # A node ceiling is a hang detector, not a performance assertion.
        "graph_node_timeout_seconds": 90.0,
        # And the lease has to move with it: a lease shorter than twice a node's
        # ceiling expires while that node is still running and lets a second
        # worker take the run, which `Settings` refuses outright. The harness's
        # sixty seconds is exactly twice the thirty this used to be - so raising
        # one without the other fails every scenario at construction.
        "worker_lease_seconds": 240,
    }
    values.update(overrides)
    return harness.worker_settings(base, **values)


def build_dependencies(
    settings: Settings,
    *,
    gateway: LLMGateway,
    database: Database,
    storage: ObjectStorage,
    transport: httpx.AsyncBaseTransport,
    tool_recorder: ToolCallRecorder,
) -> ResearchDependencies:
    """What ``app.agents.factory.build_dependencies`` builds, minus two things.

    Assembled here rather than called because the scenarios need control of two
    arguments that the production factory rightly does not expose: the socket,
    and whether ingestion embeds. The production wiring is covered by
    ``test_agents_end_to_end.py``; what is covered here is the behaviour of a
    run, which needs the parser in process and the vectors left pending.
    """
    from app.retrieval.factory import build_retriever

    return ResearchDependencies(
        gateway=gateway,
        database=database,
        storage=storage,
        toolbelt=build_toolbelt(settings, transport=transport, recorder=tool_recorder),
        ingestor=DocumentIngestor(
            database=database,
            storage=storage,
            parser=InProcessParser(
                ParseLimits(
                    max_pdf_pages=settings.max_pdf_pages, max_chars=settings.max_document_chars
                )
            ),
            chunker=Chunker(
                chunk_size_tokens=settings.chunk_size_tokens,
                chunk_overlap_tokens=settings.chunk_overlap_tokens,
                max_chunks=settings.max_chunks_per_document,
            ),
            embedder=None,
            max_chunks=settings.max_chunks_per_document,
        ),
        # ``gateway=None`` is the no-embedding-model deployment: the dense arm
        # reports itself skipped rather than pretending to have searched.
        retriever=build_retriever(settings, database=database, gateway=None),
    )


def build_world(
    *,
    database: Database,
    storage: ObjectStorage,
    settings: Settings,
    web: Web,
    brain: ScriptedBrain,
    nodes: ResearchNodes | None = None,
    worker_id: str = "worker-1",
) -> World:
    """The worker a deployment runs, over the scripted web and the scripted model."""
    telemetry = telemetry_for(settings, database)
    gateway = gateway_over(brain, settings=settings, budget=telemetry.budget)
    dependencies = build_dependencies(
        settings,
        gateway=gateway,
        database=database,
        storage=storage,
        transport=web.transport(),
        tool_recorder=telemetry.tool_recorder,
    )
    bundle = nodes or build_research_nodes(settings, dependencies=dependencies)
    return World(
        harness=harness.build_worker(
            database,
            storage,
            settings,
            bundle=bundle,
            worker_id=worker_id,
            budget=telemetry.budget,
        ),
        web=web,
        brain=brain,
        telemetry=telemetry,
        dependencies=dependencies,
        nodes=bundle,
        settings=settings,
        database=database,
    )


def respawned(world: World, *, worker_id: str = "worker-2") -> World:
    """The next worker process: same queue, same checkpoints, a new loop.

    A new graph runner over the same agents, because a replacement process
    rebuilds everything except what is durable - the queue, the database, and
    the checkpoints the interrupted run left behind.
    """
    return dataclasses.replace(
        world,
        harness=harness.respawn(
            world.harness,
            world.database,
            world.dependencies.storage,
            bundle=world.nodes,
            worker_id=worker_id,
            budget=world.telemetry.budget,
        ),
    )


# --- driving a run ------------------------------------------------------------------


async def queue_run(
    world: World,
    *,
    mode: ResearchMode = ResearchMode.DEEP,
    question: str = "How do AI inference providers price hosted H100 capacity?",
    user_id: uuid.UUID | None = None,
    **row_values: object,
) -> Any:
    """A queued run row and its job, exactly as the API creates them."""
    run = await harness.seed_run(
        world.database,
        world.settings,
        user_id=user_id,
        mode=mode,
        question=question,
        **row_values,
    )
    await world.harness.queue.enqueue(run.id)
    return run


#: How long a scenario waits for the condition it is watching for. Generous on
#: purpose, and not a measurement of anything: a scenario run is a few seconds,
#: but these execute alongside three other xdist workers on a CPU that never
#: turbos, and a deadline tuned to the good case turns load into a red suite.
#: What it is for is a *hung* run failing rather than hanging forever - so it
#: sits above the node ceiling above, which is what should notice first.
SCENARIO_DEADLINE_SECONDS = 180.0


async def run_until(
    world: World, condition: Any, *, deadline_seconds: float = SCENARIO_DEADLINE_SECONDS
) -> None:
    await harness.serve_until(world.harness, condition, deadline_seconds=deadline_seconds)


def page_of(url: str, title: str, body: str, snippet: str = "") -> Page:
    return Page(url=url, title=title, body=body, snippet=snippet)


def sources_of(state: Mapping[str, Any]) -> list[str]:
    return [source.url for source in state.get("sources") or ()]


def utc(year: int, month: int, day: int) -> dt.datetime:
    return dt.datetime(year, month, day, tzinfo=dt.UTC)


def skip_without_postgres(postgres: ProvisionedDatabase | None) -> None:
    import pytest

    from tests.support.postgres import SKIP_REASON

    if postgres is None:
        pytest.skip(SKIP_REASON)
