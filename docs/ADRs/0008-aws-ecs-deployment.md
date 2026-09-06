# ADR 0008: AWS ECS Fargate as the production deployment target

- **Status:** Accepted
- **Date:** 2026-09-05

## Context

Production runs two long-lived process types (API, worker) plus managed
Postgres with pgvector, managed Redis, object storage and metrics - operated by
a small team that should be writing research code, not maintaining a control
plane.

## Decision

**AWS ECS on Fargate**, provisioned with Terraform: VPC with public and private
subnets, ALB, two ECS services from one image, RDS PostgreSQL with pgvector
enabled, ElastiCache Redis, S3, CloudWatch logs, least-privilege IAM task roles.
Worker count scales on queue depth, API count on request concurrency.

## Consequences

- No node pools, no cluster upgrades, no CNI debugging. Terraform modules stay
  small enough to read.
- Some AWS coupling, mitigated because the application depends only on portable
  interfaces: `ObjectStorage` (S3 API), Postgres and Redis. A Kubernetes
  manifest set under `infra/kubernetes/` is the documented escape hatch.
- Fargate has higher per-task cost and cold-start latency than EC2. Acceptable
  for long-lived services; revisit if worker churn dominates cost.
