# infra/

| Directory | Contents | Phase |
|---|---|---|
| `docker/` | Dockerfiles and container bootstrap for web, api, worker | 23 |
| `monitoring/` | Prometheus scrape config, Grafana provisioning and dashboards | 17 |
| `terraform/` | AWS production infrastructure ([ADR 0008](../docs/ADRs/0008-aws-ecs-deployment.md)) | 23 |
| `kubernetes/` | Manifest set kept as the documented portability escape hatch | 23 |

Local development uses the root [docker-compose.yml](../docker-compose.yml).
