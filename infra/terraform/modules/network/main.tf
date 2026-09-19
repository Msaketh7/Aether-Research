# VPC, subnets and egress.
#
# Two tiers, which is the whole design: the load balancer is the only thing in
# a public subnet, and everything that holds or processes data is in a private
# one with no route in. That is the trust boundary docs/threat-model.md draws
# between "the internet" and "the platform", expressed as routing rather than
# as a security group anyone can widen.

locals {
  azs = slice(data.aws_availability_zones.available.names, 0, var.availability_zone_count)

  # /20 per subnet out of a /16: 4,094 usable addresses each, which matters
  # because every Fargate task takes one ENI and therefore one address. A /24
  # would cap the worker service at a couple of hundred tasks.
  public_subnets  = [for i in range(var.availability_zone_count) : cidrsubnet(var.vpc_cidr, 4, i)]
  private_subnets = [for i in range(var.availability_zone_count) : cidrsubnet(var.vpc_cidr, 4, i + 8)]

  nat_gateway_count = var.single_nat_gateway ? 1 : var.availability_zone_count
}

data "aws_availability_zones" "available" {
  state = "available"
}

resource "aws_vpc" "this" {
  cidr_block = var.vpc_cidr
  # Both required by the RDS and ElastiCache endpoints, which are reached by
  # name; without them the application resolves nothing and fails at startup
  # with a DNS error that says nothing about VPC settings.
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = { Name = "${var.name_prefix}-vpc" }
}

resource "aws_internet_gateway" "this" {
  vpc_id = aws_vpc.this.id
  tags   = { Name = "${var.name_prefix}-igw" }
}

resource "aws_subnet" "public" {
  count = var.availability_zone_count

  vpc_id            = aws_vpc.this.id
  cidr_block        = local.public_subnets[count.index]
  availability_zone = local.azs[count.index]

  # Tasks here would be directly addressable from the internet. Nothing is
  # placed here except the load balancer and the NAT gateways, and this stays
  # false so that a service accidentally placed in a public subnet has no
  # public address and fails loudly rather than becoming reachable.
  map_public_ip_on_launch = false

  tags = {
    Name = "${var.name_prefix}-public-${local.azs[count.index]}"
    Tier = "public"
  }
}

resource "aws_subnet" "private" {
  count = var.availability_zone_count

  vpc_id            = aws_vpc.this.id
  cidr_block        = local.private_subnets[count.index]
  availability_zone = local.azs[count.index]

  tags = {
    Name = "${var.name_prefix}-private-${local.azs[count.index]}"
    Tier = "private"
  }
}

# --- egress ------------------------------------------------------------------

resource "aws_eip" "nat" {
  count  = local.nat_gateway_count
  domain = "vpc"
  tags   = { Name = "${var.name_prefix}-nat-${count.index}" }
}

resource "aws_nat_gateway" "this" {
  count = local.nat_gateway_count

  allocation_id = aws_eip.nat[count.index].id
  subnet_id     = aws_subnet.public[count.index].id

  tags       = { Name = "${var.name_prefix}-nat-${count.index}" }
  depends_on = [aws_internet_gateway.this]
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.this.id
  tags   = { Name = "${var.name_prefix}-public" }
}

resource "aws_route" "public_default" {
  route_table_id         = aws_route_table.public.id
  destination_cidr_block = "0.0.0.0/0"
  gateway_id             = aws_internet_gateway.this.id
}

resource "aws_route_table_association" "public" {
  count = var.availability_zone_count

  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

# One route table per private subnet even when they share a NAT gateway, so
# that moving to one gateway per AZ is a variable change rather than a
# re-association of every subnet.
resource "aws_route_table" "private" {
  count = var.availability_zone_count

  vpc_id = aws_vpc.this.id
  tags   = { Name = "${var.name_prefix}-private-${local.azs[count.index]}" }
}

resource "aws_route" "private_default" {
  count = var.availability_zone_count

  route_table_id         = aws_route_table.private[count.index].id
  destination_cidr_block = "0.0.0.0/0"
  nat_gateway_id         = aws_nat_gateway.this[var.single_nat_gateway ? 0 : count.index].id
}

resource "aws_route_table_association" "private" {
  count = var.availability_zone_count

  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private[count.index].id
}

# --- endpoints ---------------------------------------------------------------

# S3 through a gateway endpoint rather than the NAT gateway. Every artifact the
# platform stores and every one it reads back goes this way, and NAT charges
# per gigabyte processed in both directions - so this is a correctness-neutral
# change that removes the largest data-transfer line from the bill. It is also
# one less dependency on the NAT gateway being healthy.
resource "aws_vpc_endpoint" "s3" {
  vpc_id            = aws_vpc.this.id
  service_name      = "com.amazonaws.${var.aws_region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = aws_route_table.private[*].id

  tags = { Name = "${var.name_prefix}-s3" }
}

# --- flow logs ---------------------------------------------------------------

resource "aws_cloudwatch_log_group" "flow_logs" {
  count = var.flow_logs_enabled ? 1 : 0

  name              = "/aws/vpc/${var.name_prefix}/flow-logs"
  retention_in_days = var.log_retention_days
}

data "aws_iam_policy_document" "flow_logs_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["vpc-flow-logs.amazonaws.com"]
    }
  }
}

data "aws_iam_policy_document" "flow_logs" {
  statement {
    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents",
      "logs:DescribeLogStreams",
    ]
    resources = var.flow_logs_enabled ? ["${aws_cloudwatch_log_group.flow_logs[0].arn}:*"] : ["*"]
  }
}

resource "aws_iam_role" "flow_logs" {
  count = var.flow_logs_enabled ? 1 : 0

  name               = "${var.name_prefix}-flow-logs"
  assume_role_policy = data.aws_iam_policy_document.flow_logs_assume.json
}

resource "aws_iam_role_policy" "flow_logs" {
  count = var.flow_logs_enabled ? 1 : 0

  name   = "write-flow-logs"
  role   = aws_iam_role.flow_logs[0].id
  policy = data.aws_iam_policy_document.flow_logs.json
}

# REJECT only, not ALL. The threat model's question of this log is "what tried
# to leave that should not have" - the SSRF guard's last line of defence - and
# accepted traffic is the entire normal workload, which would be a large
# CloudWatch bill for a signal already in the application's own telemetry.
resource "aws_flow_log" "this" {
  count = var.flow_logs_enabled ? 1 : 0

  vpc_id               = aws_vpc.this.id
  traffic_type         = "REJECT"
  iam_role_arn         = aws_iam_role.flow_logs[0].arn
  log_destination      = aws_cloudwatch_log_group.flow_logs[0].arn
  log_destination_type = "cloud-watch-logs"

  tags = { Name = "${var.name_prefix}-flow-logs" }
}
