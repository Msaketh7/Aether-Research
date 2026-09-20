#!/usr/bin/env bash
# The account-level things Terraform cannot create for itself, in one command.
#
#   scripts/bootstrap-aws.sh --bucket my-terraform-state --region us-east-1
#   scripts/bootstrap-aws.sh --bucket my-terraform-state --dry-run
#
# Three resources, and each is here for the same reason: it has to exist before
# the thing that would otherwise own it can run.
#
#   * The **state bucket**. Terraform's backend is configured before any
#     provider is, so a root cannot create the bucket holding its own state.
#     Versioning is not optional - it is what makes a corrupted state
#     recoverable, and the state is the only copy of the database password.
#   * The **lock table**, for the same reason, one apply at a time.
#   * The **two ECR repositories**. These are account-scoped and shared by every
#     environment: `deploy.yml` pushes `${ECR_REGISTRY}/aether-api:${SHA}`, and
#     `ECR_REGISTRY` is an account's registry host rather than an environment's.
#     Having the staging root create them would make production's images depend
#     on staging's state, so neither root owns them and this does.
#
# Everything here is idempotent: it checks before it creates and says which of
# the two it did, so re-running after a partial failure is safe and running it
# twice reports a no-op rather than an error.
#
# **This has never been run.** There is no AWS account behind this repository,
# so the commands below are written from the CLI's documented interface and
# verified by shellcheck rather than by an execution. Run it with --dry-run
# first and read what it intends to do.
set -euo pipefail

REGION="${AWS_REGION:-us-east-1}"
BUCKET=""
LOCK_TABLE=""
DRY_RUN=0
REPOSITORIES=(aether-api aether-web)

usage() {
  cat <<'EOF'
Usage: bootstrap-aws.sh --bucket NAME [--region REGION] [--lock-table NAME] [--dry-run]

  --bucket      S3 bucket for Terraform state. Must be globally unique; this is
                the value that replaces REPLACE_ME in environments/*.backend.hcl.
  --region      AWS region (default: $AWS_REGION, or us-east-1).
  --lock-table  DynamoDB table for state locking (default: <bucket>-locks).
                Only needed if you are not using S3 native locking; the shipped
                backend config sets use_lockfile, which needs no table.
  --dry-run     Print what would be created and change nothing.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --bucket) BUCKET="${2:?--bucket needs a value}"; shift 2 ;;
    --region) REGION="${2:?--region needs a value}"; shift 2 ;;
    --lock-table) LOCK_TABLE="${2:?--lock-table needs a value}"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

if [[ -z "$BUCKET" ]]; then
  echo "error: --bucket is required" >&2
  usage >&2
  exit 2
fi

run() {
  if [[ "$DRY_RUN" == 1 ]]; then
    printf '  would run: %s\n' "$*"
  else
    "$@"
  fi
}

echo "region: $REGION"
echo

# --- state bucket ------------------------------------------------------------
echo "state bucket: $BUCKET"
if aws s3api head-bucket --bucket "$BUCKET" >/dev/null 2>&1; then
  echo "  exists"
else
  echo "  creating"
  # us-east-1 is the one region where a location constraint is rejected rather
  # than required, because it is the API's default.
  if [[ "$REGION" == "us-east-1" ]]; then
    run aws s3api create-bucket --bucket "$BUCKET" --region "$REGION"
  else
    run aws s3api create-bucket --bucket "$BUCKET" --region "$REGION" \
      --create-bucket-configuration "LocationConstraint=$REGION"
  fi
fi

# Applied every run, not only on creation: these are the properties that make
# the state safe to keep, and a bucket someone made by hand may have none of
# them. Each of these calls is idempotent.
echo "  enforcing versioning, encryption, public access block and TLS-only access"
run aws s3api put-bucket-versioning --bucket "$BUCKET" \
  --versioning-configuration Status=Enabled
run aws s3api put-bucket-encryption --bucket "$BUCKET" \
  --server-side-encryption-configuration \
  '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"},"BucketKeyEnabled":true}]}'
run aws s3api put-public-access-block --bucket "$BUCKET" \
  --public-access-block-configuration \
  'BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true'
run aws s3api put-bucket-policy --bucket "$BUCKET" --policy "$(
  cat <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "DenyInsecureTransport",
      "Effect": "Deny",
      "Principal": "*",
      "Action": "s3:*",
      "Resource": ["arn:aws:s3:::$BUCKET", "arn:aws:s3:::$BUCKET/*"],
      "Condition": {"Bool": {"aws:SecureTransport": "false"}}
    }
  ]
}
EOF
)"

# --- lock table (only if asked for) -----------------------------------------
# The shipped backend config uses S3 native locking (`use_lockfile`), which
# needs no table at all. This stays for a backend that predates it.
if [[ -n "$LOCK_TABLE" ]]; then
  echo
  echo "lock table: $LOCK_TABLE"
  if aws dynamodb describe-table --table-name "$LOCK_TABLE" --region "$REGION" >/dev/null 2>&1; then
    echo "  exists"
  else
    echo "  creating"
    run aws dynamodb create-table \
      --table-name "$LOCK_TABLE" \
      --region "$REGION" \
      --attribute-definitions AttributeName=LockID,AttributeType=S \
      --key-schema AttributeName=LockID,KeyType=HASH \
      --billing-mode PAY_PER_REQUEST
  fi
fi

# --- image repositories ------------------------------------------------------
echo
for repository in "${REPOSITORIES[@]}"; do
  echo "repository: $repository"
  if aws ecr describe-repositories --repository-names "$repository" \
    --region "$REGION" >/dev/null 2>&1; then
    echo "  exists"
  else
    echo "  creating"
    # Scanning on push is belt and braces: build.yml already scans and refuses
    # to push a failing image, but an image that reaches this registry by some
    # other route should still be looked at.
    run aws ecr create-repository \
      --repository-name "$repository" \
      --region "$REGION" \
      --image-tag-mutability IMMUTABLE \
      --image-scanning-configuration scanOnPush=true \
      --encryption-configuration encryptionType=AES256
  fi

  # Untagged images accumulate on every deploy, because deploying by digest
  # leaves the previous digest untagged rather than deleted.
  run aws ecr put-lifecycle-policy \
    --repository-name "$repository" \
    --region "$REGION" \
    --lifecycle-policy-text "$(
      cat <<'EOF'
{
  "rules": [
    {
      "rulePriority": 1,
      "description": "Expire untagged images after 14 days",
      "selection": {
        "tagStatus": "untagged",
        "countType": "sinceImagePushed",
        "countUnit": "days",
        "countNumber": 14
      },
      "action": {"type": "expire"}
    }
  ]
}
EOF
    )"
done

echo
if [[ "$DRY_RUN" == 1 ]]; then
  echo "dry run: nothing was created."
  exit 0
fi

account="$(aws sts get-caller-identity --query Account --output text)"
cat <<EOF

Done. What to do with it:

  environments/*.backend.hcl   bucket = "$BUCKET"
  GitHub variable ECR_REGISTRY $account.dkr.ecr.$REGION.amazonaws.com
  environments/*.tfvars        api_image / web_image under that registry

Then: make tf-plan ENV=staging
EOF
