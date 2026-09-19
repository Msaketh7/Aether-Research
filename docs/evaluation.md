# Aether Research: Evaluation Methodology

> **The rule that governs this document: no number appears anywhere in this
> repository unless it was produced by running the suite.** Placeholders are
> written as `not yet measured`. Fabricated benchmark results are treated as a
> defect, not as documentation.

## 1. Why evaluate

A research system that cannot measure its own groundedness is a text generator
with a citation-shaped decoration. Three questions must be answerable on every
commit:

1. Does retrieval surface the right material?
2. Is the report actually supported by what was retrieved?
3. Did the agents get there efficiently and reliably?

## 2. Dataset (`data/eval/`)

Each case is a versioned record:

```json
{
  "id": "inference-infra-comparison",
  "question": "Compare the major AI inference infrastructure companies ...",
  "mode": "deep",
  "expected_topics": ["pricing", "funding", "hardware", "competition"],
  "expected_source_types": ["web", "sec", "github"],
  "expected_claims": [{ "key": "company-x.funding.series-c", "must_be_present": true }],
  "notes": "checks contradiction handling on restated revenue figures"
}
```

Dataset versions are pinned per evaluation run (`dataset_version`) alongside the
`git_sha` under test, so a metric change can be attributed to code or to data.

Case classes deliberately included: a straightforward factual question; a
multi-entity comparison; a question with **known contradictory sources**; a
question with **no good sources**, whose correct behaviour is an honest
low-coverage report rather than a confident one.

## 3. Metrics

### 3.1 Retrieval

| Metric      | Question it answers                           |
| ----------- | --------------------------------------------- |
| Recall@K    | Did the relevant chunks come back at all?     |
| Precision@K | How much of what came back was worth reading? |
| MRR         | How high was the first relevant chunk?        |
| NDCG        | Is the ranking, not just the set, good?       |

Measured per retrieval strategy (dense, lexical, hybrid, hybrid + rerank) so
that ADR-level choices are backed by numbers rather than by preference.

Implemented in `app/retrieval/metrics.py` as pure functions and run by
`scripts/benchmark_retrieval.py`. Two conventions there matter more than the
formulae: recall divides by the number of chunks the labels name rather than by
`k`, and **a query with no labelled relevant chunk returns nothing, not zero** -
it is unmeasurable, and averaging it in as zero would let an incompletely
labelled dataset make every strategy look equally bad. The labelled datasets,
the method, and the first measured baseline are in `data/eval/retrieval/`.

### 3.2 Generation

| Metric             | Definition                                                                      |
| ------------------ | ------------------------------------------------------------------------------- |
| Correctness        | Does the answer match the reference on the checkable facts?                     |
| Groundedness       | Is every factual statement traceable to retrieved content?                      |
| Faithfulness       | Does the statement preserve the source's meaning, not merely echo its words?    |
| Citation precision | Of the citations emitted, how many resolve to evidence that supports the claim? |
| Citation recall    | Of the claims that need a citation, how many carry one?                         |

Citation precision and recall are computed **structurally** first - the chain
`citation -> claim -> evidence -> document -> source` either resolves or does
not - and only then judged for semantic support. A structural failure is a bug,
not a model quality issue.

### 3.3 Agent behaviour

| Metric                  | Definition                                              |
| ----------------------- | ------------------------------------------------------- |
| Task completion         | Fraction of planner subtasks that reached `done`        |
| Tool selection accuracy | Correct tool for the subtask type                       |
| Unnecessary tool calls  | Calls whose results never entered evidence              |
| Planning quality        | Coverage of `expected_topics` by the generated subtasks |
| Recovery rate           | Runs that completed despite an injected failure         |

### 3.4 System

Latency (P50/P95/P99), cost per run, failure rate, cache hit rate, tokens per
run. These come from the same telemetry that production uses (Phase 17), not
from a separate measurement path - which is what let Phase 21 answer "where does
a run's time go" with a `GROUP BY` over `agent_runs` rather than with a sampler.
The system half of this table **has** been measured, under a scripted provider:
[`load-testing.md`](load-testing.md). The research half has not.

## 4. Runner

`app/evaluations` executes a dataset against a target build, writes one
`evaluations` row per case, and emits a report artifact containing: dataset
version, git SHA, model routing configuration, per-case metrics, aggregates, and
pass/fail against thresholds. (`packages/evaluation` is the workspace package
that once held it; the runner lives with the code it drives, because a benchmark
that ran against a copy of the application would measure the copy.)

A judged metric (correctness, faithfulness) uses an LLM judge with a fixed
prompt version and a fixed model, both recorded in the report. Changing the
judge invalidates comparison with prior runs, and the report says so.

## 5. Thresholds and the CI gate

Thresholds are configuration, not code, and every one is justified:

| Metric              | Gate               | Rationale                                    |
| ------------------- | ------------------ | -------------------------------------------- |
| Citation precision  | configurable, high | a wrong citation is worse than a missing one |
| Groundedness        | configurable, high | the core product promise                     |
| Retrieval Recall@10 | configurable       | below this, synthesis cannot succeed         |
| Failure rate        | configurable, low  | reliability regression detector              |

The gate runs on `main` and on release candidates. A regression fails the build.
Thresholds start at whatever the first measured baseline supports and are
ratcheted upward deliberately - never set aspirationally.

## 6. Reporting surface

`/evaluations` in the app renders only measured values: the latest benchmark
run, per-metric history across commits, threshold status, and per-case detail.
When no benchmark has run, the page says so rather than rendering zeros.

## 7. Current status

**The suite is built (Phase 18) and no evaluation has been executed.** Running
it needs model credentials and spends real money on every case, because every
case is a real research run - a benchmark that exercised a special evaluation
path would measure that path.

The first baseline is recorded here when it exists, together with its dataset
version, git SHA and model configuration. Until then every threshold in §5 is
ungated: a gate written before a measurement is an aspiration presented as a
requirement.

```bash
make evaluate                       # every dataset in data/eval/cases
make evaluate ARGS="--mode quick"   # a cheaper smoke run
```

What is implemented, and where:

| Piece                               | Module                               |
| ----------------------------------- | ------------------------------------ |
| Case and dataset schema             | `app/evaluations/dataset.py`         |
| Structural scorers                  | `app/evaluations/metrics.py`         |
| Retrieval metrics                   | `app/retrieval/metrics.py` (Phase 8) |
| Thresholds and the gate             | `app/evaluations/thresholds.py`      |
| Runner                              | `app/evaluations/runner.py`          |
| Printed report                      | `app/evaluations/report.py`          |
| Results as rows, and `/evaluations` | `app/db/repositories/evaluations.py` |

The judged metrics in §3.2 - correctness and faithfulness - are the half that
needs a model and are not implemented: the structural half (does the citation
chain resolve, does every claim have a span) is measured, and a structural
failure is a defect rather than a model-quality signal. Adding the judge means
fixing a model and a prompt version and recording both with every result, and
it is worth doing against a real baseline rather than against none.
