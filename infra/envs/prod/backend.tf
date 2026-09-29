terraform {
  backend "s3" {
    bucket       = "glassbox-tfstate-404379474987-ab88985b66efc96f"
    key          = "envs/prod/terraform.tfstate"
    region       = "us-east-1"
    use_lockfile = true
    encrypt      = true
  }
}
