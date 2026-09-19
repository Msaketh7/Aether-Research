# Staging: the same shape as production, at the smallest size that still
# exercises every path. Anything cheaper would stop being a rehearsal.

environment = "staging"
aws_region  = "us-east-1"

# One NAT gateway. It is the single largest fixed cost in the network and an
# AZ-level dependency; in staging that trade is obviously worth taking.
single_nat_gateway      = true
availability_zone_count = 2

# Replace both with the digests the build workflow pushed. A tag is accepted
# and a digest is better: a tag can be moved after it is deployed.
api_image = "REPLACE_ME.dkr.ecr.us-east-1.amazonaws.com/aether-api:staging"
web_image = "REPLACE_ME.dkr.ecr.us-east-1.amazonaws.com/aether-web:staging"

# No certificate yet. The deployment is reachable over HTTP and cannot be
# signed into, because the session cookie carries Secure outside local and
# test - see the note on this variable in variables.tf.
certificate_arn = ""

api_service = {
  cpu           = 512
  memory        = 1024
  desired_count = 1
  min_count     = 1
  max_count     = 4
}

web_service = {
  cpu           = 256
  memory        = 512
  desired_count = 1
  min_count     = 1
  max_count     = 2
}

# Still 4 GiB: PARSE_MAX_MEMORY_BYTES is 2 GiB and the parser forks a child
# with its own address space, so a smaller task is killed by the kernel rather
# than by the parser's own limit - and staging exists to find that before
# production does.
worker_service = {
  cpu           = 1024
  memory        = 4096
  desired_count = 1
  min_count     = 0
  max_count     = 4
}

database = {
  instance_class          = "db.t4g.micro"
  allocated_storage       = 20
  max_allocated_storage   = 100
  multi_az                = false
  backup_retention_days   = 1
  deletion_protection     = false
  performance_insights    = false
  skip_final_snapshot     = true
  apply_immediately       = true
  monitoring_interval_sec = 0
}

cache = {
  node_type                = "cache.t4g.micro"
  replica_count            = 0
  automatic_failover       = false
  multi_az                 = false
  snapshot_retention_limit = 0
}

log_retention_days = 7

# Lower ceilings than production: staging runs against real providers, and a
# forgotten load test should cost pennies rather than a bill.
app_environment = {
  MAX_ESTIMATED_COST_USD = "0.50"
  MAX_SOURCES            = "20"
  REGISTRATION_ENABLED   = "true"
}
