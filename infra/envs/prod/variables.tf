variable "cloudflare_api_token" {
  description = "Zone-scoped token with DNS Edit, Cache Rules Edit, and Zone Read; supply through TF_VAR_cloudflare_api_token."
  type        = string
  sensitive   = true
}

variable "cloudflare_zone_id" {
  description = "Cloudflare zone ID for basel.engineering."
  type        = string
}
