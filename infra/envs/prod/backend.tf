terraform {
  backend "s3" {
    # TODO: replace with the real bucket name from `terraform output state_bucket_name` in infra/bootstrap.
    bucket       = "REPLACE_WITH_BOOTSTRAP_STATE_BUCKET_NAME"
    key          = "envs/prod/terraform.tfstate"
    region       = "us-east-1"
    use_lockfile = true
    encrypt      = true
  }
}
