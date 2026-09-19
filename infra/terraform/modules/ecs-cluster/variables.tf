variable "name_prefix" {
  description = "Cluster name and role name prefix."
  type        = string
}

variable "container_insights" {
  description = "Per-task CPU and memory metrics in CloudWatch. Billed per metric."
  type        = bool
  default     = true
}

variable "secret_arns" {
  description = "Exactly the Secrets Manager entries the agent may read to inject into tasks."
  type        = list(string)
  default     = []
}
