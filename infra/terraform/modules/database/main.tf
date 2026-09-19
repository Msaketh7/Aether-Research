# RDS PostgreSQL, with pgvector.
#
# The extensions this schema needs - citext, pgcrypto and vector - are all in
# the RDS-supported set for PostgreSQL 17 and are created by the migrations
# themselves (0001 and 0002), not here. That is the right split: the schema
# owns what it needs, and Terraform owns the server it needs it on.

resource "aws_db_subnet_group" "this" {
  name        = "${var.name_prefix}-db"
  description = "Private subnets only. The database has no route to the internet."
  subnet_ids  = var.subnet_ids

  tags = { Name = "${var.name_prefix}-db" }
}

# A parameter group of our own, because the default one cannot be modified and
# every setting below is one that has to change for this workload.
resource "aws_db_parameter_group" "this" {
  name        = "${var.name_prefix}-pg17"
  family      = "postgres17"
  description = "Aether Research: logging and connection settings."

  # A research run makes about 84 transactions (Phase 22), and the API and the
  # worker each hold a pool. This is the ceiling those pools are sized under;
  # the failure it prevents is a scale-out that exhausts connections rather
  # than capacity. LEAST() with the instance-class default keeps a small
  # instance from promising more than its memory allows.
  parameter {
    name  = "max_connections"
    value = "LEAST({DBInstanceClassMemory/9531392}, 500)"
    # Connections are allocated at startup, so this cannot be applied live.
    apply_method = "pending-reboot"
  }

  # Anything slower than a second is worth a line in the log. Faster than that
  # and the application's own `agent_runs` timings are the better instrument
  # (Phase 22 measured the whole pipeline from them).
  parameter {
    name  = "log_min_duration_statement"
    value = "1000"
  }

  parameter {
    name  = "log_connections"
    value = "1"
  }

  parameter {
    name  = "log_disconnections"
    value = "1"
  }

  # Statements are logged with their parameters stripped, because a research
  # query is user content and the database log is not a place for it.
  parameter {
    name  = "log_statement"
    value = "ddl"
  }

  lifecycle {
    create_before_destroy = true
  }
}

# The password is generated here rather than typed, and never leaves this
# state and the secret below. It is *in* the state, which is the trade-off
# taken knowingly: the alternative, RDS-managed master passwords, puts the
# value in an AWS-managed secret that Terraform cannot read - and the
# application needs the whole DSN as one string (DATABASE_URL), so something
# has to assemble it. The mitigation is that the state lives in an encrypted,
# versioned, access-controlled S3 bucket; see the backend note in versions.tf.
resource "random_password" "master" {
  length = 40
  # RDS refuses '/', '@', '"' and space in a master password, and a URL-unsafe
  # character here would produce a DSN that parses wrong rather than fails.
  override_special = "!#$%&*()-_=+[]{}<>:?"
}

resource "aws_db_instance" "this" {
  identifier = "${var.name_prefix}-postgres"

  engine         = "postgres"
  engine_version = var.engine_version
  instance_class = var.instance_class

  # gp3 rather than gp2: baseline IOPS no longer scale with volume size, so a
  # small database is not also a slow one.
  storage_type          = "gp3"
  allocated_storage     = var.allocated_storage
  max_allocated_storage = var.max_allocated_storage
  storage_encrypted     = true

  db_name  = var.database_name
  username = var.master_username
  password = random_password.master.result
  port     = 5432

  db_subnet_group_name   = aws_db_subnet_group.this.name
  vpc_security_group_ids = [var.security_group_id]
  parameter_group_name   = aws_db_parameter_group.this.name
  publicly_accessible    = false

  multi_az                = var.multi_az
  backup_retention_period = var.backup_retention_days
  backup_window           = "04:00-05:00"
  maintenance_window      = "sun:05:30-sun:06:30"
  copy_tags_to_snapshot   = true

  # Point-in-time recovery is what backup_retention_period buys; the final
  # snapshot is the one taken on the way out, and skipping it in production
  # means a destroy is unrecoverable.
  skip_final_snapshot       = var.skip_final_snapshot
  final_snapshot_identifier = var.skip_final_snapshot ? null : "${var.name_prefix}-final-${formatdate("YYYYMMDDhhmmss", timestamp())}"
  deletion_protection       = var.deletion_protection

  performance_insights_enabled          = var.performance_insights
  performance_insights_retention_period = var.performance_insights ? 7 : null
  monitoring_interval                   = var.monitoring_interval_sec
  monitoring_role_arn                   = var.monitoring_interval_sec > 0 ? aws_iam_role.monitoring[0].arn : null
  enabled_cloudwatch_logs_exports       = ["postgresql", "upgrade"]

  auto_minor_version_upgrade = true
  apply_immediately          = var.apply_immediately

  lifecycle {
    ignore_changes = [
      # Recomputed from `timestamp()` on every plan, which would otherwise
      # show a permanent diff and force replacement of nothing.
      final_snapshot_identifier,
    ]
  }

  tags = { Name = "${var.name_prefix}-postgres" }
}

# --- enhanced monitoring role ------------------------------------------------

data "aws_iam_policy_document" "monitoring_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["monitoring.rds.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "monitoring" {
  count = var.monitoring_interval_sec > 0 ? 1 : 0

  name               = "${var.name_prefix}-rds-monitoring"
  assume_role_policy = data.aws_iam_policy_document.monitoring_assume.json
}

resource "aws_iam_role_policy_attachment" "monitoring" {
  count = var.monitoring_interval_sec > 0 ? 1 : 0

  role       = aws_iam_role.monitoring[0].name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonRDSEnhancedMonitoringRole"
}
