# The root: one environment, wired together.
#
# Read top to bottom and it is the dependency order - network, then the things
# inside it, then the secrets that describe them, then the cluster, then the
# four services, then the role that deploys them. Nothing here creates a
# resource directly except the log group the cache writes to; every resource
# belongs to a module that owns one concern.

module "network" {
  source = "./modules/network"

  name_prefix             = local.name_prefix
  aws_region              = var.aws_region
  vpc_cidr                = var.vpc_cidr
  availability_zone_count = var.availability_zone_count
  single_nat_gateway      = var.single_nat_gateway
  flow_logs_enabled       = var.flow_logs_enabled
  log_retention_days      = var.log_retention_days
}

module "security" {
  source = "./modules/security"

  name_prefix           = local.name_prefix
  vpc_id                = module.network.vpc_id
  vpc_cidr              = module.network.vpc_cidr
  allowed_ingress_cidrs = var.allowed_ingress_cidrs
  task_ports            = [local.api_port, local.web_port]
  worker_metrics_port   = local.worker_metrics_port
  ollama_port           = local.ollama_port
}

module "storage" {
  source = "./modules/storage"

  bucket_name = local.artifact_bucket
}

module "database" {
  source = "./modules/database"

  name_prefix       = local.name_prefix
  subnet_ids        = module.network.private_subnet_ids
  security_group_id = module.security.database_security_group_id

  instance_class          = var.database.instance_class
  allocated_storage       = var.database.allocated_storage
  max_allocated_storage   = var.database.max_allocated_storage
  multi_az                = var.database.multi_az
  backup_retention_days   = var.database.backup_retention_days
  deletion_protection     = var.database.deletion_protection
  performance_insights    = var.database.performance_insights
  skip_final_snapshot     = var.database.skip_final_snapshot
  apply_immediately       = var.database.apply_immediately
  monitoring_interval_sec = var.database.monitoring_interval_sec
}

resource "aws_cloudwatch_log_group" "redis" {
  name              = "/aws/elasticache/${local.name_prefix}"
  retention_in_days = var.log_retention_days
}

module "cache" {
  source = "./modules/cache"

  name_prefix       = local.name_prefix
  subnet_ids        = module.network.private_subnet_ids
  security_group_id = module.security.cache_security_group_id
  log_group_name    = aws_cloudwatch_log_group.redis.name

  node_type                = var.cache.node_type
  replica_count            = var.cache.replica_count
  automatic_failover       = var.cache.automatic_failover
  multi_az                 = var.cache.multi_az
  snapshot_retention_limit = var.cache.snapshot_retention_limit
}

module "secrets" {
  source = "./modules/secrets"

  name_prefix    = local.name_prefix
  derived_names  = local.derived_secret_names
  derived_values = local.derived_secret_values
  declared       = distinct(concat(local.required_secret_names, var.provider_secret_names))
}

module "ecs" {
  source = "./modules/ecs-cluster"

  name_prefix = local.name_prefix
  secret_arns = module.secrets.all_arns
}

module "discovery" {
  source = "./modules/service-discovery"

  name_prefix = local.name_prefix
  vpc_id      = module.network.vpc_id
  services    = ["ollama"]
}

# --- services ----------------------------------------------------------------

# The API. Scales on requests per target rather than CPU: it spends most of its
# time waiting on Postgres, so CPU stays low while latency climbs, and a CPU
# policy would add capacity only after the problem was visible to users. The
# CPU policy is still registered as the backstop - target tracking takes the
# larger of the two recommendations.
module "api_service" {
  source = "./modules/ecs-service"

  name_prefix        = local.name_prefix
  service_name       = "api"
  aws_region         = var.aws_region
  cluster_id         = module.ecs.cluster_id
  cluster_name       = module.ecs.cluster_name
  execution_role_arn = module.ecs.execution_role_arn
  task_policy_json   = data.aws_iam_policy_document.artifact_access.json

  image          = var.api_image
  cpu            = var.api_service.cpu
  memory         = var.api_service.memory
  desired_count  = var.api_service.desired_count
  min_count      = var.api_service.min_count
  max_count      = var.api_service.max_count
  container_port = local.api_port

  subnet_ids        = module.network.private_subnet_ids
  security_group_id = module.security.tasks_security_group_id
  target_group_arn  = module.alb.api_target_group_arn

  environment        = local.api_environment
  secrets            = module.secrets.arns_by_name
  log_retention_days = var.log_retention_days

  requests_per_target_target = 600
  alb_resource_label         = "${module.alb.arn_suffix}/${module.alb.api_target_group_arn_suffix}"
}

module "web_service" {
  source = "./modules/ecs-service"

  name_prefix        = local.name_prefix
  service_name       = "web"
  aws_region         = var.aws_region
  cluster_id         = module.ecs.cluster_id
  cluster_name       = module.ecs.cluster_name
  execution_role_arn = module.ecs.execution_role_arn
  # No task policy: the frontend reads nothing from AWS. It renders, and it
  # proxies nothing - the browser calls the API directly, through the same
  # load balancer.
  task_policy_json = ""

  image          = var.web_image
  cpu            = var.web_service.cpu
  memory         = var.web_service.memory
  desired_count  = var.web_service.desired_count
  min_count      = var.web_service.min_count
  max_count      = var.web_service.max_count
  container_port = local.web_port

  subnet_ids        = module.network.private_subnet_ids
  security_group_id = module.security.tasks_security_group_id
  target_group_arn  = module.alb.web_target_group_arn

  # Next needs no application settings at runtime: NEXT_PUBLIC_* is inlined at
  # build time (see infra/docker/web.Dockerfile), and PORT and HOSTNAME are
  # already in the image.
  environment        = {}
  secrets            = {}
  log_retention_days = var.log_retention_days

  requests_per_target_target = 1200
  alb_resource_label         = "${module.alb.arn_suffix}/${module.alb.web_target_group_arn_suffix}"
}

# The worker: the same image as the API, a different command (ADR 0001).
#
# No target group, because nothing calls a worker - it takes work from the
# queue. `stop_timeout_seconds` is above WORKER_SHUTDOWN_GRACE_SECONDS on
# purpose: a worker handed a SIGTERM gives its runs back, and a task killed
# before it finishes that leaves runs waiting for their leases to expire.
module "worker_service" {
  source = "./modules/ecs-service"

  name_prefix        = local.name_prefix
  service_name       = "worker"
  aws_region         = var.aws_region
  cluster_id         = module.ecs.cluster_id
  cluster_name       = module.ecs.cluster_name
  execution_role_arn = module.ecs.execution_role_arn
  task_policy_json   = data.aws_iam_policy_document.artifact_access.json

  image          = var.api_image
  command        = ["python", "-m", "app.workers.runner"]
  cpu            = var.worker_service.cpu
  memory         = var.worker_service.memory
  desired_count  = var.worker_service.desired_count
  min_count      = var.worker_service.min_count
  max_count      = var.worker_service.max_count
  container_port = local.worker_metrics_port

  subnet_ids        = module.network.private_subnet_ids
  security_group_id = module.security.worker_security_group_id
  target_group_arn  = ""

  environment          = local.worker_environment
  secrets              = module.secrets.arns_by_name
  log_retention_days   = var.log_retention_days
  stop_timeout_seconds = 90

  # CPU only. The queue-depth policy this service should scale on needs a
  # CloudWatch metric that nothing publishes yet; see the note beside the
  # policy in modules/ecs-service.
  cpu_target_percent = 65
}

# The embedding service: Ollama serving one model, nomic-embed-text, to the API
# and the worker. The model the registry declares for embeddings is an Ollama
# model, and without a server to reach, ingestion embeds nothing and retrieval
# quietly runs on its lexical arm alone - a deployment that works and searches
# worse, which nothing would report.
#
# A third-party image, so Terraform rolls it (`image_owner`) rather than
# deploy.yml, the way it rolls the database engine. It pulls its model at start
# (files/ollama-entrypoint.sh), and its health check is what keeps a task out of
# service discovery until the model is there. No load balancer: it is reached by
# name, through module.discovery, from inside the VPC only.
module "ollama_service" {
  source = "./modules/ecs-service"

  name_prefix        = local.name_prefix
  service_name       = "ollama"
  aws_region         = var.aws_region
  cluster_id         = module.ecs.cluster_id
  cluster_name       = module.ecs.cluster_name
  execution_role_arn = module.ecs.execution_role_arn
  # Nothing from AWS: it reads no bucket and no secret.
  task_policy_json = ""
  image_owner      = "terraform"

  image       = var.ollama_image
  entry_point = ["/bin/sh", "-c"]
  command     = [file("${path.module}/files/ollama-entrypoint.sh")]

  cpu            = var.ollama_service.cpu
  memory         = var.ollama_service.memory
  desired_count  = var.ollama_service.desired_count
  min_count      = var.ollama_service.min_count
  max_count      = var.ollama_service.max_count
  container_port = local.ollama_port

  subnet_ids           = module.network.private_subnet_ids
  security_group_id    = module.security.ollama_security_group_id
  target_group_arn     = ""
  service_registry_arn = module.discovery.service_arns["ollama"]

  # Ollama's own settings, not the application's. The task runs as uid 10001
  # like every other (modules/ecs-service), which has no home directory in this
  # image - so HOME and the model store are put somewhere it can write.
  # KEEP_ALIVE -1 keeps the model loaded between calls rather than reloading it
  # after five idle minutes, which would put a load on the first embedding of
  # every quiet spell.
  environment = {
    HOME              = "/tmp/ollama"
    OLLAMA_MODELS     = "/tmp/ollama/models"
    OLLAMA_HOST       = "0.0.0.0:${local.ollama_port}"
    OLLAMA_KEEP_ALIVE = "-1"
    OLLAMA_MODEL      = local.ollama_model
    OLLAMA_MODEL_PIN  = local.ollama_model_pin
  }
  secrets            = {}
  log_retention_days = var.log_retention_days

  # Healthy only once the model answers under the name the application uses,
  # which is after the pull and the copy. The start period covers the download.
  health_check = {
    command      = ["CMD", "/bin/ollama", "show", local.ollama_model]
    interval     = 30
    timeout      = 10
    retries      = 3
    start_period = 180
  }
  stop_timeout_seconds = 30

  cpu_target_percent = 60
}

module "alb" {
  source = "./modules/alb"

  name_prefix           = local.name_prefix
  vpc_id                = module.network.vpc_id
  public_subnet_ids     = module.network.public_subnet_ids
  security_group_id     = module.security.alb_security_group_id
  certificate_arn       = var.certificate_arn
  api_port              = local.api_port
  web_port              = local.web_port
  api_health_check_path = var.api_health_check_path
  deletion_protection   = var.environment == "production"
}

# --- continuous deployment ---------------------------------------------------

# What deploy.yml assumes. One per environment, trusted by that environment's
# GitHub deploy job only, and able to roll this cluster's services and nothing
# of any other environment's.
module "deploy_role" {
  source = "./modules/deploy-role"

  name_prefix           = local.name_prefix
  github_repository     = var.github_repository
  github_environment    = var.environment
  cluster_name          = module.ecs.cluster_name
  migration_task_family = module.api_service.task_definition_family
  ecr_repositories      = ["aether-api", "aether-web"]

  # The roles the three pipeline-deployed task definitions name. Not the
  # embedding service's: deploy.yml never registers a revision for it.
  pass_role_arns = [
    module.ecs.execution_role_arn,
    module.api_service.task_role_arn,
    module.worker_service.task_role_arn,
    module.web_service.task_role_arn,
  ]
}
