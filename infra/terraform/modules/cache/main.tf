# ElastiCache Redis.
#
# Redis is load-bearing here in two different ways, and they have different
# durability requirements. It is the job queue (app/workers/queue.py), where a
# lost message is a run that waits for the reconciliation sweep rather than a
# run that is lost - the database is the record, not Redis. And it is the
# response cache (app/cache/) and the event bus (app/research/eventbus.py),
# where a cold start costs money and latency but nothing else. Nothing that
# only exists in Redis is irreplaceable, which is why this is a small cluster
# with a snapshot rather than a large one with Multi-AZ everywhere.

resource "aws_elasticache_subnet_group" "this" {
  name        = "${var.name_prefix}-cache"
  description = "Private subnets only."
  subnet_ids  = var.subnet_ids
}

resource "aws_elasticache_parameter_group" "this" {
  name        = "${var.name_prefix}-redis7"
  family      = "redis7"
  description = "Aether Research: eviction policy."

  # The queue and the cache share one Redis, so the eviction policy has to be
  # one that never evicts a key with no TTL. `allkeys-lru` would drop queued
  # job ids under memory pressure; `volatile-lru` drops only what was written
  # with an expiry, which is exactly the cache entries and the event buffer.
  parameter {
    name  = "maxmemory-policy"
    value = "volatile-lru"
  }

  lifecycle {
    create_before_destroy = true
  }
}

# The auth token, on the same terms as the database password: generated here,
# held in the state, and handed to the application as part of a DSN in Secrets
# Manager. ElastiCache restricts the character set, and the token is URL-
# encoded into the DSN below.
resource "random_password" "auth_token" {
  length  = 64
  special = false
}

resource "aws_elasticache_replication_group" "this" {
  replication_group_id = "${var.name_prefix}-redis"
  description          = "Aether Research queue, cache and event bus."

  engine         = "redis"
  engine_version = var.engine_version
  node_type      = var.node_type
  port           = 6379

  # One primary plus `replica_count` replicas. Automatic failover needs at
  # least one replica, so the two settings are checked against each other
  # rather than trusted.
  num_cache_clusters         = var.replica_count + 1
  automatic_failover_enabled = var.automatic_failover && var.replica_count > 0
  multi_az_enabled           = var.multi_az && var.replica_count > 0

  subnet_group_name    = aws_elasticache_subnet_group.this.name
  security_group_ids   = [var.security_group_id]
  parameter_group_name = aws_elasticache_parameter_group.this.name

  at_rest_encryption_enabled = true
  transit_encryption_enabled = true
  auth_token                 = random_password.auth_token.result

  snapshot_retention_limit = var.snapshot_retention_limit
  snapshot_window          = "03:00-04:00"
  maintenance_window       = "sun:06:30-sun:07:30"

  auto_minor_version_upgrade = true
  apply_immediately          = var.apply_immediately

  log_delivery_configuration {
    destination      = var.log_group_name
    destination_type = "cloudwatch-logs"
    log_format       = "json"
    log_type         = "engine-log"
  }

  tags = { Name = "${var.name_prefix}-redis" }
}
