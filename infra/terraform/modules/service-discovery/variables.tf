variable "name_prefix" {
  description = "Project and environment prefix; the namespace is <prefix>.internal."
  type        = string
}

variable "vpc_id" {
  description = "VPC the namespace resolves in."
  type        = string
}

variable "services" {
  description = "Names to register, each resolving to that service's running tasks."
  type        = list(string)
}
