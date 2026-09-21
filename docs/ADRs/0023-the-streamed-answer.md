# ADR 0023: The answer is an agent, a durable event stream, and a row

- **Status**: Accepted
- **Date**: 2026-09-21

## Context

The system could research a question and produce a report. It could not answer
one.

A run's only prose output was the report: an executive summary, key findings, a
detailed analysis, an evidence list and a reference list, assembled from rows
and served at `/research/{id}/report` once the citation validator had passed
over it. That is the right artifact. It is the wrong thing to show someone who
asked a question forty seconds ago and is watching a progress bar.

Three separate gaps, which is why one change addresses all of them:

1. **No direct answer.** A reader wanting to know what the research found had
   to read a document and derive it. Every comparable product — ChatGPT,
   Claude, Perplexity — leads with a few paragraphs that answer the question,
   and puts the apparatus behind it.
2. **Nothing arrived incrementally.** The progress stream said _which sources
   were found_ and _how many claims were extracted_; the text appeared all at
   once at the end, or not at all. `LLMGateway.stream` had existed since Phase
   6 and nothing had ever called it.
3. **The page was a dashboard about a question, not a reply to it.** The
   question box led to a screen of stat tiles, a checklist and a feed.

## Decision

**A tenth agent, not a second use of the synthesizer.** `AnswerAgent` has its
own role (`answerer`), its own prompt, its own output ceiling and its own tier
in the routing table. It shares the claim catalogue with the synthesizer and
nothing else.

The two jobs genuinely differ. One writes a document with a fixed section
order, assembled sections it may not author, and a repair loop behind it; the
other writes four paragraphs under a latency a person is watching. Collapsing
them would mean choosing one model, one ceiling and one set of instructions for
both — and the choice that suits a report is the wrong one for an answer.

**It runs before synthesis.** `… → critic → answerer → synthesizer → citation
validator`. Every path that stops discovery routes through the answerer first.
So the reader has their answer while the report is still being assembled, and a
run that dies at synthesis has still answered the question. The citation repair
loop deliberately does not come back through it: a marker that did not resolve
is a defect in the _report_, and re-answering would rewrite, under a reader, a
paragraph they have already read.

**Its text is streamed as `research_events` rows.** Three new types —
`answer_started`, `answer_delta`, `answer_completed` — carried by the transport
ADR 0018 already built. The stream is not a second channel beside the event bus;
it _is_ the event bus.

**Pieces are phrase-sized, not per-token.** Every published piece is a stored
row, so text accumulates in the worker and is published at about forty-eight
characters, or sooner when a slow model has kept the buffer waiting. The node
flushes the tail on the way out, whether the agent returned an answer, returned
nothing, or raised.

**`answer_completed` carries the whole text.** Repeating every character
already streamed is the point: a subscriber that dropped a piece heals instead
of showing a hole in the middle of a sentence, and a reader who was not
watching is served the same event on replay.

**The answer gets its own table.** `run_answers`, keyed on a derived id, one
row per run, written by the projection in the same transaction as the evidence
and the report.

## Consequences

### What this costs, stated plainly

**A streamed call cannot fail over, and this is the first call that streams.**
The gateway will not retry or switch model once a token has been delivered —
splicing two different answers together is worse than failing. So a dropped
connection mid-answer leaves half an answer on the reader's screen. The graph
records the failure and carries on to synthesis: the failure costs the answer,
never the run.

**Two exempt calls instead of one.** The synthesizer was already exempt from the
per-run cost ceiling, because FR-8 promises a partial result and the partial
result costs a model call. The answerer is exempt for the same reason and with
more force — a budget-stopped run that shows nothing at all is the outcome the
requirement exists to prevent. The overshoot past a ceiling is now two calls
wide rather than one.

**About forty more rows per run.** A four-paragraph answer is roughly forty
`answer_delta` rows, which is the same order as the source and claim events a
run already writes. The chunk size is the dial: larger is fewer rows and a
choppier stream. It is a setting, not a constant.

**The answer's citations are numbered against a report that may not exist yet.**
Markers are resolved through the report's citation list, and while the answer is
streaming there is no report. They render _unresolved_ rather than disappearing.
That is the honest rendering of a citation whose chain has not been checked, and
it is the same rendering a citation that _failed_ the check gets.

**A duplicated artifact.** The answer restates what the report's executive
summary says. That is deliberate — they are read by different people at
different moments — but it is two pieces of prose that can disagree, and
nothing checks that they do not.

### What it buys

- A reader has an answer in seconds rather than a bar for minutes.
- A run that fails at synthesis has still answered the question.
- The answer replays exactly, because its pieces are durable rows.
- `LLMGateway.stream` is finally exercised, and the missing budget check it had
  carried since Phase 6 is now there.

### Rejected alternatives

**Have the synthesizer stream its executive summary.** Cheapest by far: no new
agent, no new role, no new table. Rejected because the section's job is to open
a document, it is written under instructions about section order and assembled
sections, and streaming it would still leave the reader waiting for the whole
report's generation to begin. It also ties the answer's existence to the
success of the most expensive step in the run.

**Stream over a second WebSocket or a dedicated SSE channel.** Rejected because
the run already has exactly one numbered, durable, replayable progress stream,
and a second one would need its own ordering, its own reconnect semantics and
its own relay through the API.

**Publish a row per token.** Rejected on cost: a few thousand inserts per run to
deliver text nobody can read at that rate. The batching is invisible to a reader
at forty-eight characters and is one setting away from finer.

**Store the answer on `reports`.** Rejected because the answer is produced
_before_ the report and is what the reader came for: hanging it off the report
would mean a run that answered the question and then died at synthesis had
nothing to show, which is precisely the failure streaming it early prevents.
