# infra/

| Directory     | Contents                                                                            | Phase |
| ------------- | ----------------------------------------------------------------------------------- | ----- |
| `docker/`     | Dockerfiles for the two images: api (api + worker) and web                          | 23    |
| `monitoring/` | Prometheus scrape config, Grafana provisioning and dashboards                       | 17    |
| `terraform/`  | AWS production infrastructure ([ADR 0008](../docs/ADRs/0008-aws-ecs-deployment.md)) | 23    |
| `kubernetes/` | Manifest set kept as the documented portability escape hatch                        | 23    |

Local development uses the root [docker-compose.yml](../docker-compose.yml):
`make up` for the backing services alone, `make up-app` for those plus web, api
and worker built from `docker/`.

## Two images, three process types

`docker/api.Dockerfile` builds one image that the API, the worker and the
migration step all run — one codebase, two long-lived process types, per
[ADR 0001](../docs/ADRs/0001-modular-monolith.md). They differ only in the
command. `docker/web.Dockerfile` builds the frontend.

Both build from the **repository root**, for reasons that are not
interchangeable: the web build needs the npm workspace, because
`@aether/shared-types` is consumed as TypeScript source rather than as a
published package; and the API image must keep the `apps/api` path depth,
because `app/core/config.py` resolves the repository root by walking four
directories up from itself. `.dockerignore` is what keeps that context from
meaning 77,000 files and a `.env`.

## What has and has not been run

The Terraform is validated (`terraform validate`, and `terraform fmt`) and has
**never been applied**. The images build in CI and have **never been built on
the development machine**, which has no Docker. The Kubernetes manifests have
**never been applied to a cluster**.

What holds these to the code they deploy is
[`apps/api/tests/test_infrastructure.py`](../apps/api/tests/test_infrastructure.py):
it asserts the facts each file states about the application — the settings it
sets exist, the probe paths are routes, the worker's stop timeout outlasts its
shutdown grace, the load balancer outlasts a stream — and fails the build when
one of them stops being true.
