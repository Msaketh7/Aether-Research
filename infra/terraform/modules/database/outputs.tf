output "endpoint" {
  description = "host:port for the instance."
  value       = aws_db_instance.this.endpoint
}

output "address" {
  description = "Hostname, without the port."
  value       = aws_db_instance.this.address
}

output "port" {
  description = "Listening port."
  value       = aws_db_instance.this.port
}

output "database_name" {
  description = "The initial database."
  value       = aws_db_instance.this.db_name
}

output "identifier" {
  description = "The RDS identifier, used by alarms and by the restore runbook."
  value       = aws_db_instance.this.identifier
}

# The DSN the application reads as DATABASE_URL. Assembled here because this is
# the module that knows every part of it, and marked sensitive so that it
# cannot appear in a plan, an output listing or a CI log. The `+asyncpg`
# dialect suffix is not decoration - SQLAlchemy selects its driver from it, and
# a bare postgresql:// URL would try to load psycopg2 and fail at startup.
output "database_url" {
  description = "Full SQLAlchemy DSN, for storage in Secrets Manager."
  value       = "postgresql+asyncpg://${var.master_username}:${urlencode(random_password.master.result)}@${aws_db_instance.this.endpoint}/${aws_db_instance.this.db_name}"
  sensitive   = true
}
