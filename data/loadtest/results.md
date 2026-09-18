# Load test results

## Conditions

- **measured** - 2026-09-18T21:15:45+00:00
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
| offered-10 | 10 | 4 | 26.8 | 10 | 100.0% | 22.38 |
| offered-25 | 25 | 4 | 44.6 | 25 | 100.0% | 33.67 |
| offered-50 | 50 | 4 | 80.5 | 50 | 100.0% | 37.25 |
| offered-100 | 100 | 4 | 152.9 | 100 | 100.0% | 39.25 |

## Levels under load

Instantaneous readings at a fixed interval, so a peak shorter than the interval is a peak this did not see.

| profile | peak queue depth | peak running | slots saturated | peak db in use | peak db overflow | peak llm in flight | samples |
| --- | --- | --- | --- | --- | --- | --- | --- |
| offered-10 | 10.0 | 4.0 | 77% | 5.0 | 0.0 | 4.0 | 102 |
| offered-25 | 25.0 | 4.0 | 88% | 5.0 | 0.0 | 4.0 | 173 |
| offered-50 | 665.0 | 4.0 | 99% | 5.0 | 0.0 | 4.0 | 312 |
| offered-100 | 4662.0 | 4.0 | 100% | 5.0 | 0.0 | 4.0 | 593 |

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
| total (submit to finish) | 10 | 20.89 | 26.29 | 26.53 | 26.59 |
| queue wait | 10 | 14.55 | 20.91 | 21.15 | 21.21 |
| execution | 10 | 6.60 | 14.17 | 14.20 | 14.20 |

### offered-25

| stage | n | P50 (s) | P95 (s) | P99 (s) | max (s) |
| --- | --- | --- | --- | --- | --- |
| total (submit to finish) | 25 | 25.32 | 39.51 | 43.29 | 44.49 |
| queue wait | 25 | 19.15 | 32.81 | 37.73 | 39.29 |
| execution | 25 | 6.18 | 7.74 | 7.87 | 7.90 |

### offered-50

| stage | n | P50 (s) | P95 (s) | P99 (s) | max (s) |
| --- | --- | --- | --- | --- | --- |
| total (submit to finish) | 50 | 44.42 | 75.90 | 80.25 | 80.27 |
| queue wait | 50 | 38.53 | 69.85 | 73.67 | 73.78 |
| execution | 50 | 6.14 | 7.09 | 7.16 | 7.19 |

### offered-100

| stage | n | P50 (s) | P95 (s) | P99 (s) | max (s) |
| --- | --- | --- | --- | --- | --- |
| total (submit to finish) | 100 | 81.70 | 146.30 | 152.65 | 152.67 |
| queue wait | 100 | 75.84 | 140.63 | 146.24 | 146.25 |
| execution | 100 | 5.81 | 6.78 | 7.35 | 7.40 |

## Not measured

- **embedding_and_dense_retrieval** - No pgvector locally: ingestion stores chunks with vectors pending and retrieval runs its lexical arm only.
- **provider_behaviour** - The model is scripted at a declared latency, so nothing here measures a real provider's throttling or tail latency.
- **redis_utilisation** - No Redis on this machine. `APP_ENV=test` selects the in-memory queue adapter, so queue depth is measured and Redis is not.
