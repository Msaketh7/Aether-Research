variable "name_prefix" {
  description = "Path prefix for every secret name, e.g. aether-staging/database-url."
  type        = string
}

# Names and values arrive separately, which looks like duplication and is not.
#
# Terraform propagates sensitivity through every expression, so a map whose
# values are sensitive is itself sensitive - keys included - and a sensitive
# value cannot be a `for_each` argument, because the key would appear in
# resource addresses and plan output. Splitting the two is what lets the
# *names* build the resources while the *values* stay marked all the way to the
# API call. The check below is what keeps the halves in step.
variable "derived_names" {
  description = "Setting names whose values this configuration computed."
  type        = list(string)
  default     = []
}

variable "derived_values" {
  description = "Setting name -> value. Must contain every name in derived_names."
  type        = map(string)
  sensitive   = true
  default     = {}
}

variable "declared" {
  description = "Setting names whose values are written outside Terraform."
  type        = list(string)
  default     = []
}
