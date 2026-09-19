# infra/kubernetes

The portability escape hatch named in
[ADR 0008](../../docs/ADRs/0008-aws-ecs-deployment.md).

ECS Fargate is the deployment target, and the reason that is an acceptable
amount of AWS coupling is that the application depends only on portable
interfaces: `ObjectStorage` over the S3 API, Postgres, Redis. This directory is
the demonstration of that claim rather than a second supported path — the same
two images, the same environment variables, the same two process types, run by
a different scheduler.

## What is here

| File               | Contents                                                         |
| ------------------ | ---------------------------------------------------------------- |
| `namespace.yaml`   | The namespace and a default-deny network policy                  |
| `config.yaml`      | The non-secret settings, and the secret's shape                  |
| `migrate-job.yaml` | `alembic upgrade heads`, as the pre-deploy step                  |
| `api.yaml`         | Deployment, Service, HPA, PodDisruptionBudget                    |
| `web.yaml`         | Deployment, Service, HPA                                         |
| `worker.yaml`      | Deployment and HPA. No Service — nothing calls a worker          |
| `ingress.yaml`     | The one path rule: `/api` to the API, everything else to the web |

## What is not here

Postgres, Redis and object storage. Running a database in the cluster is a
different decision with different failure modes, and this directory exists to
show that the _application_ is portable, not to argue that its data stores
should move. Point `DATABASE_URL`, `REDIS_URL` and the S3 settings at whatever
the target platform provides.

## Status

**Never applied.** These manifests have not been run against a cluster —
`apps/api/tests/test_infrastructure.py` parses them and checks the invariants
that would otherwise drift from the compose file and the Terraform (image
names, the two process types, the settings each carries, non-root execution),
and that is the whole of the verification. Treat them as a reviewed starting
point, not as a tested deployment.

```bash
kubectl apply -f infra/kubernetes/namespace.yaml
kubectl apply -f infra/kubernetes/           # once the secret exists
```
