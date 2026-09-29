variable "aws_account_id" {
  description = "AWS account that owns the Glassbox infrastructure."
  type        = string
  default     = "404379474987"
}

variable "aws_region" {
  description = "AWS region for the state bucket and later production infrastructure."
  type        = string
  default     = "us-east-1"
}
