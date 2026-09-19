# infra/terraform

AWS production infrastructure, per [ADR 0008](../../docs/ADRs/0008-aws-ecs-deployment.md).

One root, one `.tfvars` per environment, one state key per environment. The
root wires modules together and creates almost nothing itself.

## Layout

| Path                    | Owns                                                             |
| ----------------------- | ---------------------------------------------------------------- |
| `versions.tf`           | Terraform and provider constraints; the partial S3 backend       |
| `variables.tf`          | Everything an environment chooses                                |
| `locals.tf`             | Names, and the settings every task carries                       |
| `main.tf`               | The wiring, in dependency order                                  |
| `monitoring.tf`         | The six alarms CloudWatch can see and Prometheus cannot          |
| `outputs.tf`            | What a person or `deploy.yml` needs after an apply               |
| `environments/*.tfvars` | Sizing and durability per environment                            |
| `modules/network`       | VPC, two subnet tiers, NAT, the S3 gateway endpoint, flow logs   |
| `modules/security`      | One security group per role, each rule naming another group      |
| `modules/database`      | RDS PostgreSQL 17, parameter group, backups, enhanced monitoring |
| `modules/cache`         | ElastiCache Redis, encrypted in transit, `volatile-lru`          |
| `modules/storage`       | The artifact bucket: versioned, encrypted, private, lifecycled   |
| `modules/alb`           | Load balancer, two target groups, the `/api/*` rule              |
| `modules/ecs-cluster`   | Cluster and the shared execution role                            |
| `modules/ecs-service`   | One Fargate service, used three times                            |
| `modules/secrets`       | Secrets Manager entries, derived and declared                    |

## The shape of a deployment

```
                    internet
                       |
              ALB (public subnets)
              /                  \
      default |                   | /api/*
              v                   v
        web service          api service ──┐
      (private subnets)   (private subnets)│
                                           │      worker service
                                           │    (private subnets, no ingress)
                                           v                │
                              RDS Postgres · ElastiCache Redis · S3
```

`/api/*` is the only path rule. Everything else is the frontend, which is why
the built web image carries a **relative** API base URL and therefore contains
no hostname: one image serves every environment, the browser's request is
same-origin, the session cookie is first-party and CORS never applies.

The probes (`/health`, `/ready`) and the Prometheus endpoint (`/metrics`) are
deliberately not routed through the load balancer. They are reachable from the
target group and from inside the VPC, and not from the internet.

## Running it

```bash
make tf-validate              # no credentials, no state, no cloud
make tf-plan  ENV=staging
make tf-apply ENV=staging
```

The state bucket and its lock table must exist before the first `init`. That is
the one bootstrap step Terraform cannot do for itself; create them once with
versioning and encryption enabled.

## Secrets

Two kinds, handled differently on purpose.

**Derived** — `DATABASE_URL` and `REDIS_URL`. This configuration generates the
password and the auth token, assembles the DSN, and writes it to Secrets
Manager. Both therefore exist in the Terraform state, which is the trade taken
knowingly: the application reads one string per datastore, so something must
assemble it, and the alternative (an AWS-managed master password Terraform
cannot read) cannot. The mitigation is the backend — encrypted, versioned,
access-controlled.

**Declared** — the model and search provider keys. Terraform creates the entry
and the task's permission to read it, writes an empty placeholder, and then
ignores the value forever. The real value is written once, out of band:

```bash
aws secretsmanager put-secret-value \
  --secret-id aether-staging/anthropic-api-key \
  --secret-string "$KEY"
```

A key that has not been set reads as absent rather than empty
(`app/core/config.py`), so that provider simply does not exist and the
deployment runs without it.

## What has not been done

**Nothing here has been applied.** No AWS account has been provisioned from
this configuration, so every claim in it is a claim about what Terraform would
create, verified by `terraform validate` and by review — not by a running
deployment. The numbers in `environments/*.tfvars` are sizing decisions, not
measurements; the only measured capacity figures this project has are in
[`docs/load-testing.md`](../../docs/load-testing.md) and they were taken on one
throttled laptop against a scripted model provider.

Two specific gaps, both marked at the code:

- The worker's autoscaling policy is CPU, not queue depth. The right metric
  (`research_queue_depth`) is a Prometheus gauge that nothing publishes to
  CloudWatch, and a target-tracking policy pointed at a metric that does not
  exist would silently never fire.
- There is no DNS or certificate automation. `certificate_arn` is an input;
  Route 53 records are not managed here.
