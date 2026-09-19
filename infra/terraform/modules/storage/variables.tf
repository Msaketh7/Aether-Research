variable "bucket_name" {
  description = "Globally unique bucket name. S3 names are a flat global namespace, which is why this carries the account id."
  type        = string
}

variable "noncurrent_version_retention_days" {
  description = "How long a superseded version is kept before expiry."
  type        = number
  default     = 90
}
