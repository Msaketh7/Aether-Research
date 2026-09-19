output "vpc_id" {
  description = "The VPC every other module attaches to."
  value       = aws_vpc.this.id
}

output "vpc_cidr" {
  description = "The VPC's CIDR, used by security groups that allow VPC-internal traffic."
  value       = aws_vpc.this.cidr_block
}

output "public_subnet_ids" {
  description = "Subnets for the load balancer. Nothing else belongs here."
  value       = aws_subnet.public[*].id
}

output "private_subnet_ids" {
  description = "Subnets for tasks, the database and the cache."
  value       = aws_subnet.private[*].id
}

output "availability_zones" {
  description = "The AZs in use, in the same order as the subnet lists."
  value       = local.azs
}
