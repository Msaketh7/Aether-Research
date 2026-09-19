# The cluster, and the one role every task in it shares.
#
# Two roles exist per service and the difference matters. The *execution* role
# belongs to the ECS agent: it pulls the image, writes the log stream and reads
# the secrets it injects, and it is the same for every service because every
# service needs exactly that. The *task* role belongs to the running code and
# is different for each service, so it is created in the service module beside
# the thing it grants.

resource "aws_ecs_cluster" "this" {
  name = var.name_prefix

  setting {
    name  = "containerInsights"
    value = var.container_insights ? "enabled" : "disabled"
  }

  tags = { Name = var.name_prefix }
}

resource "aws_ecs_cluster_capacity_providers" "this" {
  cluster_name       = aws_ecs_cluster.this.name
  capacity_providers = ["FARGATE", "FARGATE_SPOT"]

  default_capacity_provider_strategy {
    capacity_provider = "FARGATE"
    weight            = 1
    base              = 0
  }
}

# --- execution role ----------------------------------------------------------

data "aws_iam_policy_document" "task_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "execution" {
  name               = "${var.name_prefix}-task-execution"
  assume_role_policy = data.aws_iam_policy_document.task_assume.json
  description        = "Used by the ECS agent to pull images, write logs and inject secrets."
}

resource "aws_iam_role_policy_attachment" "execution_managed" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

# Named ARNs, not a wildcard. The managed policy above covers ECR and logs; it
# does not cover Secrets Manager, and the usual shortcut for that is
# `secretsmanager:*` on `*` - which grants every secret in the account to a
# role that needs six of them.
data "aws_iam_policy_document" "execution_secrets" {
  count = length(var.secret_arns) > 0 ? 1 : 0

  statement {
    sid       = "ReadInjectedSecrets"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = var.secret_arns
  }
}

resource "aws_iam_role_policy" "execution_secrets" {
  count = length(var.secret_arns) > 0 ? 1 : 0

  name   = "read-injected-secrets"
  role   = aws_iam_role.execution.id
  policy = data.aws_iam_policy_document.execution_secrets[0].json
}
