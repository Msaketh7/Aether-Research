output "cluster_id" {
  value       = aws_ecs_cluster.this.id
  description = "Cluster ARN, which is what a service references."
}

output "cluster_name" {
  value       = aws_ecs_cluster.this.name
  description = "Cluster name, used by alarms and the deploy workflow."
}

output "execution_role_arn" {
  value       = aws_iam_role.execution.arn
  description = "Shared by every task definition."
}
