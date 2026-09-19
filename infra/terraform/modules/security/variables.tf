variable "name_prefix" {
  description = "Prefix for every security group name."
  type        = string
}

variable "vpc_id" {
  description = "VPC the groups belong to."
  type        = string
}

variable "vpc_cidr" {
  description = "VPC CIDR, for the rules that allow VPC-internal traffic rather than a peer group."
  type        = string
}

variable "allowed_ingress_cidrs" {
  description = "Who may reach the load balancer on 80 and 443."
  type        = list(string)
}

variable "task_ports" {
  description = "Container ports the load balancer forwards to."
  type        = list(number)
}

variable "worker_metrics_port" {
  description = "WORKER_METRICS_PORT, scraped from inside the VPC."
  type        = number
}
