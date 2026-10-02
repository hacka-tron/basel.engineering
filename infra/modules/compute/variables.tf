variable "vpc_id" {
  description = "VPC that contains the public subnet."
  type        = string
}

variable "public_subnet_id" {
  description = "Public subnet for the single k3s node."
  type        = string
}

variable "aws_region" {
  description = "AWS region used for the EC2 and Bedrock source endpoint."
  type        = string
  default     = "us-east-1"
}

variable "aws_account_id" {
  description = "AWS account that owns the instance and inference profiles."
  type        = string
  default     = "404379474987"
}

variable "ecr_repository_arn" {
  description = "ARN of the glassbox ECR repository, so the node can pull the application image."
  type        = string
}

variable "alert_email" {
  description = "Address the glassbox-alerts topic emails (status-check alarms). Already public on the site, so not a secret. AWS sends a confirmation link first; nothing is delivered until it is clicked."
  type        = string
}
