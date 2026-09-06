# data/

| Directory   | Contents                                                                       |
| ----------- | ------------------------------------------------------------------------------ |
| `seed/`     | Deterministic demo data used to populate a fresh local database                |
| `fixtures/` | Small, stable inputs for tests (documents, HTML samples, API payloads)         |
| `eval/`     | Versioned evaluation dataset - see [docs/evaluation.md](../docs/evaluation.md) |

Nothing here is production data, and nothing here contains secrets. Fixtures are
committed so that tests are reproducible without network access.
