# @aether/evaluation

The benchmark runner: executes the `data/eval/` dataset against a build,
computes retrieval, generation, agent and system metrics, and gates CI on
thresholds. Not yet implemented - built in Phase 18.

Methodology, metric definitions and the reporting rules are in
[docs/evaluation.md](../../docs/evaluation.md). The governing rule: no metric is
ever written down unless the suite produced it.
