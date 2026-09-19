# Security groups, one per role.
#
# Every rule here names another security group rather than a CIDR, so the
# statement it makes is "the API may reach the database" and not "anything in
# 10.0.128.0/20 may reach the database". The two are the same set of hosts
# today and diverge the moment somebody adds a subnet.
#
# Rules are separate resources, not inline blocks. Inline `ingress`/`egress`
# inside aws_security_group are authoritative: adding one anywhere else, by
# hand or by another module, is silently reverted on the next apply - and a
# security group that quietly loses a rule is worse than one that has too many.

# --- load balancer -----------------------------------------------------------

resource "aws_security_group" "alb" {
  name        = "${var.name_prefix}-alb"
  description = "Public entry point. The only group reachable from outside the VPC."
  vpc_id      = var.vpc_id

  tags = { Name = "${var.name_prefix}-alb" }

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_vpc_security_group_ingress_rule" "alb_http" {
  for_each = toset(var.allowed_ingress_cidrs)

  security_group_id = aws_security_group.alb.id
  description       = "HTTP, redirected to HTTPS when a certificate is configured"
  cidr_ipv4         = each.value
  from_port         = 80
  to_port           = 80
  ip_protocol       = "tcp"
}

resource "aws_vpc_security_group_ingress_rule" "alb_https" {
  for_each = toset(var.allowed_ingress_cidrs)

  security_group_id = aws_security_group.alb.id
  description       = "HTTPS"
  cidr_ipv4         = each.value
  from_port         = 443
  to_port           = 443
  ip_protocol       = "tcp"
}

resource "aws_vpc_security_group_egress_rule" "alb_to_tasks" {
  security_group_id            = aws_security_group.alb.id
  description                  = "Forward to the task groups it has target groups for"
  referenced_security_group_id = aws_security_group.tasks.id
  ip_protocol                  = "-1"
}

# --- tasks that serve HTTP (api, web) ----------------------------------------

resource "aws_security_group" "tasks" {
  name        = "${var.name_prefix}-tasks"
  description = "Fargate tasks behind the load balancer."
  vpc_id      = var.vpc_id

  tags = { Name = "${var.name_prefix}-tasks" }

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_vpc_security_group_ingress_rule" "tasks_from_alb" {
  for_each = toset([for p in var.task_ports : tostring(p)])

  security_group_id            = aws_security_group.tasks.id
  description                  = "Load balancer to container port ${each.value}"
  referenced_security_group_id = aws_security_group.alb.id
  from_port                    = tonumber(each.value)
  to_port                      = tonumber(each.value)
  ip_protocol                  = "tcp"
}

# Unrestricted egress, and deliberately so. This platform's job is to read the
# open web; an allowlist of destinations here would be a list of every domain
# any research question might ever reach, which is not a list that exists. What
# the platform may fetch is decided by the four-layer SSRF guard in
# app/sources/, at the point where the URL is known - see
# docs/threat-model.md. The VPC flow log records what was refused below this.
resource "aws_vpc_security_group_egress_rule" "tasks_all" {
  security_group_id = aws_security_group.tasks.id
  description       = "Outbound research traffic; destinations are decided by the SSRF guard"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
}

# --- the worker --------------------------------------------------------------

# Its own group with no ingress at all. The worker is reached by nothing: it
# takes work from Redis and reports through Postgres, and its Prometheus port
# is scraped from inside the VPC only. Sharing the tasks group would have given
# it the load balancer's ingress rule for no reason.
resource "aws_security_group" "worker" {
  name        = "${var.name_prefix}-worker"
  description = "Research worker tasks. No ingress; nothing calls a worker."
  vpc_id      = var.vpc_id

  tags = { Name = "${var.name_prefix}-worker" }

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_vpc_security_group_ingress_rule" "worker_metrics" {
  security_group_id = aws_security_group.worker.id
  description       = "Prometheus scrape of WORKER_METRICS_PORT, from inside the VPC only"
  cidr_ipv4         = var.vpc_cidr
  from_port         = var.worker_metrics_port
  to_port           = var.worker_metrics_port
  ip_protocol       = "tcp"
}

resource "aws_vpc_security_group_egress_rule" "worker_all" {
  security_group_id = aws_security_group.worker.id
  description       = "Outbound research traffic; destinations are decided by the SSRF guard"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
}

# --- data stores -------------------------------------------------------------

resource "aws_security_group" "database" {
  name        = "${var.name_prefix}-database"
  description = "RDS PostgreSQL. Reachable from the API and the worker, and nothing else."
  vpc_id      = var.vpc_id

  tags = { Name = "${var.name_prefix}-database" }

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_vpc_security_group_ingress_rule" "database_from_tasks" {
  security_group_id            = aws_security_group.database.id
  description                  = "API tasks"
  referenced_security_group_id = aws_security_group.tasks.id
  from_port                    = 5432
  to_port                      = 5432
  ip_protocol                  = "tcp"
}

resource "aws_vpc_security_group_ingress_rule" "database_from_worker" {
  security_group_id            = aws_security_group.database.id
  description                  = "Worker tasks"
  referenced_security_group_id = aws_security_group.worker.id
  from_port                    = 5432
  to_port                      = 5432
  ip_protocol                  = "tcp"
}

resource "aws_security_group" "cache" {
  name        = "${var.name_prefix}-cache"
  description = "ElastiCache Redis. Reachable from the API and the worker, and nothing else."
  vpc_id      = var.vpc_id

  tags = { Name = "${var.name_prefix}-cache" }

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_vpc_security_group_ingress_rule" "cache_from_tasks" {
  security_group_id            = aws_security_group.cache.id
  description                  = "API tasks"
  referenced_security_group_id = aws_security_group.tasks.id
  from_port                    = 6379
  to_port                      = 6379
  ip_protocol                  = "tcp"
}

resource "aws_vpc_security_group_ingress_rule" "cache_from_worker" {
  security_group_id            = aws_security_group.cache.id
  description                  = "Worker tasks"
  referenced_security_group_id = aws_security_group.worker.id
  from_port                    = 6379
  to_port                      = 6379
  ip_protocol                  = "tcp"
}
