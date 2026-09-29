terraform {
  required_providers {
    cloudflare = {
      source  = "cloudflare/cloudflare"
      version = "~> 5.24.0"
    }
  }
}

provider "cloudflare" {
  api_token = var.cloudflare_api_token
}

resource "cloudflare_dns_record" "apex" {
  zone_id = var.cloudflare_zone_id
  name    = "basel.engineering"
  type    = "A"
  content = var.origin_ip
  proxied = true
  ttl     = 1
}

# Cache Rules use the current Rulesets API, rather than legacy Page Rules.
resource "cloudflare_ruleset" "api_cache" {
  zone_id = var.cloudflare_zone_id
  name    = "Glassbox API cache bypass"
  kind    = "zone"
  phase   = "http_request_cache_settings"

  rules = [{
    ref         = "glassbox_api_cache_bypass"
    description = "Do not cache API responses or SSE streams"
    expression  = "(http.host eq \"basel.engineering\" and starts_with(http.request.uri.path, \"/api/\"))"
    action      = "set_cache_settings"
    action_parameters = {
      cache = false
    }
  }]
}
