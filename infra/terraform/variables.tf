# Everything an environment chooses.
#
# The rule applied here is the same one `app/core/config.py` follows: a value
# that differs between environments is a declared variable with a type and a
# comment, and a value that does not is a literal in the module that owns it.
# Fifty knobs nobody sets are worse than ten that are understood.

# --- identity ----------------------------------------------------------------

variable "project" {
  description = "Name prefix for every resource. Appears in ARNs, log groups and tags."
  type        = string
  default     = "aether"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,20}$", var.project))
    error_message = "project must be lowercase alphanumeric with hyphens, 2-21 characters."
  }
}

variable "environment" {
  description = "Deployment environment. Selects sizing defaults and is part of every name."
  type        = string

  validation {
    condition     = contains(["staging", "production"], var.environment)
    error_message = "environment must be staging or production."
  }
}

variable "aws_region" {
  description = "AWS region for every resource in this root."
  type        = string
  default     = "us-east-1"
}

# --- network -----------------------------------------------------------------

variable "vpc_cidr" {
  description = "CIDR block for the VPC. Must be large enough for two subnets per AZ."
  type        = string
  default     = "10.0.0.0/16"
}

variable "availability_zone_count" {
  description = "How many AZs to spread subnets across. Two is the minimum an ALB accepts."
  type        = number
  default     = 2

  validation {
    condition     = var.availability_zone_count >= 2 && var.availability_zone_count <= 4
    error_message = "availability_zone_count must be between 2 and 4."
  }
}

variable "single_nat_gateway" {
  description = <<-EOT
    Route every private subnet through one NAT gateway instead of one per AZ.

    A NAT gateway is roughly $32/month plus data processing, so this is the
    single largest fixed cost in the network. One gateway is the right answer
    for staging and the wrong one for production: it makes a whole AZ's egress
    depend on another AZ being up.
  EOT
  type        = bool
  default     = true
}

variable "flow_logs_enabled" {
  description = "Send VPC flow logs to CloudWatch. Evidence for the threat model's egress claims."
  type        = bool
  default     = true
}

variable "log_retention_days" {
  description = "Retention for every CloudWatch log group this root creates."
  type        = number
  default     = 30
}

# --- load balancer -----------------------------------------------------------

variable "certificate_arn" {
  description = <<-EOT
    ACM certificate for the HTTPS listener.

    Empty means HTTP only, which is what a first apply into a fresh account
    looks like before DNS exists. The session cookie carries `Secure` outside
    local and test, so a browser will not send it over the HTTP listener -
    an HTTP-only deployment can be reached but not signed into, and that is
    the intended pressure to finish the certificate.
  EOT
  type        = string
  default     = ""
}

variable "api_health_check_path" {
  description = <<-EOT
    Target group health check for the API.

    Liveness (/health), not readiness (/ready), and the reason is a failure
    mode rather than a preference: /ready asks Postgres, Redis and S3, so one
    dependency blip would fail the check on every replica at once and the load
    balancer would drain the entire service. A replica that cannot reach Redis
    still serves the endpoints that do not need it, and returns a typed 503 for
    the ones that do. /ready is what the post-deploy smoke test asks, once,
    where a failure means "do not promote this" rather than "remove all
    capacity".
  EOT
  type        = string
  default     = "/health"
}

variable "allowed_ingress_cidrs" {
  description = "Who may reach the load balancer. Narrow this for a private staging environment."
  type        = list(string)
  default     = ["0.0.0.0/0"]
}

# --- images ------------------------------------------------------------------

variable "api_image" {
  description = "Fully qualified image for the api and worker services (ADR 0001: one image)."
  type        = string
}

variable "web_image" {
  description = "Fully qualified image for the web service."
  type        = string
}

# --- service sizing ----------------------------------------------------------

variable "api_service" {
  description = "Fargate sizing and scaling bounds for the API service."
  type = object({
    cpu           = number
    memory        = number
    desired_count = number
    min_count     = number
    max_count     = number
  })
  default = {
    cpu           = 512
    memory        = 1024
    desired_count = 2
    min_count     = 2
    max_count     = 10
  }
}

variable "web_service" {
  description = "Fargate sizing and scaling bounds for the web service."
  type = object({
    cpu           = number
    memory        = number
    desired_count = number
    min_count     = number
    max_count     = number
  })
  default = {
    cpu           = 256
    memory        = 512
    desired_count = 2
    min_count     = 2
    max_count     = 6
  }
}

variable "worker_service" {
  description = <<-EOT
    Fargate sizing and scaling bounds for the research worker.

    Memory is the binding constraint, not CPU: document parsing forks a child
    with its own address space and PARSE_MAX_MEMORY_BYTES defaults to 2 GiB, so
    a task smaller than that can be killed by the kernel mid-run rather than by
    the parser's own limit.
  EOT
  type = object({
    cpu           = number
    memory        = number
    desired_count = number
    min_count     = number
    max_count     = number
  })
  default = {
    cpu           = 1024
    memory        = 4096
    desired_count = 2
    min_count     = 1
    max_count     = 20
  }
}

variable "worker_concurrency" {
  description = <<-EOT
    Runs a single worker task executes at once (WORKER_CONCURRENCY).

    Left at 1, which is the shipped default and deliberate: the deployment
    model is horizontal, and one run per task is what makes a task's CPU
    reservation and the autoscaler's numbers mean something. Phase 22 measured
    what raising it buys on a single host and what else has to move with it -
    docs/load-testing.md section 9, and the note beside the setting in
    app/core/config.py.
  EOT
  type        = number
  default     = 1
}

# --- data stores -------------------------------------------------------------

variable "database" {
  description = "RDS PostgreSQL sizing and durability settings."
  type = object({
    instance_class          = string
    allocated_storage       = number
    max_allocated_storage   = number
    multi_az                = bool
    backup_retention_days   = number
    deletion_protection     = bool
    performance_insights    = bool
    skip_final_snapshot     = bool
    apply_immediately       = bool
    monitoring_interval_sec = number
  })
  default = {
    instance_class          = "db.t4g.medium"
    allocated_storage       = 50
    max_allocated_storage   = 200
    multi_az                = false
    backup_retention_days   = 7
    deletion_protection     = true
    performance_insights    = true
    skip_final_snapshot     = false
    apply_immediately       = false
    monitoring_interval_sec = 60
  }
}

variable "cache" {
  description = "ElastiCache Redis sizing and durability settings."
  type = object({
    node_type                = string
    replica_count            = number
    automatic_failover       = bool
    multi_az                 = bool
    snapshot_retention_limit = number
  })
  default = {
    node_type                = "cache.t4g.micro"
    replica_count            = 1
    automatic_failover       = true
    multi_az                 = false
    snapshot_retention_limit = 1
  }
}

# --- application settings carried into the task definitions ------------------

variable "app_environment" {
  description = <<-EOT
    Extra plain (non-secret) settings for the api and worker tasks.

    Merged over the block this root computes, so an environment can raise a
    ceiling without a code change. Every key must be a variable declared in
    app/core/config.py - one that is not is silently ignored by pydantic's
    `extra="ignore"`, which is exactly the kind of quiet nothing that
    apps/api/tests/test_infrastructure.py exists to catch.
  EOT
  type        = map(string)
  default     = {}
}

variable "provider_secret_names" {
  description = <<-EOT
    Settings whose values live in Secrets Manager rather than in this state.

    Terraform creates the secret and the task's permission to read it; the
    value is written out of band, because a model provider key in a plan output
    is a model provider key in a CI log.
  EOT
  type        = list(string)
  default = [
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "TAVILY_API_KEY",
    "BRAVE_API_KEY",
    "GITHUB_TOKEN",
  ]
}

variable "cors_allow_origins" {
  description = <<-EOT
    Origins the API accepts credentialed requests from.

    Usually empty: the browser talks to the API through the same load balancer
    under /api, so the request is same-origin and CORS never applies. Set this
    only when the frontend is served from somewhere else.
  EOT
  type        = list(string)
  default     = []
}

variable "alarm_topic_arn" {
  description = "SNS topic for CloudWatch alarms. Empty creates the alarms without an action."
  type        = string
  default     = ""
}
