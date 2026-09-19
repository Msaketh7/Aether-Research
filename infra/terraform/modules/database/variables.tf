variable "name_prefix" {
  description = "Prefix for every resource name in this module."
  type        = string
}

variable "subnet_ids" {
  description = "Private subnets for the DB subnet group."
  type        = list(string)
}

variable "security_group_id" {
  description = "The database security group, which allows only the API and the worker."
  type        = string
}

variable "engine_version" {
  description = "PostgreSQL major version. 17 is what the local stack and CI run."
  type        = string
  default     = "17"
}

variable "instance_class" {
  description = "RDS instance class."
  type        = string
}

variable "allocated_storage" {
  description = "Initial gp3 storage, in GiB."
  type        = number
}

variable "max_allocated_storage" {
  description = "Ceiling for storage autoscaling, in GiB."
  type        = number
}

variable "database_name" {
  description = "Initial database name."
  type        = string
  default     = "aether"
}

variable "master_username" {
  description = "Master user. Migrations run as this user, which is why it may create extensions."
  type        = string
  default     = "aether"
}

variable "multi_az" {
  description = "Synchronous standby in a second AZ."
  type        = bool
}

variable "backup_retention_days" {
  description = "Days of automated backups, which is also the point-in-time recovery window."
  type        = number
}

variable "deletion_protection" {
  description = "Refuse to delete the instance until this is turned off."
  type        = bool
}

variable "performance_insights" {
  description = "Enable Performance Insights."
  type        = bool
}

variable "skip_final_snapshot" {
  description = "Destroy without a final snapshot. Never true in production."
  type        = bool
}

variable "apply_immediately" {
  description = "Apply modifications outside the maintenance window."
  type        = bool
}

variable "monitoring_interval_sec" {
  description = "Enhanced monitoring interval in seconds; 0 disables it."
  type        = number
}
