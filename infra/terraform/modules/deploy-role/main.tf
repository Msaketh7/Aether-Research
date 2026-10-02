# The role `.github/workflows/deploy.yml` assumes, and only what that workflow
# does with it.
#
# Federated through GitHub's OIDC provider, so there is no access key anywhere:
# a deploy job exchanges a token GitHub signed for this run for credentials that
# last an hour. The trust is narrower than "this repository" - it is one GitHub
# *environment* of this repository, which is where the deploy job's approval
# rule lives. A workflow on a branch, a fork's pull request or a job without
# `environment: production` presents a different `sub` and is refused here,
# whatever the workflow file says.
#
# The OIDC provider itself is looked up, not created. It is one per account and
# shared by every environment, so a staging root that created it would make
# production depend on staging's state; scripts/bootstrap-aws.sh creates it,
# alongside the other account-scoped things Terraform cannot own.
#
# The permissions mirror deploy.yml step by step, and
# apps/api/tests/test_infrastructure.py fails when the workflow calls an AWS
# operation this policy does not grant.

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}
data "aws_partition" "current" {}

data "aws_iam_openid_connect_provider" "github" {
  url = "https://${var.oidc_host}"
}

locals {
  account   = data.aws_caller_identity.current.account_id
  region    = data.aws_region.current.region
  partition = data.aws_partition.current.partition

  cluster_arn = "arn:${local.partition}:ecs:${local.region}:${local.account}:cluster/${var.cluster_name}"
}

data "aws_iam_policy_document" "trust" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [data.aws_iam_openid_connect_provider.github.arn]
    }

    condition {
      test     = "StringEquals"
      variable = "${var.oidc_host}:aud"
      values   = ["sts.amazonaws.com"]
    }

    # StringEquals, not StringLike: a wildcard here is how a trust meant for one
    # environment ends up trusting every branch.
    condition {
      test     = "StringEquals"
      variable = "${var.oidc_host}:sub"
      values   = ["repo:${var.github_repository}:environment:${var.github_environment}"]
    }
  }
}

resource "aws_iam_role" "this" {
  name                 = "${var.name_prefix}-deploy"
  description          = "Assumed by deploy.yml in the ${var.github_environment} GitHub environment of ${var.github_repository}."
  assume_role_policy   = data.aws_iam_policy_document.trust.json
  max_session_duration = 3600
}

data "aws_iam_policy_document" "deploy" {
  # `amazon-ecr-login`. The token is account-wide by design; what it can be
  # used for is the next statement.
  statement {
    sid       = "SignInToEcr"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }

  # "Mirror the images into ECR": `imagetools create` copies a manifest and its
  # blobs into these repositories and nowhere else.
  statement {
    sid = "PushTheImages"
    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:BatchGetImage",
      "ecr:CompleteLayerUpload",
      "ecr:GetDownloadUrlForLayer",
      "ecr:InitiateLayerUpload",
      "ecr:PutImage",
      "ecr:UploadLayerPart",
    ]
    resources = [
      for repository in var.ecr_repositories :
      "arn:${local.partition}:ecr:${local.region}:${local.account}:repository/${repository}"
    ]
  }

  # "Render the task definitions" reads the current revision, and "Register
  # them" writes the next. Neither action supports a resource-level
  # restriction, so the scope is the PassRole statement below: a revision can
  # only be registered with roles this policy lets the caller pass.
  statement {
    sid       = "RegisterRevisions"
    actions   = ["ecs:DescribeTaskDefinition", "ecs:RegisterTaskDefinition"]
    resources = ["*"]
  }

  # "Record the running revisions", "Roll the services", and both
  # `wait services-stable` calls - in this cluster only.
  statement {
    sid     = "RollTheServices"
    actions = ["ecs:DescribeServices", "ecs:UpdateService"]
    resources = [
      "arn:${local.partition}:ecs:${local.region}:${local.account}:service/${var.cluster_name}/*",
    ]
  }

  # "Run the migrations": one task, from the API family, in this cluster.
  statement {
    sid     = "RunTheMigration"
    actions = ["ecs:RunTask"]
    resources = [
      "arn:${local.partition}:ecs:${local.region}:${local.account}:task-definition/${var.migration_task_family}:*",
    ]

    condition {
      test     = "ArnEquals"
      variable = "ecs:cluster"
      values   = [local.cluster_arn]
    }
  }

  # `wait tasks-stopped` and the exit-code check that follows it.
  statement {
    sid     = "WatchTheMigration"
    actions = ["ecs:DescribeTasks"]
    resources = [
      "arn:${local.partition}:ecs:${local.region}:${local.account}:task/${var.cluster_name}/*",
    ]
  }

  # Resources created with tags need permission to tag at creation, and the
  # task definitions carry the provider's default tags. Allowed only as part of
  # creating something, never on its own.
  statement {
    sid       = "TagWhatItCreates"
    actions   = ["ecs:TagResource"]
    resources = ["*"]

    condition {
      test     = "StringEquals"
      variable = "ecs:CreateAction"
      values   = ["RegisterTaskDefinition", "RunTask"]
    }
  }

  # A task definition names the roles its tasks run as, and registering or
  # running one passes them. These roles and no others, and only to ECS.
  statement {
    sid       = "PassTheTaskRoles"
    actions   = ["iam:PassRole"]
    resources = var.pass_role_arns

    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values   = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role_policy" "deploy" {
  name   = "deploy"
  role   = aws_iam_role.this.id
  policy = data.aws_iam_policy_document.deploy.json
}
