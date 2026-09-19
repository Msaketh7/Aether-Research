output "primary_endpoint" {
  description = "Hostname of the primary node."
  value       = aws_elasticache_replication_group.this.primary_endpoint_address
}

output "port" {
  description = "Listening port."
  value       = aws_elasticache_replication_group.this.port
}

output "replication_group_id" {
  description = "Used by alarms and by the runbook."
  value       = aws_elasticache_replication_group.this.replication_group_id
}

# The URL the application reads as REDIS_URL.
#
# `rediss://`, with two s: transit encryption is on, and redis-py selects TLS
# from the scheme. A `redis://` URL against this cluster connects and then
# fails on the first command with a protocol error that mentions nothing about
# TLS, which is a bad half-hour to hand somebody.
#
# The empty username before the colon is not a typo - it is how an AUTH-token
# cluster is addressed in a URL, where the token is the password and there is
# no user.
output "redis_url" {
  description = "Full Redis URL including the auth token, for storage in Secrets Manager."
  value       = "rediss://:${urlencode(random_password.auth_token.result)}@${aws_elasticache_replication_group.this.primary_endpoint_address}:${aws_elasticache_replication_group.this.port}/0"
  sensitive   = true
}
