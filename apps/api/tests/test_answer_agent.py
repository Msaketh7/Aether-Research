"""The answerer: the question answered, in pieces, from claims it may cite.

Phase 27. What these pin is the set of things that are easy to get wrong in a
streaming agent and invisible once it ships:

* the text reaches the sink *as it arrives*, not in one piece at the end -
  which is the entire difference between this feature and a slower report;
* the answer is still a complete value afterwards, because a run is recorded
  from state and a reader arriving an hour later never saw a delta;
* citations are read out of the prose rather than taken on trust;
* a run with nothing to cite gets no answer rather than a fluent paragraph.
"""

from __future__ import annotations

import pytest

from app.agents.answer import AnswerAgent
from app.agents.schemas import MAX_ANSWER_CHARS
from app.core.enums import AgentName, ClaimStatus
from app.models.errors import ProviderUnavailable
from tests.support.agents import claim, contradiction, evidence, gateway, ident, source, state

pytestmark = pytest.mark.anyio


class Sink:
    """What the graph gives the agent: somewhere to put each piece."""

    def __init__(self) -> None:
        self.pieces: list[str] = []

    async def __call__(self, text: str) -> None:
        self.pieces.append(text)

    @property
    def text(self) -> str:
        return "".join(self.pieces)


def a_state(**overrides: object):
    """A run that has gathered two claims from one source."""
    span = evidence("ev-1")
    base = {
        "sources": [source()],
        "evidence": [span],
        "claims": [
            claim("claim-1", status=ClaimStatus.VERIFIED, confidence=0.82),
            claim(
                "claim-2",
                text="Provider B lists H100 capacity at 4,000 GPUs.",
                key="provider b | h100 capacity | 2026",
                object_value="4,000",
                status=ClaimStatus.VERIFIED,
                confidence=0.71,
            ),
        ],
    }
    base.update(overrides)
    return state(**base)


async def test_the_answer_reaches_the_sink_in_the_pieces_it_arrived_in() -> None:
    """Delivered as written, not assembled and handed over at the end.

    The assertion is on the *number* of pieces rather than only on the text. An
    implementation that buffered the whole answer and called the sink once would
    produce identical final text and none of the value.
    """
    pieces = ["The two providers ", "differ on price [1] ", "and on capacity [2]."]
    llm, model, _ = gateway()
    model.streams = [pieces]
    sink = Sink()

    result = await AnswerAgent(llm).answer(a_state(), on_delta=sink)

    assert sink.pieces == pieces
    assert result.value is not None
    assert result.value.text == "".join(pieces)


async def test_the_answer_survives_the_stream_it_was_delivered_in() -> None:
    """The returned draft is the whole answer, and knows what wrote it.

    A reader who opens the run tomorrow is served this value, not the stream, so
    a node that streamed perfectly and returned a fragment would look correct
    until the page was reloaded.
    """
    llm, model, _ = gateway()
    model.streams = [["Prices differ [1].", " Capacity differs [2]."]]

    result = await AnswerAgent(llm).answer(a_state(), on_delta=Sink())

    draft = result.value
    assert draft is not None
    assert draft.text == "Prices differ [1]. Capacity differs [2]."
    assert draft.model == "scripted-strong"
    assert draft.word_count == 6
    assert not draft.truncated


async def test_cited_claims_are_read_out_of_the_prose() -> None:
    """``claim_ids`` is derived from the markers, so it cannot be wished into being.

    A marker the catalogue does not have resolves to nothing and is not counted;
    the marker itself stays in the text, where the reader sees it unresolved
    rather than having a fabricated citation quietly deleted.
    """
    llm, model, _ = gateway()
    model.streams = [["Capacity is the differentiator [2], not price [9]."]]

    result = await AnswerAgent(llm).answer(a_state(), on_delta=Sink())

    draft = result.value
    assert draft is not None
    assert draft.claim_ids == (ident("claim-2"),)
    assert "[9]" in draft.text


async def test_a_run_with_no_claims_gets_no_answer_and_costs_nothing() -> None:
    """Nothing to cite means nothing to say. The model is never called."""
    llm, model, _ = gateway()

    result = await AnswerAgent(llm).answer(a_state(claims=[]), on_delta=Sink())

    assert result.value is None
    assert model.calls == 0


async def test_an_answer_of_only_whitespace_is_not_an_answer() -> None:
    """A provider that answered with silence produced no draft, and did not raise.

    Recorded as usage, because the call was still made and still paid for.
    """
    llm, model, _ = gateway()
    model.streams = [["   ", "\n\n"]]

    result = await AnswerAgent(llm).answer(a_state(), on_delta=Sink())

    assert result.value is None
    assert result.usage.tokens.total > 0


async def test_an_answer_longer_than_the_state_can_hold_is_cut_and_says_so() -> None:
    """The ceiling is enforced while streaming, not discovered at validation.

    Without it a model that ignored its token limit would fail schema validation
    *after* the reader had watched the whole thing arrive - the one moment an
    error is most expensive.
    """
    llm, model, _ = gateway()
    model.streams = [["x" * 4000] * 5]
    sink = Sink()

    result = await AnswerAgent(llm).answer(a_state(), on_delta=sink)

    draft = result.value
    assert draft is not None
    assert len(draft.text) <= MAX_ANSWER_CHARS
    assert draft.truncated
    # Nothing beyond the ceiling was shown to the reader either.
    assert len(sink.text) <= MAX_ANSWER_CHARS


async def test_a_stream_that_fails_partway_raises_with_the_partial_answer_delivered() -> None:
    """The gateway does not retry a stream that has already emitted, and nor does this.

    The reader keeps what arrived - the graph is what decides the run carries on
    to synthesis without an answer, and that decision is tested there.
    """
    llm, model, _ = gateway()
    model.streams = [["Prices differ [1].", " and then"]]
    model.stream_failures = {0: (1, ProviderUnavailable("the provider dropped the connection"))}
    sink = Sink()

    with pytest.raises(ProviderUnavailable):
        await AnswerAgent(llm).answer(a_state(), on_delta=sink)

    assert sink.text == "Prices differ [1]."


async def test_the_prompt_carries_the_claims_and_the_disagreement_between_them() -> None:
    """What the writer is told, and what it is told not to do with it."""
    llm, model, _ = gateway()
    model.streams = [["Both are reported [1][2]."]]

    await AnswerAgent(llm).answer(
        a_state(contradictions=[contradiction("c-1", a="claim-1", b="claim-2")]),
        on_delta=Sink(),
    )

    user = model.user_prompts()[0]
    assert "H100 inference costs $4.10 per GPU-hour." in user
    assert "do not choose between them" in user
    assert "Sources read: 1" in user
    assert "Claims available to cite: 2" in user


async def test_the_call_is_attributed_to_the_answerer_and_priced() -> None:
    """One ledger row, under its own role, with a cost the registry declares.

    Its own role rather than the synthesizer's is what makes "what did answering
    cost" a question the ledger can answer at all.
    """
    llm, model, recorder = gateway()
    model.streams = [["An answer [1]."]]

    result = await AnswerAgent(llm).answer(a_state(), on_delta=Sink())

    assert len(recorder.calls) == 1
    recorded = recorder.calls[0]
    assert recorded.role is AgentName.ANSWERER
    assert recorded.operation == "stream"
    assert recorded.cost_usd is not None
    assert result.usage.cost.measured
