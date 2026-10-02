# Private DNS names for services other tasks call directly.
#
# The API and the worker reach the embedding service by name rather than through
# a load balancer: it serves only traffic from inside the VPC, and an internal
# load balancer would cost more per month than the service it fronts. ECS
# registers each task's address here as it starts and removes it as it stops,
# so the name always resolves to tasks that are running.

resource "aws_service_discovery_private_dns_namespace" "this" {
  name        = "${var.name_prefix}.internal"
  description = "Service names inside the ${var.name_prefix} VPC."
  vpc         = var.vpc_id
}

resource "aws_service_discovery_service" "this" {
  for_each = toset(var.services)

  name = each.key

  dns_config {
    namespace_id = aws_service_discovery_private_dns_namespace.this.id

    # Short, because a task replaced in a deployment must stop being an answer
    # quickly: a client holding a stale address for a minute is a minute of
    # failed calls the gateway has to retry through.
    dns_records {
      ttl  = 10
      type = "A"
    }

    routing_policy = "MULTIVALUE"
  }

  # ECS reports health for the tasks it registers, from the container's own
  # health check, so a task still pulling its model is not an answer yet.
  health_check_custom_config {}
}
