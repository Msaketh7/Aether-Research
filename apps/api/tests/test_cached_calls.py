"""Caching where it actually costs money: the web tools and the embedder.

The unit tests for the machinery are in ``test_cache.py``. These drive the real
toolbelt over a mock socket and the real gateway over a mock provider, and
count what left the process - because the only claim worth making is that the
second call did not make a request, and only a transport can say that.
"""

from __future__ import annotations

from collections.abc import Callable

import httpx2 as httpx

from app.cache import CacheNamespace, InMemoryCache, ResponseCache, disabled_cache
from app.cache.store import CachePolicy
from app.core.config import Settings
from app.core.enums import LlmProvider
from app.models import (
    CollectingCallRecorder,
    EmbeddingResult,
    LLMGateway,
    TokenUsage,
    load_registry,
)
from app.models.routing import ModelRouter
from app.sources import (
    CollectingToolRecorder,
    ExtractContentInput,
    FetchUrlInput,
    SafeHttpClient,
    Toolbelt,
    ToolbeltConfig,
    ToolExecutor,
    WebSearchInput,
)
from app.sources.tools import search

Handler = Callable[[httpx.Request], httpx.Response]

ARTICLE = """
<html><head><title>Acme Q3 Results</title></head><body>
<article><h1>Acme Q3 Results</h1>
<p>Acme Corporation reported third quarter revenue of $4.2 billion, an increase
of eighteen percent compared with the same period last year. The company said
demand for its inference infrastructure products drove the gain, with capacity
constrained through the end of the year.</p>
<p>Operating margin widened to 31 percent from 27 percent. Management raised
full year guidance and said it would add two data centre regions.</p></article>
</body></html>
"""


def cache(**overrides: object) -> ResponseCache:
    fields: dict[str, object] = {
        "ttl_seconds": dict.fromkeys(CacheNamespace, 300),
        "enabled": frozenset(CacheNamespace),
        "max_value_bytes": 1024 * 1024,
    }
    fields.update(overrides)
    return ResponseCache(InMemoryCache(), CachePolicy(**fields))  # type: ignore[arg-type]


class CountingTransport:
    """A socket that says which requests actually reached it.

    ``pages`` excludes robots.txt, which the fetcher reads once per origin and
    caches itself - counting it here would make the interesting number depend
    on politeness rather than on the cache.
    """

    def __init__(self, handler: Handler) -> None:
        self.paths: list[str] = []
        self._handler = handler

    @property
    def pages(self) -> int:
        return sum(1 for path in self.paths if not path.endswith("/robots.txt"))

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.paths.append(str(request.url))
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return self._handler(request)


def belt(
    transport: CountingTransport,
    *,
    response_cache: ResponseCache | None = None,
    provider: search.SearchProvider | None = None,
) -> tuple[Toolbelt, CollectingToolRecorder]:
    recorder = CollectingToolRecorder()
    return (
        Toolbelt(
            client=SafeHttpClient(transport=httpx.MockTransport(transport)),  # type: ignore[arg-type]
            executor=ToolExecutor(
                recorder=recorder,
                max_attempts=1,
                # Generous on purpose. What is under test is whether a second
                # call reached the network, not how fast trafilatura parses -
                # and extraction is CPU-bound, so a tight bound here turns a
                # loaded machine into a failing cache test.
                timeout_seconds=60,
            ),
            config=ToolbeltConfig(
                sec_user_agent="AetherResearch/test (test@example.com)",
                github_token=None,
                search_provider=provider,
            ),
            cache=response_cache or disabled_cache(),
        ),
        recorder,
    )


def html(_: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200,
        content=ARTICLE.encode(),
        headers={"content-type": "text/html; charset=utf-8"},
    )


# --- fetch ----------------------------------------------------------------


async def test_the_same_page_is_fetched_once():
    transport = CountingTransport(html)
    toolbelt, _ = belt(transport, response_cache=cache())
    request = FetchUrlInput(url="https://example.com/article")

    first = await toolbelt.fetch_url(request)
    second = await toolbelt.fetch_url(request)

    assert transport.pages == 1, "the second call reached the network"
    assert first.cache_hit is False
    assert second.cache_hit is True
    assert second.value.content_hash == first.value.content_hash
    assert second.value.raw_bytes == first.value.raw_bytes


async def test_a_cache_hit_is_still_a_recorded_tool_call():
    """A cache that made calls vanish from the ledger would make the trace
    disagree with the run: the agent did ask, and it acted on an answer."""
    transport = CountingTransport(html)
    toolbelt, recorder = belt(transport, response_cache=cache())
    request = FetchUrlInput(url="https://example.com/article")

    await toolbelt.fetch_url(request)
    await toolbelt.fetch_url(request)

    assert [call.cache_hit for call in recorder.calls] == [False, True]
    assert all(call.tool_name.value == "fetch" for call in recorder.calls)


async def test_a_cached_body_is_sanitised_the_way_a_fetched_one_is():
    """Reconstructed through ``UntrustedText``, never revived (ADR 0011)."""
    marker = "<html><body><p>" + ("Ordinary prose about revenue. " * 20) + "‮text‬</p></body></html>"
    transport = CountingTransport(
        lambda _: httpx.Response(
            200, content=marker.encode(), headers={"content-type": "text/html"}
        )
    )
    toolbelt, _ = belt(transport, response_cache=cache())
    request = FetchUrlInput(url="https://example.com/rtl")

    fresh = (await toolbelt.fetch_url(request)).value
    cached = (await toolbelt.fetch_url(request)).value

    assert cached.body.expose() == fresh.body.expose()
    assert "‮" not in cached.body.expose()


async def test_the_terms_a_page_was_fetched_under_are_part_of_its_key():
    """A page fetched despite robots.txt must not be served to a caller that
    did not ask to override it."""
    transport = CountingTransport(html)
    toolbelt, _ = belt(transport, response_cache=cache())

    await toolbelt.fetch_url(FetchUrlInput(url="https://example.com/a", ignore_robots=True))
    await toolbelt.fetch_url(FetchUrlInput(url="https://example.com/a", ignore_robots=False))

    assert transport.pages == 2


async def test_without_a_cache_every_call_is_a_request():
    transport = CountingTransport(html)
    toolbelt, _ = belt(transport)
    request = FetchUrlInput(url="https://example.com/article")

    await toolbelt.fetch_url(request)
    await toolbelt.fetch_url(request)

    assert transport.pages == 2


# --- search ---------------------------------------------------------------


class StubProvider:
    """A search vendor that counts the queries it was actually asked."""

    def __init__(self, name: str = "tavily") -> None:
        self._name = name
        self.queries: list[str] = []

    @property
    def name(self) -> str:
        return self._name

    async def search(
        self, request: WebSearchInput, client: SafeHttpClient
    ) -> list[search.SearchResult]:
        self.queries.append(request.query)
        return [
            search.SearchResult(
                url="https://example.com/a",
                canonical_url="https://example.com/a",
                domain="example.com",
                title="A result",
                snippet="A snippet the provider wrote.",
                score=0.8,
                published_at=None,
            )
        ]


async def test_the_same_query_is_asked_once():
    provider = StubProvider()
    toolbelt, _ = belt(CountingTransport(html), response_cache=cache(), provider=provider)
    request = WebSearchInput(query="inference pricing comparison")

    first = await toolbelt.web_search(request)
    second = await toolbelt.web_search(request)

    assert provider.queries == ["inference pricing comparison"]
    assert second.cache_hit is True
    assert second.value.results[0].url == first.value.results[0].url
    assert second.value.results[0].score == 0.8


async def test_a_narrower_query_is_a_different_question():
    provider = StubProvider()
    toolbelt, _ = belt(CountingTransport(html), response_cache=cache(), provider=provider)

    await toolbelt.web_search(WebSearchInput(query="inference pricing"))
    await toolbelt.web_search(
        WebSearchInput(query="inference pricing", include_domains=("example.com",))
    )
    await toolbelt.web_search(WebSearchInput(query="inference pricing", recency_days=30))

    assert len(provider.queries) == 3


async def test_two_vendors_answers_are_not_confused_with_each_other():
    """A failover must not serve one provider's results under the other's name."""
    shared = cache()
    tavily = StubProvider("tavily")
    brave = StubProvider("brave")
    first_belt, _ = belt(CountingTransport(html), response_cache=shared, provider=tavily)
    second_belt, _ = belt(CountingTransport(html), response_cache=shared, provider=brave)
    request = WebSearchInput(query="inference pricing")

    await first_belt.web_search(request)
    answer = await second_belt.web_search(request)

    assert brave.queries == ["inference pricing"]
    assert answer.value.provider == "brave"


# --- extraction -----------------------------------------------------------


async def test_extracting_the_same_html_twice_parses_it_once():
    """A deterministic, CPU-bound transformation of bytes already in hand."""
    toolbelt, _ = belt(CountingTransport(html), response_cache=cache())
    request = ExtractContentInput(html=ARTICLE, source_url="https://example.com/article")

    first = await toolbelt.extract_content(request)
    second = await toolbelt.extract_content(request)

    assert second.cache_hit is True
    assert second.value.text.expose() == first.value.text.expose()
    assert second.value.title == first.value.title
    assert second.value.method == first.value.method


# --- embeddings -----------------------------------------------------------


class CountingEmbeddingProvider:
    """A provider that says how many texts it was actually asked to embed."""

    def __init__(self) -> None:
        self.batches: list[list[str]] = []

    async def embed(self, texts: list[str], *, model: str) -> EmbeddingResult:
        self.batches.append(list(texts))
        return EmbeddingResult(
            # A vector derived from the text, so a vector that ends up beside
            # the wrong one is visible rather than merely plausible.
            vectors=[[float(sum(map(ord, text))), 0.5, 0.25] for text in texts],
            provider=LlmProvider.OPENAI,
            model=model,
            usage=TokenUsage(prompt_tokens=sum(len(t) for t in texts), completion_tokens=0),
            latency_ms=12,
        )

    async def generate(self, request: object) -> object:  # pragma: no cover - unused
        raise NotImplementedError

    async def generate_structured(self, request: object, schema: object) -> object:
        raise NotImplementedError  # pragma: no cover - unused

    async def stream(self, request: object) -> object:  # pragma: no cover - unused
        raise NotImplementedError

    async def count_tokens(self, request: object) -> int:  # pragma: no cover - unused
        raise NotImplementedError

    async def check(self) -> bool:  # pragma: no cover - unused
        return True

    async def close(self) -> None:
        return None


def gateway_over(
    provider: CountingEmbeddingProvider, response_cache: ResponseCache
) -> tuple[LLMGateway, CollectingCallRecorder]:
    registry = load_registry(None)
    recorder = CollectingCallRecorder()
    spec = ModelRouter(registry).embedding_model()
    return (
        LLMGateway(
            registry=registry,
            router=ModelRouter(registry),
            providers={spec.provider: provider},  # type: ignore[dict-item]
            recorder=recorder,
            cache=response_cache,
        ),
        recorder,
    )


async def test_a_text_is_embedded_once_however_often_it_is_seen():
    """Re-ingesting a document the corpus already holds is the common case."""
    provider = CountingEmbeddingProvider()
    gateway, recorder = gateway_over(provider, cache())

    first = await gateway.embed(["alpha", "beta"])
    second = await gateway.embed(["alpha", "beta"])

    # One batch, of two texts. The texts the provider sees carry the model's
    # own task prefix, which is why they are matched by suffix.
    assert len(provider.batches) == 1
    assert [text.endswith(("alpha", "beta")) for text in provider.batches[0]] == [True, True]
    assert list(second.vectors) == list(first.vectors)
    assert second.usage.prompt_tokens == 0, "nothing was sent, so nothing was spent"
    assert [call.cache_hit for call in recorder.calls] == [False, True]


async def test_only_the_texts_that_are_new_are_sent():
    """The whole point of caching per text rather than per batch."""
    provider = CountingEmbeddingProvider()
    gateway, _ = gateway_over(provider, cache())
    await gateway.embed(["alpha", "beta"])

    merged = await gateway.embed(["alpha", "gamma", "beta"])

    assert len(provider.batches[-1]) == 1
    assert provider.batches[-1][0].endswith("gamma")
    # And the vectors come back in the order the texts were given, which is the
    # property a shifted vector would silently break.
    alpha, gamma, beta = (vector[0] for vector in merged.vectors)
    assert alpha != gamma and gamma != beta
    assert alpha == (await gateway.embed(["alpha"])).vectors[0][0]


async def test_a_query_and_a_passage_are_not_the_same_cache_entry():
    """An asymmetric model prefixes them differently, and the key is the
    prepared text - so a query must not be served a passage's vector."""
    from app.models.base import EmbeddingPurpose

    provider = CountingEmbeddingProvider()
    gateway, _ = gateway_over(provider, cache())

    await gateway.embed(["alpha"], purpose=EmbeddingPurpose.DOCUMENT)
    await gateway.embed(["alpha"], purpose=EmbeddingPurpose.QUERY)

    assert len(provider.batches) == 2


async def test_embeddings_are_not_cached_when_that_is_turned_off():
    provider = CountingEmbeddingProvider()
    gateway, _ = gateway_over(provider, cache(enabled=frozenset({CacheNamespace.SEARCH})))

    await gateway.embed(["alpha"])
    await gateway.embed(["alpha"])

    assert len(provider.batches) == 2


def test_the_settings_that_decide_all_of_this_have_defaults():
    """Caching on by default, and every TTL declared rather than implied."""
    settings = Settings(app_env="test")

    assert settings.cache_enabled is True
    assert settings.cache_search_ttl_seconds < settings.cache_page_ttl_seconds
    assert settings.cache_page_ttl_seconds < settings.cache_embedding_ttl_seconds


def test_the_synthesizer_still_has_no_tools_to_cache():
    """A narrowed belt shares the cache but not the permissions (TDD 15.3)."""
    toolbelt, _ = belt(CountingTransport(html), response_cache=cache())

    narrowed = toolbelt.restricted_to(frozenset())

    assert narrowed.permitted == frozenset()
