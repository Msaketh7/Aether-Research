# The artifact bucket.
#
# Everything the platform stores that is not a row: uploaded documents, fetched
# pages, generated reports. ADR 0010 makes `ObjectStorage` the interface, so
# this bucket is one implementation of it and the application knows only its
# name.

resource "aws_s3_bucket" "artifacts" {
  bucket = var.bucket_name

  tags = { Name = var.bucket_name }
}

# Every object, every time. Not a "nice to have" here: the objects are
# user-uploaded documents and the reports derived from them, and
# docs/threat-model.md treats both as confidential. SSE-S3 rather than SSE-KMS
# because the workload reads thousands of small objects per run and a KMS
# request per read is both a cost and a throttling ceiling; a customer-managed
# key is the upgrade when a compliance requirement asks for one.
resource "aws_s3_bucket_server_side_encryption_configuration" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
    bucket_key_enabled = true
  }
}

# The disaster-recovery half of TDD section 21: a deleted or overwritten
# artifact is recoverable, because the delete writes a marker rather than
# removing bytes.
resource "aws_s3_bucket_versioning" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_public_access_block" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id

  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id

  # An interrupted upload leaves parts that are billed and invisible in a
  # listing. A worker killed mid-ingestion is a normal event here - shutdown
  # hands runs back - so this is not a theoretical tidy-up.
  rule {
    id     = "abort-incomplete-multipart-uploads"
    status = "Enabled"

    filter {}

    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }

  # Versioning without expiry grows without bound. Old versions exist to
  # recover from an accident that is noticed within weeks, not years.
  rule {
    id     = "expire-noncurrent-versions"
    status = "Enabled"

    filter {}

    noncurrent_version_expiration {
      noncurrent_days           = var.noncurrent_version_retention_days
      newer_noncurrent_versions = 3
    }
  }

  depends_on = [aws_s3_bucket_versioning.artifacts]
}

# Refuse plaintext. The application always uses HTTPS - botocore does - so this
# costs nothing and closes the case where something else does not.
data "aws_iam_policy_document" "artifacts" {
  statement {
    sid     = "DenyInsecureTransport"
    effect  = "Deny"
    actions = ["s3:*"]
    resources = [
      aws_s3_bucket.artifacts.arn,
      "${aws_s3_bucket.artifacts.arn}/*",
    ]

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id
  policy = data.aws_iam_policy_document.artifacts.json

  depends_on = [aws_s3_bucket_public_access_block.artifacts]
}
