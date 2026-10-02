# Secrets Manager entries, in two kinds.
#
# **Derived** secrets have a value this configuration already knows - the
# database DSN and the Redis URL, assembled by the modules that created those
# services. Terraform writes them.
#
# **Declared** secrets have a value Terraform must never see: the model and
# search provider keys, the single sign-on client secrets, and the key the API
# signs access tokens with. Terraform creates the entry and the task's permission
# to read it, and the value is written out of band, once, by a person or a
# deployment secret store. A placeholder version is created so that a task
# referencing it starts rather than failing to pull - the application already
# treats a blank credential as an absent one (app/core/config.py), so a
# provider whose key has not been set simply does not exist, which is a
# degraded deployment rather than a crashed one.
#
# The exception is JWT_PRIVATE_KEY, which the API and the worker refuse to start
# without. That is deliberate: a deployment that cannot sign tokens cannot sign
# anybody in, and a task that fails its first start naming the missing key is a
# better first apply than a login page that never works.

resource "aws_secretsmanager_secret" "derived" {
  for_each = toset(var.derived_names)

  name        = "${var.name_prefix}/${lower(replace(each.key, "_", "-"))}"
  description = "Aether Research: ${each.key}"

  # Zero, because these are recreated by the same apply that destroys them and
  # a name held in a 30-day recovery window cannot be reused. The value is
  # reproducible from this configuration, so there is nothing to recover.
  recovery_window_in_days = 0
}

resource "aws_secretsmanager_secret_version" "derived" {
  for_each = toset(var.derived_names)

  secret_id = aws_secretsmanager_secret.derived[each.key].id
  # The only place a derived value is read. `each.value` cannot be used here:
  # the iterator comes from the names, not the values.
  secret_string = var.derived_values[each.key]

  lifecycle {
    precondition {
      # `nonsensitive` on the answer, not on the data: whether a key is
      # present leaks nothing, and without this the condition itself would be
      # sensitive and refused. Without the check, a name with no value would
      # fail at apply time with a missing-key error naming neither half.
      condition     = nonsensitive(contains(keys(var.derived_values), each.key))
      error_message = "derived_names contains ${each.key}, which derived_values has no value for."
    }
  }
}

resource "aws_secretsmanager_secret" "declared" {
  for_each = toset(var.declared)

  name        = "${var.name_prefix}/${lower(replace(each.key, "_", "-"))}"
  description = "Aether Research: ${each.key}. Value is set outside Terraform."

  # Seven days, the minimum: these hold values Terraform cannot regenerate, so
  # a destroy should be recoverable for at least a week.
  recovery_window_in_days = 7
}

resource "aws_secretsmanager_secret_version" "declared_placeholder" {
  for_each = toset(var.declared)

  secret_id = aws_secretsmanager_secret.declared[each.key].id
  # One space, not an empty string - and the difference is the first apply.
  # The provider sends `secret_string` only when `GetOk` finds it set, which an
  # empty string is not, so `""` reaches PutSecretValue as no value at all and
  # AWS refuses it ("You must provide either SecretString or SecretBinary").
  # The settings layer strips a credential before deciding it is absent
  # (app/core/config.py, _blank_credential_is_no_credential), so a space reads
  # as "no credential" exactly as an empty string would have.
  # apps/api/tests/test_infrastructure.py holds the two to each other.
  secret_string = " "

  lifecycle {
    # The whole point: once a real value is written, Terraform stops having an
    # opinion about it. Without this, every apply would overwrite the key with
    # the placeholder and the deployment would lose its providers at the next
    # unrelated infrastructure change.
    ignore_changes = [secret_string]
  }
}
