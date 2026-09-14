# Retrieval benchmark datasets

Run one:

```bash
uv run python scripts/with_test_db.py uv run python scripts/benchmark_retrieval.py
```

from `apps/api`. It provisions a throwaway Postgres, ingests the corpus through
the real pipeline, resolves the labels against the chunks it produced, and scores
each strategy. `--chunk-sizes` sweeps the chunk size; `--embed` measures the
dense arms where a model and pgvector are available.

## `repo-docs-v1.json`

**Corpus:** this repository's own `docs/` — the PRD, the TDD, the architecture
map, the threat model and the evaluation methodology. Chosen because it is real
prose of the kind the system reads, it is substantial and structured (tables,
code fences, deep heading trees), and anyone who clones the repository can
reproduce the measurement with no network access and no licensing question.

**Labels:** 14 questions, hand-written, each with one verbatim _anchor_ — a
passage from the corpus that answers it. A chunk is relevant if it contains an
anchor. Anchors stay within a single source line, so that a Markdown list marker
or blockquote prefix on the following line cannot break the match, and they are
compared with whitespace collapsed and case folded.

An anchor that no longer appears makes its case **unmeasurable** and is reported
by name. That is a broken label, not a retrieval regression, and the two must
not look alike.

## What this dataset is and is not

It is enough to rank strategies against each other, to catch a regression, and
to compare chunk sizes on one corpus. It is **not** a statement about absolute
retrieval quality on the open web: 14 cases, one corpus, one author's judgement
about which passage answers which question.

Two properties of the labels shape how the numbers read:

- Most cases have exactly one relevant chunk, so **precision@10 is capped near
  0.1** by construction. A low precision figure here is arithmetic, not a
  finding.
- Recall@10 is therefore close to "what fraction of questions were answered at
  all", and MRR and nDCG carry most of the information about ranking quality.

## Measured baseline

Produced by running the command above on 2026-09-11, commit of Phase 8, on the
14 cases. Lexical arm only: no embedding model has been run against a real
corpus in this repository, and pgvector is not installed on the machine this was
measured on, so the dense, hybrid and hybrid+rerank rows were **not measured**.

| chunk / overlap | chunks | recall@10 | precision@10 |   MRR | nDCG@10 |
| --------------- | -----: | --------: | -----------: | ----: | ------: |
| 256 / 32        |    247 |     0.714 |        0.071 | 0.331 |   0.423 |
| 512 / 64        |    195 |     0.714 |        0.071 | 0.342 |   0.427 |
| 1024 / 128      |    189 |     0.714 |        0.071 | 0.298 |   0.395 |

Recall is identical across sizes: the same ten questions are answered and the
same four are missed. 512/64 ranks best on both order-sensitive metrics, which
is why it stays the default.

The four the lexical arm never finds — `tool-permissions`, `isolated-parsing`,
`report-structure`, `verbatim-spans` — are all vocabulary mismatches, where the
question and the passage that answers it share few words. They are exactly the
cases the dense arm exists for, which is the most useful thing this baseline
says: it shows what hybrid retrieval has to buy, without yet being able to
price it.
