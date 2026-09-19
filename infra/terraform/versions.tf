# Provider and Terraform version constraints.
#
# Pinned to a major version, not to an exact one: a patch release of the AWS
# provider should not require a commit here, and a major release must.
#
# The backend is partial on purpose. The bucket and the state key differ per
# environment, and hard-coding one environment's bucket into the root is how a
# `terraform apply` ends up pointed at the wrong state. `make tf-plan ENV=x`
# supplies environments/x.backend.hcl.

terraform {
  required_version = ">= 1.9.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }

  backend "s3" {}
}
