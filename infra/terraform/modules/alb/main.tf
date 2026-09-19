# The public entry point.
#
# One load balancer, two target groups, one path rule. The frontend is the
# default target and `/api/*` goes to the API, which is what makes
# NEXT_PUBLIC_API_BASE_URL a *relative* path in the built image: the browser
# resolves it against the page's own origin, the request is same-origin, the
# session cookie is first-party and CORS never enters into it. One image then
# serves every environment, because it contains no hostname.

resource "aws_lb" "this" {
  name               = "${var.name_prefix}-alb"
  load_balancer_type = "application"
  internal           = false
  subnets            = var.public_subnet_ids
  security_groups    = [var.security_group_id]

  # A research request can hold an SSE connection open for the whole run;
  # SSE_MAX_CONNECTION_SECONDS is 900 and the load balancer must outlast it, or
  # the stream dies at 60 seconds and the frontend reconnects in a loop for
  # reasons nothing in the application logs.
  idle_timeout = var.idle_timeout_seconds

  drop_invalid_header_fields = true
  enable_deletion_protection = var.deletion_protection

  # The client address the API resolves is taken from X-Forwarded-For through
  # exactly TRUSTED_PROXY_HOPS declared hops (app/security/forwarded.py). This
  # load balancer is that one hop, and it appends rather than replaces, which
  # is what makes the count meaningful.
  xff_header_processing_mode = "append"

  dynamic "access_logs" {
    for_each = var.access_logs_bucket == "" ? [] : [1]
    content {
      bucket  = var.access_logs_bucket
      prefix  = var.name_prefix
      enabled = true
    }
  }

  tags = { Name = "${var.name_prefix}-alb" }
}

# --- target groups -----------------------------------------------------------

resource "aws_lb_target_group" "web" {
  name        = "${var.name_prefix}-web"
  port        = var.web_port
  protocol    = "HTTP"
  target_type = "ip"
  vpc_id      = var.vpc_id

  health_check {
    path                = var.web_health_check_path
    matcher             = "200"
    interval            = 15
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }

  # Long enough for an in-flight page render, short enough that a deploy does
  # not wait on idle keep-alive connections.
  deregistration_delay = 30

  lifecycle {
    create_before_destroy = true
  }

  tags = { Name = "${var.name_prefix}-web" }
}

resource "aws_lb_target_group" "api" {
  name        = "${var.name_prefix}-api"
  port        = var.api_port
  protocol    = "HTTP"
  target_type = "ip"
  vpc_id      = var.vpc_id

  health_check {
    path                = var.api_health_check_path
    matcher             = "200"
    interval            = 15
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }

  # Longer than the web group, and for one reason: an SSE connection is a
  # request that has not finished. Draining a task that is streaming a run's
  # progress cuts the stream; the frontend recovers by reconnecting with
  # Last-Event-ID, but a deploy should not need that to be exercised on every
  # replica. Two minutes covers the reconnect, not the whole run.
  deregistration_delay = 120

  lifecycle {
    create_before_destroy = true
  }

  tags = { Name = "${var.name_prefix}-api" }
}

# --- listeners ---------------------------------------------------------------

locals {
  https_enabled = var.certificate_arn != ""
}

resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.this.arn
  port              = 80
  protocol          = "HTTP"

  # With a certificate this listener exists only to send browsers to the other
  # one. Without one it serves the site, which is the state a fresh account is
  # in before DNS exists - reachable, and not sign-in-able, because the session
  # cookie carries Secure outside local and test.
  dynamic "default_action" {
    for_each = local.https_enabled ? [1] : []
    content {
      type = "redirect"
      redirect {
        port        = "443"
        protocol    = "HTTPS"
        status_code = "HTTP_301"
      }
    }
  }

  dynamic "default_action" {
    for_each = local.https_enabled ? [] : [1]
    content {
      type             = "forward"
      target_group_arn = aws_lb_target_group.web.arn
    }
  }
}

resource "aws_lb_listener" "https" {
  count = local.https_enabled ? 1 : 0

  load_balancer_arn = aws_lb.this.arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = var.ssl_policy
  certificate_arn   = var.certificate_arn

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.web.arn
  }
}

# The one rule. `/api/*` matches the versioned surface the frontend calls and
# nothing else: the probes (/health, /ready) and the Prometheus endpoint
# (/metrics) are deliberately *not* routed here, so they are reachable from the
# target group and from inside the VPC but not from the internet.
resource "aws_lb_listener_rule" "api" {
  listener_arn = local.https_enabled ? aws_lb_listener.https[0].arn : aws_lb_listener.http.arn
  priority     = 100

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.api.arn
  }

  condition {
    path_pattern {
      values = ["/api/*"]
    }
  }
}
