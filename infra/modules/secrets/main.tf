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

# HMAC key for services/glassbox/limits.py's client_ip_hash() — pseudonymizes
# visitor IPs for rate limiting (docs/DESIGN.md 7.1: no raw IPs are logged).
# Must be stable across pod restarts/redeploys, hence Terraform-managed like
# the MySQL password rather than generated ad hoc on the node.
resource "random_password" "ip_hash_salt" {
  length  = 64
  special = false
}

resource "aws_ssm_parameter" "ip_hash_salt" {
  name  = "/glassbox/ip_hash_salt"
  type  = "SecureString"
  tier  = "Standard"
  value = random_password.ip_hash_salt.result
}
