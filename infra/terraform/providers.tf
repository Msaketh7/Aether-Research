# The one provider, with the tags every resource inherits.
#
# `default_tags` rather than a tag block per resource: a resource created
# without them is invisible to cost allocation and to the "what is this?"
# question at 3am, and remembering to tag is not a control.

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project     = var.project
      Environment = var.environment
      ManagedBy   = "terraform"
      Repository  = "aether-research"
    }
  }
}
