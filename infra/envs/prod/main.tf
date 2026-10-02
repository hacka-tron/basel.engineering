provider "aws" {
  region              = "us-east-1"
  allowed_account_ids = ["404379474987"]

  default_tags {
    tags = { project = "glassbox" }
  }
}

module "network" {
  source = "../../modules/network"
}

module "compute" {
  source             = "../../modules/compute"
  vpc_id             = module.network.vpc_id
  public_subnet_id   = module.network.public_subnet_id
  ecr_repository_arn = module.registry.repository_arn
  # Status-check alarm emails (alarms.tf). Public on the site already.
  alert_email = "baselmabdelrahman@gmail.com"

  depends_on = [module.network, module.registry]
}

module "secrets" {
  source = "../../modules/secrets"
}

module "registry" {
  source = "../../modules/registry"
}

# Runbook actions (glassbox-ops-* SSM documents) that .github/workflows/ops.yml
# runs on the node. See infra/CI.md "Runbooks".
module "ops" {
  source = "../../modules/ops"
}

module "edge" {
  source               = "../../modules/edge"
  cloudflare_api_token = var.cloudflare_api_token
  cloudflare_zone_id   = var.cloudflare_zone_id
  origin_ip            = module.compute.elastic_ip
}
