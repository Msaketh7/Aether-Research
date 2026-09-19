# Partial backend configuration: `terraform init -backend-config=this`.
#
# The bucket and the lock table are not created by this configuration - they
# have to exist before the first `init`, which is the one bootstrap step
# Terraform cannot do for itself. Create them once, by hand or by a separate
# root, with versioning and encryption on: versioning is what makes a corrupted
# state recoverable, and it is the only copy of the database password.

bucket       = "REPLACE_ME-terraform-state"
key          = "aether/production/terraform.tfstate"
region       = "us-east-1"
encrypt      = true
use_lockfile = true
