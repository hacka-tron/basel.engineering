output "vpc_id" {
  value = aws_vpc.glassbox.id
}

output "public_subnet_id" {
  value = aws_subnet.public.id
}

output "vpc_cidr" {
  value = aws_vpc.glassbox.cidr_block
}
