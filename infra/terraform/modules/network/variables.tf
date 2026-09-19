variable "name_prefix" {
  description = "Prefix for every resource name in this module."
  type        = string
}

variable "aws_region" {
  description = "Region, needed to name the S3 gateway endpoint's service."
  type        = string
}

variable "vpc_cidr" {
  description = "CIDR block for the VPC."
  type        = string
}

variable "availability_zone_count" {
  description = "How many AZs to spread public and private subnets across."
  type        = number
}

variable "single_nat_gateway" {
  description = "Share one NAT gateway across every private subnet."
  type        = bool
}

variable "flow_logs_enabled" {
  description = "Record rejected traffic to CloudWatch."
  type        = bool
}

variable "log_retention_days" {
  description = "Retention for the flow log group."
  type        = number
}
