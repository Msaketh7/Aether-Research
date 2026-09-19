variable "name_prefix" {
  description = "Prefix for every resource name in this module."
  type        = string
}

variable "subnet_ids" {
  description = "Private subnets for the cache subnet group."
  type        = list(string)
}

variable "security_group_id" {
  description = "The cache security group, which allows only the API and the worker."
  type        = string
}

variable "engine_version" {
  description = "Redis version. 7.x matches the local stack and CI."
  type        = string
  default     = "7.1"
}

variable "node_type" {
  description = "ElastiCache node type."
  type        = string
}

variable "replica_count" {
  description = "Read replicas in addition to the primary. Failover needs at least one."
  type        = number
}

variable "automatic_failover" {
  description = "Promote a replica when the primary fails."
  type        = bool
}

variable "multi_az" {
  description = "Place replicas in other availability zones."
  type        = bool
}

variable "snapshot_retention_limit" {
  description = "Days of automatic snapshots; 0 disables them."
  type        = number
}

variable "apply_immediately" {
  description = "Apply modifications outside the maintenance window."
  type        = bool
  default     = false
}

variable "log_group_name" {
  description = "CloudWatch log group for the Redis engine log."
  type        = string
}
