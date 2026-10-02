# Names, and the settings every task carries.
#
# The application settings are assembled here rather than in each service's
# module call, because the API and the worker must agree on almost all of them:
# they share `app/core/config.py`, and a run that is planned under one set of
# ceilings and executed under another is a bug that would take a long time to
# find.

data "aws_caller_identity" "current" {}

locals {
  name_prefix = "${var.project}-${var.environment}"

  # Where people reach this deployment: the domain once DNS points at the load
  # balancer, the load balancer's own name before then.
  public_url = trimsuffix(var.public_url != "" ? var.public_url : module.alb.url, "/")

  # S3 bucket names are a single global namespace, so a name that is unique
  # within this account is not enough.
  artifact_bucket = "${local.name_prefix}-artifacts-${data.aws_caller_identity.current.account_id}"

  # Settings shared by the API and the worker. Every key here is a field of
  # `Settings` in app/core/config.py; pydantic ignores anything else, so a
  # misspelling would be a setting that silently keeps its default.
  # apps/api/tests/test_infrastructure.py fails the build on one.
  common_app_environment = {
    APP_ENV    = var.environment
    LOG_LEVEL  = "info"
    AWS_REGION = var.aws_region

    # Object storage through the task role. No keys: botocore's default
    # credential chain resolves the role, which is why S3_ACCESS_KEY_ID and
    # S3_SECRET_ACCESS_KEY are absent here and must stay absent.
    STORAGE_BACKEND = "s3"
    S3_BUCKET       = module.storage.bucket
    S3_REGION       = var.aws_region

    # The development identity is refused outside local and test by
    # app/auth/principal.py's allowlist. Setting this to false as well is the
    # belt to that brace: two independent things now have to be wrong before an
    # unauthenticated request is served as a user.
    DEV_IDENTITY_ENABLED = "false"

    # Exactly one trusted hop, which is the load balancer. The ALB appends to
    # X-Forwarded-For (see the xff_header_processing_mode note in the alb
    # module), so the rightmost entry is the address it observed. A larger
    # number here would let a caller choose their own rate-limit bucket and
    # write fiction into the audit log.
    TRUSTED_PROXY_HOPS = "1"

    # Empty unless something is served from another origin: the browser
    # reaches the API through the same load balancer under /api, so the
    # request is same-origin and CORS does not apply.
    CORS_ALLOW_ORIGINS = join(",", var.cors_allow_origins)

    # Required rather than defaulted: the application's fallback carries an
    # example.com contact, which SEC blocks. See variables.tf.
    SEC_USER_AGENT = var.sec_user_agent

    METRICS_ENABLED = "true"

    # Embeddings come from the Ollama service this root runs inside the VPC
    # (ollama_service in main.tf), reached by its private DNS name. Pinned
    # rather than resolved, because an index whose vectors came from two models
    # fails silently - and the model the service pulls is held to this key by
    # apps/api/tests/test_infrastructure.py.
    EMBEDDING_MODEL = local.embedding_model_key
    OLLAMA_BASE_URL = "http://${module.discovery.service_hostnames["ollama"]}:${local.ollama_port}"

    # The public origin, which is more than an SSO setting: it is the issuer
    # and the audience of every access token the API mints (app/api/deps.py),
    # so leaving it at its localhost default signs production tokens as
    # http://localhost:3000. The redirect base is the API under the same
    # origin, because the load balancer routes /api/* there.
    SSO_APP_BASE_URL      = local.public_url
    SSO_REDIRECT_BASE_URL = "${local.public_url}/api/v1"
  }

  api_environment = merge(
    local.common_app_environment,
    {
      OTEL_SERVICE_NAME = "${local.name_prefix}-api"
      API_PORT          = tostring(local.api_port)
    },
    var.app_environment,
  )

  worker_environment = merge(
    local.common_app_environment,
    {
      OTEL_SERVICE_NAME   = "${local.name_prefix}-worker"
      WORKER_CONCURRENCY  = tostring(var.worker_concurrency)
      WORKER_METRICS_PORT = tostring(local.worker_metrics_port)
    },
    var.app_environment,
  )

  api_port            = 8000
  web_port            = 3000
  worker_metrics_port = 9100
  ollama_port         = 11434

  # The embedding model, stated once. `embedding_model_key` is the registry key
  # the application pins; `ollama_model` is that entry's `model_id` in
  # app/models/registry.yaml, which is the name the application asks Ollama
  # for. `ollama_model_pin` is the tag actually pulled - `latest` is a moving
  # pointer, and a model that changes under an existing index is the silent
  # failure EMBEDDING_MODEL exists to prevent - and it is then copied to the
  # unversioned name so the application's requests resolve to it.
  embedding_model_key = "ollama:embed"
  ollama_model        = "nomic-embed-text"
  ollama_model_pin    = "nomic-embed-text:v1.5"

  # Declared secrets every deployment needs, whatever else it chooses. Kept out
  # of `var.provider_secret_names` so that overriding that list - to drop a
  # search provider, say - cannot also drop the key the API refuses to start
  # without (app/core/config.py, _check_auth_bounds).
  required_secret_names = ["JWT_PRIVATE_KEY"]

  # Derived secrets: values this configuration computed and the application
  # reads as one string each. Names and values are separate because Terraform
  # marks any expression touching a sensitive value as sensitive, and a
  # sensitive value cannot drive a `for_each` - see modules/secrets/variables.tf.
  derived_secret_names = ["DATABASE_URL", "REDIS_URL"]

  derived_secret_values = {
    DATABASE_URL = module.database.database_url
    REDIS_URL    = module.cache.redis_url
  }
}

# What the API and the worker may do with the artifact bucket, and nothing
# else. `s3:*` on the bucket would also grant the ability to change its policy
# and turn off its public access block from inside the application.
data "aws_iam_policy_document" "artifact_access" {
  statement {
    sid = "ReadWriteArtifacts"
    actions = [
      "s3:GetObject",
      "s3:PutObject",
      "s3:DeleteObject",
      "s3:AbortMultipartUpload",
    ]
    resources = ["${module.storage.arn}/*"]
  }

  statement {
    sid = "ListArtifactBucket"
    actions = [
      "s3:ListBucket",
      "s3:ListBucketMultipartUploads",
      "s3:GetBucketLocation",
    ]
    resources = [module.storage.arn]
  }
}
