output "instance_id" {
  value = module.compute.instance_id
}

output "elastic_ip" {
  value = module.compute.elastic_ip
}

output "mysql_password_parameter_name" {
  value = module.secrets.mysql_password_parameter_name
}

output "ecr_repository_url" {
  value = module.registry.repository_url
}

output "ops_document_names" {
  value = module.ops.document_names
}
