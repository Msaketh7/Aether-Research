# Diagrams

The editable sources behind the architecture pictures. Each one is drawn from
[`architecture.md`](../architecture.md), [`TDD.md`](../TDD.md),
[`threat-model.md`](../threat-model.md) and the code itself - not from the
prose alone. Where a document and the code disagreed, the code won; the two
places that happened are recorded at the bottom of this file.

**The rendered, clickable version of all of this is the Build Board** -
<https://claude.ai/artifact/2SYSiJiY9XNSZhAyx11ir6> - which carries the system
flow, the agent graph, the claim chain, the run lifecycle, the stack and the
system design document behind one link. It is access-controlled, so the link
only opens for people it has been shared with. These files are what you edit
when the system changes.

## The files

| File                                                                         | What it shows                                                                                                               | Open with            |
| ---------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------- | -------------------- |
| [`01-hld-container.drawio`](01-hld-container.drawio)                         | Containers, the three process types, the five trust boundaries, and the invariants legend                                   | diagrams.net         |
| [`02-research-graph.mmd`](02-research-graph.mmd)                             | The nine agents as `graph.py` wires them: conditional edges, the bounded re-plan loop, the quick-mode skip, the repair pass | any Mermaid renderer |
| [`03-run-lifecycle.mmd`](03-run-lifecycle.mmd)                               | One run end to end across browser, API, Redis, worker, graph and stores, with the crash / ceiling / cancel branches         | any Mermaid renderer |
| [`04-data-model.mmd`](04-data-model.mmd)                                     | The system of record as an ERD, centred on the source -> citation chain                                                     | any Mermaid renderer |
| [`05-rag-and-untrusted-content.drawio`](05-rag-and-untrusted-content.drawio) | Ingestion and hybrid retrieval side by side, with the SSRF layers and the injection controls as the panel they defend       | diagrams.net         |
| [`06-deployment-and-cicd.drawio`](06-deployment-and-cicd.drawio)             | ECS Fargate topology, the migrate task, and the five pipelines. **Written and validated, never applied**                    | diagrams.net         |
| [`07-scale-and-failure-modes.drawio`](07-scale-and-failure-modes.drawio)     | Measured capacity, the bottleneck ladder, and six failure modes as trigger -> what notices -> what recovers -> what is seen | diagrams.net         |
| [`08-hld-flow.excalidraw`](08-hld-flow.excalidraw)                           | The same high-level flow, hand-drawn, for whiteboard conversations                                                          | excalidraw.com       |
| [`09-lld-agent-graph.excalidraw`](09-lld-agent-graph.excalidraw)             | The agent graph, the node-wrapper contract and the `[n]` chain, hand-drawn                                                  | excalidraw.com       |

## Editing them

- **`.drawio`** - open at <https://app.diagrams.net> (File > Open From > Device),
  or with the Draw.io Integration extension in VS Code. The XML is uncompressed
  and diffs readably, so keep it that way: do not enable compressed saving.
- **`.mmd`** - plain Mermaid. Paste into <https://mermaid.live>, or render in any
  Markdown viewer that supports Mermaid fences.
- **`.excalidraw`** - open at <https://excalidraw.com> (menu > Open). These were
  generated from the drawing calls that produced the published versions and then
  loaded back into Excalidraw to confirm they open; they are ordinary scene files
  now, so edit and save them in place.

Two things the files do not carry:

1. **Edge routing.** The published renderings had an obstacle-avoiding router
   applied after layout. The committed XML holds the authored geometry with
   draw.io's default orthogonal routing, so a wire may cross a box until you
   nudge it.
2. **The camera walkthroughs.** The Excalidraw versions were drawn with a moving
   camera that framed each section in turn. A scene file has no camera, so that
   sequencing is gone.

## Where the numbers come from

Every figure in `07-scale-and-failure-modes.drawio` was measured on 2026-09-18
by `make loadtest` and `make loadtest-api-local`, and is reproduced from
[`load-testing.md`](../load-testing.md) and `data/loadtest/results.json`.
Nothing on any of these diagrams is estimated. Where something has not been
measured the diagram says so - and _not measured_, _zero_ and _not built_ are
treated as three different facts, the same way they are everywhere else in this
repository.

## Where the code and the documents disagree

The diagrams follow the code.

- **The graph's shape.** `TDD.md` 4.1 draws a simpler flow. The real graph has
  three conditional edges - `after_planner`, `after_critic`, `after_validation` -
  and the deep-mode nodes are added at build time, so quick mode is a different
  graph rather than a skipped branch. See `apps/api/app/agents/graph.py`.
- **Auth.js.** `TDD.md` 3.3 names it, written when the frontend was expected to
  own the session. Phase 20 built opaque server-side sessions in the API
  instead, beside the authorization checks that were already there. ADR 0021
  records why, and why the token is not a JWT.
