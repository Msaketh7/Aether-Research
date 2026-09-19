output "dns_name" {
  description = "The load balancer's hostname. Point the site's DNS record at this."
  value       = aws_lb.this.dns_name
}

output "zone_id" {
  description = "Hosted zone for an alias record."
  value       = aws_lb.this.zone_id
}

output "arn" {
  description = "Load balancer ARN, used by alarms."
  value       = aws_lb.this.arn
}

output "arn_suffix" {
  description = "The form CloudWatch and the request-count scaling policy use."
  value       = aws_lb.this.arn_suffix
}

output "web_target_group_arn" {
  value       = aws_lb_target_group.web.arn
  description = "Attached to the web service."
}

output "api_target_group_arn" {
  value       = aws_lb_target_group.api.arn
  description = "Attached to the API service."
}

output "api_target_group_arn_suffix" {
  value       = aws_lb_target_group.api.arn_suffix
  description = "The resource label a request-count target-tracking policy needs."
}

output "web_target_group_arn_suffix" {
  value       = aws_lb_target_group.web.arn_suffix
  description = "The resource label a request-count target-tracking policy needs."
}

output "url" {
  description = "Base URL of the deployment, as the smoke test should call it."
  value       = "${var.certificate_arn == "" ? "http" : "https"}://${aws_lb.this.dns_name}"
}
