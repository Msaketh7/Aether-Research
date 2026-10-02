output "namespace" {
  description = "The private DNS namespace."
  value       = aws_service_discovery_private_dns_namespace.this.name
}

output "service_arns" {
  description = "Service name -> Cloud Map service ARN, for an ECS service's registry."
  value       = { for name, service in aws_service_discovery_service.this : name => service.arn }
}

output "service_hostnames" {
  description = "Service name -> the DNS name other tasks reach it by."
  value       = { for name in var.services : name => "${name}.${aws_service_discovery_private_dns_namespace.this.name}" }
}
