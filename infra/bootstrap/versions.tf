terraform {
  required_version = ">= 1.10.0, < 2.0.0"

  # State lives in the bucket this root creates, under its own key. The
  # bucket was created by a first apply with local state; the one-time move is
  # in README.md ("Moving state to S3"). Same bucket, region, encryption and
  # S3-native locking as infra/envs/prod/backend.tf. A backend block can't use
  # variables, so the bucket name is literal, as in prod.
  backend "s3" {
    bucket       = "glassbox-tfstate-404379474987-ab88985b66efc96f"
    key          = "bootstrap/terraform.tfstate"
    region       = "us-east-1"
    use_lockfile = true
    encrypt      = true
  }

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

provider "aws" {
  region              = var.aws_region
  allowed_account_ids = [var.aws_account_id]

  default_tags {
    tags = {
      project = "glassbox"
    }
  }
}
