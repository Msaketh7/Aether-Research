# Load test results

## Conditions

- **measured** - 2026-09-18T22:11:39+00:00
- **machine** - Intel64 Family 6 Model 186 Stepping 2, GenuineIntel, Windows
- **python** - 3.13.5
- **what is real** - Postgres, the job queue, the lease, the LangGraph graph, all nine agents, the toolbelt with its SSRF guard, ingestion, retrieval, the evidence projection and report assembly.
- **what is scripted** - The model provider (behind the real gateway, answering at 0.5s per call) and the socket (behind the real guarded client).
- **queue** - In-memory adapter (`APP_ENV=test`). Redis is not installed here.
- **retrieval** - Lexical arm only - no pgvector on this machine.
- **caveat** - These are this machine's numbers, and its CPU runs at about a third of nominal. Read them as ratios between profiles, not as a capacity figure for a deployment.

## Throughput and completion

| profile | offered | capacity | wall (s) | completed | completion rate | runs/min |
| --- | --- | --- | --- | --- | --- | --- |
| offered-10 | 10 | 4 | 27.3 | 10 | 100.0% | 22.02 |
| offered-25 | 25 | 4 | 41.4 | 25 | 100.0% | 36.23 |
| offered-50 | 50 | 4 | 80.5 | 50 | 100.0% | 37.28 |
| offered-100 | 100 | 4 | 158.4 | 100 | 100.0% | 37.88 |

## Levels under load

Instantaneous readings at a fixed interval, so a peak shorter than the interval is a peak this did not see.

| profile | peak queue depth | peak running | slots saturated | peak db in use | peak db overflow | peak llm in flight | samples |
| --- | --- | --- | --- | --- | --- | --- | --- |
| offered-10 | 10.0 | 4.0 | 79% | 6.0 | 0.0 | 4.0 | 104 |
| offered-25 | 25.0 | 4.0 | 87% | 4.0 | 0.0 | 4.0 | 162 |
| offered-50 | 60.0 | 4.0 | 96% | 5.0 | 0.0 | 4.0 | 313 |
| offered-100 | 224.0 | 4.0 | 99% | 5.0 | 0.0 | 4.0 | 615 |

## Database round trips

Transactions Postgres committed while the profile ran, counted by Postgres. A CPU profile cannot supply this: it counts every resumption of a coroutine as a call.

| profile | commits | commits per run |
| --- | --- | --- |
| offered-10 | 877 | 87.7 |
| offered-25 | 2083 | 83.3 |
| offered-50 | 4193 | 83.9 |
| offered-100 | 8400 | 84.0 |

## Model-call throttling

The gateway's own concurrency ceiling. A call that queued here was held by this system, not by a provider.

| profile | slot limit | model calls | calls that queued | mean wait (s) | max wait (s) |
| --- | --- | --- | --- | --- | --- |
| offered-10 | 8 | 80 | 0.0% | 0.0000 | 0.000 |
| offered-25 | 8 | 200 | 0.0% | 0.0000 | 0.000 |
| offered-50 | 8 | 400 | 0.0% | 0.0000 | 0.000 |
| offered-100 | 8 | 800 | 0.0% | 0.0000 | 0.000 |

## Latency

Percentiles interpolate between order statistics. Failed runs are excluded: a run that failed quickly is not a fast run.

### offered-10

| stage | n | P50 (s) | P95 (s) | P99 (s) | max (s) |
| --- | --- | --- | --- | --- | --- |
| total (submit to finish) | 10 | 21.58 | 27.06 | 27.11 | 27.12 |
| queue wait | 10 | 16.07 | 21.51 | 21.53 | 21.54 |
| execution | 10 | 5.57 | 15.44 | 15.45 | 15.45 |

### offered-25

| stage | n | P50 (s) | P95 (s) | P99 (s) | max (s) |
| --- | --- | --- | --- | --- | --- |
| total (submit to finish) | 25 | 25.22 | 37.29 | 40.37 | 41.34 |
| queue wait | 25 | 19.05 | 31.41 | 35.07 | 36.22 |
| execution | 25 | 5.98 | 6.90 | 7.06 | 7.09 |

### offered-50

| stage | n | P50 (s) | P95 (s) | P99 (s) | max (s) |
| --- | --- | --- | --- | --- | --- |
| total (submit to finish) | 50 | 45.22 | 77.05 | 80.38 | 80.38 |
| queue wait | 50 | 39.54 | 70.83 | 74.26 | 74.38 |
| execution | 50 | 6.07 | 7.29 | 7.98 | 8.08 |

### offered-100

| stage | n | P50 (s) | P95 (s) | P99 (s) | max (s) |
| --- | --- | --- | --- | --- | --- |
| total (submit to finish) | 100 | 85.50 | 152.05 | 158.35 | 158.38 |
| queue wait | 100 | 79.87 | 145.64 | 151.99 | 152.00 |
| execution | 100 | 6.14 | 6.92 | 7.15 | 7.16 |

## Where the node time went

From the `agent_runs` rows each profile wrote, not from a sampler. Ranked by total, because a cheap node that runs eight times costs more than an expensive one that runs once.

### offered-10

| agent | executions | total (s) | share | mean (s) | P95 (s) |
| --- | --- | --- | --- | --- | --- |
| researcher | 10 | 45.7 | 57.6% | 4.568 | 9.443 |
| evidence_extractor | 10 | 5.9 | 7.4% | 0.588 | 0.690 |
| planner | 10 | 5.8 | 7.4% | 0.584 | 0.624 |
| critic | 20 | 5.7 | 7.1% | 0.283 | 0.607 |
| verifier | 10 | 5.4 | 6.8% | 0.539 | 0.589 |
| synthesizer | 10 | 5.4 | 6.8% | 0.536 | 0.564 |
| claim_normalizer | 10 | 5.3 | 6.7% | 0.528 | 0.546 |
| citation_validator | 10 | 0.2 | 0.2% | 0.018 | 0.033 |

### offered-25

| agent | executions | total (s) | share | mean (s) | P95 (s) |
| --- | --- | --- | --- | --- | --- |
| researcher | 25 | 37.0 | 30.7% | 1.481 | 1.872 |
| evidence_extractor | 25 | 14.6 | 12.1% | 0.583 | 0.637 |
| critic | 50 | 13.9 | 11.5% | 0.278 | 0.550 |
| planner | 25 | 13.9 | 11.5% | 0.557 | 0.634 |
| synthesizer | 25 | 13.6 | 11.3% | 0.545 | 0.586 |
| verifier | 25 | 13.5 | 11.2% | 0.539 | 0.550 |
| claim_normalizer | 25 | 13.5 | 11.2% | 0.538 | 0.551 |
| citation_validator | 25 | 0.5 | 0.5% | 0.022 | 0.037 |

### offered-50

| agent | executions | total (s) | share | mean (s) | P95 (s) |
| --- | --- | --- | --- | --- | --- |
| researcher | 50 | 73.8 | 30.4% | 1.475 | 1.803 |
| evidence_extractor | 50 | 29.6 | 12.2% | 0.593 | 0.697 |
| critic | 100 | 28.1 | 11.6% | 0.281 | 0.558 |
| planner | 50 | 28.1 | 11.6% | 0.562 | 0.658 |
| synthesizer | 50 | 27.4 | 11.3% | 0.549 | 0.581 |
| claim_normalizer | 50 | 27.4 | 11.3% | 0.548 | 0.598 |
| verifier | 50 | 27.2 | 11.2% | 0.545 | 0.570 |
| citation_validator | 50 | 1.3 | 0.5% | 0.026 | 0.042 |

### offered-100

| agent | executions | total (s) | share | mean (s) | P95 (s) |
| --- | --- | --- | --- | --- | --- |
| researcher | 100 | 143.8 | 29.7% | 1.438 | 1.738 |
| evidence_extractor | 100 | 59.1 | 12.2% | 0.591 | 0.648 |
| critic | 200 | 57.3 | 11.9% | 0.287 | 0.580 |
| synthesizer | 100 | 55.8 | 11.5% | 0.558 | 0.621 |
| planner | 100 | 55.6 | 11.5% | 0.556 | 0.632 |
| verifier | 100 | 54.9 | 11.3% | 0.549 | 0.588 |
| claim_normalizer | 100 | 54.8 | 11.3% | 0.548 | 0.588 |
| citation_validator | 100 | 2.4 | 0.5% | 0.024 | 0.040 |

## Not measured

- **embedding_and_dense_retrieval** - No pgvector locally: ingestion stores chunks with vectors pending and retrieval runs its lexical arm only.
- **provider_behaviour** - The model is scripted at a declared latency, so nothing here measures a real provider's throttling or tail latency.
- **redis_utilisation** - No Redis on this machine. `APP_ENV=test` selects the in-memory queue adapter, so queue depth is measured and Redis is not.
