output "arn" {
  description = "The role deploy.yml assumes; the GitHub variable AWS_ROLE_ARN."
  value       = aws_iam_role.this.arn
}
