# What a person or a pipeline needs after an apply.
#
# Deliberately short. An output is a promise that something else consumes;
# `.github/workflows/deploy.yml` consumes the cluster and service names and the
# URL, and a human consumes the rest. Nothing sensitive is here - the DSNs are
# in Secrets Manager and marked sensitive at their source.

output "url" {
  description = "Base URL of the deployment. What the smoke test calls."
  value       = module.alb.url
}

output "alb_dns_name" {
  description = "Point the site's DNS record at this."
  value       = module.alb.dns_name
}

output "alb_zone_id" {
  description = "Hosted zone for a Route 53 alias record."
  value       = module.alb.zone_id
}

output "ecs_cluster_name" {
  description = "Cluster the deploy workflow updates services in."
  value       = module.ecs.cluster_name
}

output "service_names" {
  description = "ECS service names, by role."
  value = {
    api    = module.api_service.service_name
    web    = module.web_service.service_name
    worker = module.worker_service.service_name
  }
}

output "task_definition_families" {
  description = "Families the deploy workflow registers new revisions against."
  value = {
    api    = module.api_service.task_definition_family
    web    = module.web_service.task_definition_family
    worker = module.worker_service.task_definition_family
  }
}

# The migration step runs the API image with `alembic upgrade heads` as a
# one-off task, so it needs everything a task needs and none of it is
# discoverable from the service definitions.
output "migration_task_config" {
  description = "Everything `aws ecs run-task` needs to run the pre-deploy migration."
  value = {
    cluster           = module.ecs.cluster_name
    task_definition   = module.api_service.task_definition_family
    subnets           = module.network.private_subnet_ids
    security_groups   = [module.security.tasks_security_group_id]
    container_name    = "api"
    migration_command = ["alembic", "upgrade", "heads"]
    log_group         = module.api_service.log_group_name
  }
}

output "artifact_bucket" {
  description = "The S3 bucket holding uploads, fetched pages and reports."
  value       = module.storage.bucket
}

output "database_identifier" {
  description = "RDS identifier, for snapshots and the restore runbook."
  value       = module.database.identifier
}

output "cache_replication_group_id" {
  description = "ElastiCache replication group, for the runbook."
  value       = module.cache.replication_group_id
}

output "secret_names" {
  description = "Settings whose values live in Secrets Manager, by setting name."
  value       = keys(module.secrets.arns_by_name)
}
