terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.66.0"
    }
  }
}

data "aws_availability_zones" "available" {
  state = "available"
}

resource "aws_vpc" "glassbox" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = { Name = "glassbox" }
}

resource "aws_subnet" "public" {
  vpc_id                  = aws_vpc.glassbox.id
  cidr_block              = var.public_subnet_cidr
  availability_zone       = data.aws_availability_zones.available.names[0]
  map_public_ip_on_launch = false

  tags = { Name = "glassbox-public" }
}

resource "aws_internet_gateway" "glassbox" {
  vpc_id = aws_vpc.glassbox.id

  tags = { Name = "glassbox" }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.glassbox.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.glassbox.id
  }

  tags = { Name = "glassbox-public" }
}

resource "aws_route_table_association" "public" {
  subnet_id      = aws_subnet.public.id
  route_table_id = aws_route_table.public.id
}
