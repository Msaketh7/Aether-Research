# @aether/evaluation

**This package is a pointer.** The evaluation suite is Python and lives with
the code it measures:

| What                    | Where                                                                                    |
| ----------------------- | ---------------------------------------------------------------------------------------- |
| Dataset schema, loader  | [`apps/api/app/evaluations/dataset.py`](../../apps/api/app/evaluations/dataset.py)       |
| Datasets                | [`data/eval/cases/`](../../data/eval/cases)                                              |
| Scorers                 | [`apps/api/app/evaluations/metrics.py`](../../apps/api/app/evaluations/metrics.py)       |
| Retrieval metrics       | [`apps/api/app/retrieval/metrics.py`](../../apps/api/app/retrieval/metrics.py)           |
| Thresholds and the gate | [`apps/api/app/evaluations/thresholds.py`](../../apps/api/app/evaluations/thresholds.py) |
| Runner and report       | [`apps/api/app/evaluations/`](../../apps/api/app/evaluations)                            |
| Results, as rows        | `evaluations` table; served by `GET /api/v1/evaluations`                                 |

```bash
make evaluate                       # every shipped dataset
make evaluate ARGS="--mode quick"   # a cheap smoke run
```

## Why here and not in this package

The runner executes the real research graph and scores the rows it produced.
Reimplementing that in TypeScript would mean a second definition of what a
claim, a citation and a budget are - and the second definition is always the
one that drifts. The same argument as
[`packages/prompts`](../prompts/README.md) and ADR 0015.

The workspace entry stays because the monorepo layout is fixed in Phase 0 and
because a reader looking for the evaluation suite will look here first.

## The rule this suite exists to keep

> No number appears anywhere in this repository unless it was produced by
> running the suite.

A metric that could not be measured is `null` everywhere - in the scorer, in
the aggregate, in the stored row, in the API response and in the printed
report - and is rendered as "not measured". It is never a zero, because a zero
is a measurement and would be a false one. See
[`docs/evaluation.md`](../../docs/evaluation.md).

**No benchmark has been executed.** The suite is built and tested; running it
needs model credentials and spends money, and this repository publishes no
baseline it has not measured.
