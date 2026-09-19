output "alb_security_group_id" {
  value       = aws_security_group.alb.id
  description = "Attached to the load balancer."
}

output "tasks_security_group_id" {
  value       = aws_security_group.tasks.id
  description = "Attached to every task that serves HTTP."
}

output "worker_security_group_id" {
  value       = aws_security_group.worker.id
  description = "Attached to the research worker."
}

output "database_security_group_id" {
  value       = aws_security_group.database.id
  description = "Attached to the RDS instance."
}

output "cache_security_group_id" {
  value       = aws_security_group.cache.id
  description = "Attached to the ElastiCache replication group."
}
