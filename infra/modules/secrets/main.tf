terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.66.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.9.1"
    }
  }
}

resource "random_password" "mysql" {
  length  = 32
  special = false # Alphanumeric is safe in MySQL URLs and shell environment values.
}

resource "aws_ssm_parameter" "mysql_password" {
  name  = "/glassbox/mysql/password"
  type  = "SecureString"
  tier  = "Standard"
  value = random_password.mysql.result
}
