# Production.
#
# Every difference from staging is a durability or availability decision:
# Multi-AZ on both data stores, a NAT gateway per AZ, deletion protection on,
# a final snapshot, and floors that keep two replicas of everything that
# serves traffic.

environment = "production"
aws_region  = "us-east-1"

single_nat_gateway      = false
availability_zone_count = 3

api_image = "REPLACE_ME.dkr.ecr.us-east-1.amazonaws.com/aether-api:REPLACE_DIGEST"
web_image = "REPLACE_ME.dkr.ecr.us-east-1.amazonaws.com/aether-web:REPLACE_DIGEST"

# SEC's access terms require a real contact address and block anonymous
# scrapers; the application's default carries example.com, which the variable's
# own validation refuses. The same agent is sent on ordinary fetches too.
sec_user_agent = "AetherResearch/0.1 (REPLACE_ME)"

# Required here. Without it the HTTPS listener does not exist and nobody can
# sign in, because the session cookie carries Secure.
certificate_arn = "REPLACE_ME"

api_service = {
  cpu           = 1024
  memory        = 2048
  desired_count = 2
  min_count     = 2
  max_count     = 12
}

web_service = {
  cpu           = 512
  memory        = 1024
  desired_count = 2
  min_count     = 2
  max_count     = 8
}

# min_count 1 rather than 0: a cold worker service means the first research
# request of the day waits for a Fargate task to start, image pull included.
worker_service = {
  cpu           = 2048
  memory        = 8192
  desired_count = 2
  min_count     = 1
  max_count     = 20
}

database = {
  instance_class          = "db.r7g.large"
  allocated_storage       = 100
  max_allocated_storage   = 1000
  multi_az                = true
  backup_retention_days   = 30
  deletion_protection     = true
  performance_insights    = true
  skip_final_snapshot     = false
  apply_immediately       = false
  monitoring_interval_sec = 60
}

cache = {
  node_type                = "cache.t4g.small"
  replica_count            = 1
  automatic_failover       = true
  multi_az                 = true
  snapshot_retention_limit = 7
}

log_retention_days = 90

# Set this to the SNS topic that pages someone. Without it the alarms still
# exist and still record their state; they just do not tell anybody.
alarm_topic_arn = ""
