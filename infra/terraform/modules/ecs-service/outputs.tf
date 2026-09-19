output "service_name" {
  description = "The ECS service name, which the deploy workflow updates."
  value       = aws_ecs_service.this.name
}

output "task_definition_family" {
  description = "Family the deploy workflow registers a new revision against."
  value       = aws_ecs_task_definition.this.family
}

output "task_definition_arn" {
  description = "The revision Terraform created. The running one may be newer; see the lifecycle block."
  value       = aws_ecs_task_definition.this.arn
}

output "task_role_arn" {
  description = "What this service's process may do."
  value       = aws_iam_role.task.arn
}

output "log_group_name" {
  description = "Where this service's logs go."
  value       = aws_cloudwatch_log_group.this.name
}
