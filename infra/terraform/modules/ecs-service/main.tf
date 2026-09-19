# One Fargate service. Used three times: api, web, worker.
#
# The three differ in ways that are all inputs - image, command, port, whether
# a load balancer is in front, what the task role may do, what it scales on -
# so they share this module rather than three near-copies that drift. The
# worker in particular is the same *image* as the API (ADR 0001) with a
# different command, and this is where that stops being a claim.

locals {
  # A task with no port is not behind a load balancer; a task with one is.
  # Everything that follows keys off this rather than off a separate flag that
  # could disagree with it.
  load_balanced = var.target_group_arn != ""

  log_group = "/ecs/${var.name_prefix}/${var.service_name}"
}

resource "aws_cloudwatch_log_group" "this" {
  name              = local.log_group
  retention_in_days = var.log_retention_days

  tags = { Name = "${var.name_prefix}-${var.service_name}" }
}

# --- task role ---------------------------------------------------------------

data "aws_iam_policy_document" "assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "task" {
  name               = "${var.name_prefix}-${var.service_name}-task"
  assume_role_policy = data.aws_iam_policy_document.assume.json
  description        = "What the ${var.service_name} process itself may do."
}

# Deliberately empty for the web service, which reads nothing from AWS. An
# inline policy created unconditionally would be an empty statement list, which
# IAM rejects, so the count is the check.
resource "aws_iam_role_policy" "task" {
  count = var.task_policy_json == "" ? 0 : 1

  name   = "${var.service_name}-permissions"
  role   = aws_iam_role.task.id
  policy = var.task_policy_json
}

# ECS Exec, off by default. It opens an interactive shell into a running task,
# which is the right tool at 3am and the wrong thing to leave enabled: the
# worker's environment holds every provider key.
resource "aws_iam_role_policy" "exec" {
  count = var.enable_execute_command ? 1 : 0

  name = "ssm-session"
  role = aws_iam_role.task.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "ssmmessages:CreateControlChannel",
        "ssmmessages:CreateDataChannel",
        "ssmmessages:OpenControlChannel",
        "ssmmessages:OpenDataChannel",
      ]
      Resource = "*"
    }]
  })
}

# --- task definition ---------------------------------------------------------

resource "aws_ecs_task_definition" "this" {
  family                   = "${var.name_prefix}-${var.service_name}"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.cpu
  memory                   = var.memory
  execution_role_arn       = var.execution_role_arn
  task_role_arn            = aws_iam_role.task.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = var.cpu_architecture
  }

  container_definitions = jsonencode([
    merge(
      {
        name      = var.service_name
        image     = var.image
        essential = true

        # Environment splits in two on one rule: a value that would be a
        # finding in a log goes in `secrets` and is fetched by the agent at
        # start; everything else is plain. There is no third category, and
        # nothing is passed as a command-line argument, where it would be
        # visible in a process listing inside the container.
        environment = [
          for key in sort(keys(var.environment)) : {
            name  = key
            value = tostring(var.environment[key])
          }
        ]

        secrets = [
          for key in sort(keys(var.secrets)) : {
            name      = key
            valueFrom = var.secrets[key]
          }
        ]

        logConfiguration = {
          logDriver = "awslogs"
          options = {
            "awslogs-group"         = aws_cloudwatch_log_group.this.name
            "awslogs-region"        = var.aws_region
            "awslogs-stream-prefix" = var.service_name
            # Non-blocking, with a buffer. The blocking default makes a write
            # to stdout wait on CloudWatch, so a throttled log API becomes
            # application latency - and this application logs a line per node
            # execution, per tool call and per model call. The trade is that a
            # sustained overflow drops log lines rather than slowing the run,
            # which is the right way round: the durable record is in Postgres.
            "mode"            = "non-blocking"
            "max-buffer-size" = "4m"
          }
        }

        # Containers in a Fargate task share a network namespace but not a
        # filesystem, and the image already runs as uid 10001. Read-only root
        # is not set: the document parser writes its temporary files to disk,
        # and a tmpfs mount sized for a 25 MiB artifact would have to be
        # guessed rather than measured.
        user = "10001:10001"

        stopTimeout = var.stop_timeout_seconds
      },
      var.command == null ? {} : { command = var.command },
      # The worker has a port too - its Prometheus endpoint - it just has no
      # target group. So the mapping follows the port, not the load balancer.
      var.container_port > 0 ? {
        portMappings = [{
          containerPort = var.container_port
          protocol      = "tcp"
        }]
      } : {},
    )
  ])

  tags = { Name = "${var.name_prefix}-${var.service_name}" }
}

# --- service -----------------------------------------------------------------

resource "aws_ecs_service" "this" {
  name            = "${var.name_prefix}-${var.service_name}"
  cluster         = var.cluster_id
  task_definition = aws_ecs_task_definition.this.arn
  desired_count   = var.desired_count
  launch_type     = "FARGATE"

  enable_execute_command = var.enable_execute_command
  propagate_tags         = "SERVICE"

  network_configuration {
    subnets = var.subnet_ids
    # Private subnets throughout, so a task reaches the internet through the
    # NAT gateway and has no address of its own.
    assign_public_ip = false
    security_groups  = [var.security_group_id]
  }

  dynamic "load_balancer" {
    for_each = local.load_balanced ? [1] : []
    content {
      target_group_arn = var.target_group_arn
      container_name   = var.service_name
      container_port   = var.container_port
    }
  }

  # Without this the service can fail its first deployment: the task starts,
  # the target group has not yet seen two consecutive healthy checks, and ECS
  # kills it as unhealthy. The grace period is the application's own startup -
  # the API imports about 6 s of Python before it listens.
  health_check_grace_period_seconds = local.load_balanced ? var.health_check_grace_period : null

  # A rolling deployment that can never drop below capacity: 100% minimum
  # healthy, 200% maximum, so new tasks start before old ones stop. The
  # circuit breaker is what makes a bad image a rollback rather than an
  # outage - ECS stops replacing tasks and restores the previous definition.
  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  lifecycle {
    # The deploy workflow updates the image by registering a new task
    # definition revision, so Terraform must not treat that as drift and roll
    # it back on the next unrelated apply. Terraform owns the shape of the
    # service; the pipeline owns which build is running in it.
    ignore_changes = [task_definition, desired_count]
  }

  tags = { Name = "${var.name_prefix}-${var.service_name}" }
}

# --- autoscaling -------------------------------------------------------------

resource "aws_appautoscaling_target" "this" {
  count = var.autoscaling_enabled ? 1 : 0

  service_namespace  = "ecs"
  resource_id        = "service/${var.cluster_name}/${aws_ecs_service.this.name}"
  scalable_dimension = "ecs:service:DesiredCount"
  min_capacity       = var.min_count
  max_capacity       = var.max_count
}

resource "aws_appautoscaling_policy" "cpu" {
  count = var.autoscaling_enabled ? 1 : 0

  name               = "${var.name_prefix}-${var.service_name}-cpu"
  policy_type        = "TargetTrackingScaling"
  service_namespace  = aws_appautoscaling_target.this[0].service_namespace
  resource_id        = aws_appautoscaling_target.this[0].resource_id
  scalable_dimension = aws_appautoscaling_target.this[0].scalable_dimension

  target_tracking_scaling_policy_configuration {
    predefined_metric_specification {
      predefined_metric_type = "ECSServiceAverageCPUUtilization"
    }
    target_value = var.cpu_target_percent
    # Out fast, in slow. Scaling in during a lull and immediately back out
    # costs a cold start on every task; the asymmetry is deliberate.
    scale_out_cooldown = 60
    scale_in_cooldown  = 300
  }
}

# Request concurrency, for the services that answer requests. ALBRequestCount
# PerTarget is the metric TDD section 21 names for the API, and it is a better
# signal than CPU for a process that spends its time waiting on Postgres.
resource "aws_appautoscaling_policy" "requests" {
  count = var.autoscaling_enabled && var.requests_per_target_target > 0 ? 1 : 0

  name               = "${var.name_prefix}-${var.service_name}-requests"
  policy_type        = "TargetTrackingScaling"
  service_namespace  = aws_appautoscaling_target.this[0].service_namespace
  resource_id        = aws_appautoscaling_target.this[0].resource_id
  scalable_dimension = aws_appautoscaling_target.this[0].scalable_dimension

  target_tracking_scaling_policy_configuration {
    predefined_metric_specification {
      predefined_metric_type = "ALBRequestCountPerTarget"
      resource_label         = var.alb_resource_label
    }
    target_value       = var.requests_per_target_target
    scale_out_cooldown = 60
    scale_in_cooldown  = 300
  }
}

# Queue depth, for the worker.
#
# Off by default, and the reason is that the metric does not exist yet in
# CloudWatch. `research_queue_depth` is a Prometheus gauge
# (app/observability/metrics.py), scraped by the Prometheus in
# infra/monitoring - nothing publishes it to CloudWatch, so an autoscaling
# policy pointed at it would silently never fire, which is worse than not
# having one. Enabling this needs a metric publisher first; the policy is here
# so that the shape of the decision is reviewable, not so that it can be
# switched on and believed.
resource "aws_appautoscaling_policy" "queue_depth" {
  count = var.autoscaling_enabled && var.queue_depth_target > 0 ? 1 : 0

  name               = "${var.name_prefix}-${var.service_name}-queue-depth"
  policy_type        = "TargetTrackingScaling"
  service_namespace  = aws_appautoscaling_target.this[0].service_namespace
  resource_id        = aws_appautoscaling_target.this[0].resource_id
  scalable_dimension = aws_appautoscaling_target.this[0].scalable_dimension

  target_tracking_scaling_policy_configuration {
    customized_metric_specification {
      metric_name = "research_queue_depth"
      namespace   = var.metric_namespace
      statistic   = "Average"
    }
    target_value       = var.queue_depth_target
    scale_out_cooldown = 60
    # Five minutes, and the reason is the lease: a worker that is stopped
    # mid-run hands the run back, and it is then re-dispatched after
    # WORKER_QUEUED_GRACE_SECONDS. Scaling in aggressively converts a quiet
    # period into a queue of re-dispatched work.
    scale_in_cooldown = 300
  }
}
