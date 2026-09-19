# Alarms.
#
# A short list on purpose. The application already measures itself in detail -
# Prometheus on both processes, OpenTelemetry spans at every seam, a Grafana
# dashboard in infra/monitoring - and duplicating that in CloudWatch would
# produce two sources of truth that disagree during an incident. What is here
# is what only AWS can see: the infrastructure underneath the application, and
# the load balancer in front of it.
#
# Every alarm treats missing data as `notBreaching`. A service with no traffic
# emits no datapoints, and an alarm that fires because nothing happened is an
# alarm people mute.

locals {
  alarm_actions = var.alarm_topic_arn == "" ? [] : [var.alarm_topic_arn]
}

resource "aws_cloudwatch_metric_alarm" "alb_5xx" {
  alarm_name        = "${local.name_prefix}-alb-5xx"
  alarm_description = "The load balancer itself returned 5xx: no healthy target, or a target that would not answer."

  namespace   = "AWS/ApplicationELB"
  metric_name = "HTTPCode_ELB_5XX_Count"
  statistic   = "Sum"
  dimensions  = { LoadBalancer = module.alb.arn_suffix }

  comparison_operator = "GreaterThanThreshold"
  threshold           = 10
  period              = 60
  evaluation_periods  = 2
  treat_missing_data  = "notBreaching"

  alarm_actions = local.alarm_actions
  ok_actions    = local.alarm_actions
}

resource "aws_cloudwatch_metric_alarm" "api_unhealthy_targets" {
  alarm_name        = "${local.name_prefix}-api-unhealthy-targets"
  alarm_description = "API replicas are failing their health check."

  namespace   = "AWS/ApplicationELB"
  metric_name = "UnHealthyHostCount"
  statistic   = "Maximum"
  dimensions = {
    LoadBalancer = module.alb.arn_suffix
    TargetGroup  = module.alb.api_target_group_arn_suffix
  }

  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  period              = 60
  evaluation_periods  = 3
  treat_missing_data  = "notBreaching"

  alarm_actions = local.alarm_actions
  ok_actions    = local.alarm_actions
}

resource "aws_cloudwatch_metric_alarm" "database_cpu" {
  alarm_name        = "${local.name_prefix}-database-cpu"
  alarm_description = "Sustained database CPU. The usual cause is a query without an index, not load."

  namespace   = "AWS/RDS"
  metric_name = "CPUUtilization"
  statistic   = "Average"
  dimensions  = { DBInstanceIdentifier = module.database.identifier }

  comparison_operator = "GreaterThanThreshold"
  threshold           = 80
  period              = 300
  evaluation_periods  = 3
  treat_missing_data  = "notBreaching"

  alarm_actions = local.alarm_actions
  ok_actions    = local.alarm_actions
}

# Storage autoscaling raises the ceiling, so this fires only when growth
# outpaces it - which is the case worth waking someone for, because a full
# volume stops writes and a research run is mostly writes.
resource "aws_cloudwatch_metric_alarm" "database_storage" {
  alarm_name        = "${local.name_prefix}-database-storage"
  alarm_description = "Free storage below 10 GiB despite autoscaling."

  namespace   = "AWS/RDS"
  metric_name = "FreeStorageSpace"
  statistic   = "Minimum"
  dimensions  = { DBInstanceIdentifier = module.database.identifier }

  comparison_operator = "LessThanThreshold"
  threshold           = 10 * 1024 * 1024 * 1024
  period              = 300
  evaluation_periods  = 2
  treat_missing_data  = "notBreaching"

  alarm_actions = local.alarm_actions
  ok_actions    = local.alarm_actions
}

# Redis holds the queue and the cache under one eviction policy
# (`volatile-lru`, see modules/cache). Memory pressure evicts cache entries
# first, which is survivable; this alarm is the warning before it stops being.
resource "aws_cloudwatch_metric_alarm" "cache_memory" {
  alarm_name        = "${local.name_prefix}-cache-memory"
  alarm_description = "Redis is evicting: the cache is being trimmed and the queue is next."

  namespace   = "AWS/ElastiCache"
  metric_name = "DatabaseMemoryUsagePercentage"
  statistic   = "Average"
  dimensions  = { ReplicationGroupId = module.cache.replication_group_id }

  comparison_operator = "GreaterThanThreshold"
  threshold           = 85
  period              = 300
  evaluation_periods  = 2
  treat_missing_data  = "notBreaching"

  alarm_actions = local.alarm_actions
  ok_actions    = local.alarm_actions
}

# The worker at its ceiling is not an error - it is the system doing what it
# was asked - but it is the moment when queued runs start waiting, and the
# answer is a higher ceiling rather than a faster worker.
resource "aws_cloudwatch_metric_alarm" "worker_at_capacity" {
  alarm_name        = "${local.name_prefix}-worker-at-capacity"
  alarm_description = "The worker service has been at its maximum task count for fifteen minutes."

  namespace   = "ECS/ContainerInsights"
  metric_name = "RunningTaskCount"
  statistic   = "Average"
  dimensions = {
    ClusterName = module.ecs.cluster_name
    ServiceName = module.worker_service.service_name
  }

  comparison_operator = "GreaterThanOrEqualToThreshold"
  threshold           = var.worker_service.max_count
  period              = 300
  evaluation_periods  = 3
  treat_missing_data  = "notBreaching"

  alarm_actions = local.alarm_actions
  ok_actions    = local.alarm_actions
}
