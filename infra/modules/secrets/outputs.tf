output "mysql_password_parameter_name" {
  value = aws_ssm_parameter.mysql_password.name
}

output "ip_hash_salt_parameter_name" {
  value = aws_ssm_parameter.ip_hash_salt.name
}
