output "bucket" {
  description = "Bucket name, which is what the application reads as S3_BUCKET."
  value       = aws_s3_bucket.artifacts.id
}

output "arn" {
  description = "Bucket ARN, for the task role's policy."
  value       = aws_s3_bucket.artifacts.arn
}
