variable "name_prefix" {
  description = "Project and environment prefix, shared by every service."
  type        = string
}

variable "service_name" {
  description = "api, web or worker. Also the container name and the log stream prefix."
  type        = string
}

variable "aws_region" {
  description = "Region, for the awslogs driver."
  type        = string
}

variable "cluster_id" {
  description = "ECS cluster ARN."
  type        = string
}

variable "cluster_name" {
  description = "ECS cluster name, needed to build the autoscaling resource id."
  type        = string
}

variable "execution_role_arn" {
  description = "Shared execution role from the cluster module."
  type        = string
}

variable "task_policy_json" {
  description = "Inline policy for the task role. Empty means the process needs nothing from AWS."
  type        = string
  default     = ""
}

variable "image" {
  description = "Fully qualified container image, including the tag or digest."
  type        = string
}

variable "command" {
  description = "Overrides the image's CMD. Null keeps it - which is how the API runs and the worker does not."
  type        = list(string)
  default     = null
}

variable "cpu" {
  description = "Task CPU units. 1024 is one vCPU."
  type        = number
}

variable "memory" {
  description = "Task memory in MiB. Must be a combination Fargate accepts for the CPU above."
  type        = number
}

variable "cpu_architecture" {
  description = "X86_64 or ARM64. ARM64 is cheaper per vCPU-hour and needs an image built for it."
  type        = string
  default     = "X86_64"

  validation {
    condition     = contains(["X86_64", "ARM64"], var.cpu_architecture)
    error_message = "cpu_architecture must be X86_64 or ARM64."
  }
}

variable "container_port" {
  description = "Port the process listens on. 0 for a process that listens on nothing."
  type        = number
  default     = 0
}

variable "target_group_arn" {
  description = "Target group to register in. Empty means the service is not behind the load balancer."
  type        = string
  default     = ""
}

variable "health_check_grace_period" {
  description = "Seconds before target-group health failures can kill a starting task."
  type        = number
  default     = 90
}

variable "stop_timeout_seconds" {
  description = <<-EOT
    Grace between SIGTERM and SIGKILL.

    For the worker this must exceed WORKER_SHUTDOWN_GRACE_SECONDS: the worker
    hands its runs back on shutdown, and a task killed before it finishes that
    leaves a run holding an expired lease until the reconciliation sweep
    notices. Fargate's ceiling is 120.
  EOT
  type        = number
  default     = 60
}

variable "desired_count" {
  description = "Tasks at creation. Autoscaling owns the number afterwards."
  type        = number
}

variable "subnet_ids" {
  description = "Private subnets to place tasks in."
  type        = list(string)
}

variable "security_group_id" {
  description = "Security group for the tasks."
  type        = string
}

variable "environment" {
  description = "Plain settings, injected as environment variables."
  type        = map(string)
  default     = {}
}

variable "secrets" {
  description = "Settings whose values come from Secrets Manager, as name -> secret ARN."
  type        = map(string)
  default     = {}
}

variable "log_retention_days" {
  description = "Retention for this service's log group."
  type        = number
}

variable "enable_execute_command" {
  description = "Allow `aws ecs execute-command` into a running task. Off outside an incident."
  type        = bool
  default     = false
}

# --- autoscaling -------------------------------------------------------------

variable "autoscaling_enabled" {
  description = "Register an autoscaling target for this service."
  type        = bool
  default     = true
}

variable "min_count" {
  description = "Floor for autoscaling."
  type        = number
}

variable "max_count" {
  description = "Ceiling for autoscaling. Bounds the blast radius of a runaway metric."
  type        = number
}

variable "cpu_target_percent" {
  description = "Average CPU the scaler holds the service at."
  type        = number
  default     = 60
}

variable "requests_per_target_target" {
  description = "Requests per target per minute to hold. 0 disables the request-count policy."
  type        = number
  default     = 0
}

variable "alb_resource_label" {
  description = "app/<alb>/<id>/targetgroup/<tg>/<id>, required by the request-count policy."
  type        = string
  default     = ""
}

variable "queue_depth_target" {
  description = "Queued runs per worker to hold. 0 disables; see the note beside the policy."
  type        = number
  default     = 0
}

variable "metric_namespace" {
  description = "CloudWatch namespace the queue-depth metric would be published to."
  type        = string
  default     = "AetherResearch"
}
