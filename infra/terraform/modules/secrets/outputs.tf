output "arns_by_name" {
  description = "Setting name -> secret ARN, in the shape a task definition's `secrets` block wants."
  value = merge(
    { for name, secret in aws_secretsmanager_secret.derived : name => secret.arn },
    { for name, secret in aws_secretsmanager_secret.declared : name => secret.arn },
  )
}

output "all_arns" {
  description = "Every ARN, for the execution role's read policy."
  value = concat(
    [for secret in aws_secretsmanager_secret.derived : secret.arn],
    [for secret in aws_secretsmanager_secret.declared : secret.arn],
  )
}
