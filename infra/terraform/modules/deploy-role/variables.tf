variable "name_prefix" {
  description = "Project and environment prefix; the role is <prefix>-deploy."
  type        = string
}

variable "github_repository" {
  description = "owner/name of the repository whose deploy workflow may assume this role."
  type        = string
}

variable "github_environment" {
  description = "The GitHub environment the deploy job runs in. Only that environment's jobs are trusted."
  type        = string
}

variable "oidc_host" {
  description = "GitHub's OIDC issuer host, which is also the prefix of its claim condition keys."
  type        = string
  default     = "token.actions.githubusercontent.com"
}

variable "cluster_name" {
  description = "The cluster whose services the workflow rolls and in which it runs the migration."
  type        = string
}

variable "migration_task_family" {
  description = "The task definition family the migration runs from - the API's."
  type        = string
}

variable "ecr_repositories" {
  description = "ECR repositories the workflow mirrors images into."
  type        = list(string)
}

variable "pass_role_arns" {
  description = "Roles a registered task definition may name: the execution role and each service's task role."
  type        = list(string)
}
