variable "name_prefix" {
  description = "Prefix for the load balancer and its target groups. ALB names are capped at 32 characters."
  type        = string
}

variable "vpc_id" {
  description = "VPC the target groups resolve task IPs in."
  type        = string
}

variable "public_subnet_ids" {
  description = "Public subnets, at least two, in different AZs."
  type        = list(string)
}

variable "security_group_id" {
  description = "The ALB security group."
  type        = string
}

variable "certificate_arn" {
  description = "ACM certificate. Empty means HTTP only."
  type        = string
}

variable "ssl_policy" {
  description = "Negotiation policy for the HTTPS listener. TLS 1.2 minimum."
  type        = string
  default     = "ELBSecurityPolicy-TLS13-1-2-2021-06"
}

variable "web_port" {
  description = "Container port the frontend listens on."
  type        = number
  default     = 3000
}

variable "api_port" {
  description = "Container port the API listens on."
  type        = number
  default     = 8000
}

variable "web_health_check_path" {
  description = "A page the frontend renders without a session and without calling the API."
  type        = string
  default     = "/login"
}

variable "api_health_check_path" {
  description = "Liveness, not readiness. See the variable of the same name in the root."
  type        = string
}

variable "idle_timeout_seconds" {
  description = "Must exceed SSE_MAX_CONNECTION_SECONDS, or long streams are cut by the load balancer."
  type        = number
  default     = 960
}

variable "deletion_protection" {
  description = "Refuse to delete the load balancer until this is turned off."
  type        = bool
  default     = false
}

variable "access_logs_bucket" {
  description = "Bucket for ALB access logs. Empty disables them."
  type        = string
  default     = ""
}
