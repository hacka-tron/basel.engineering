output "instance_id" {
  value = aws_instance.glassbox.id
}

output "elastic_ip" {
  value = aws_eip.glassbox.public_ip
}

output "security_group_id" {
  value = aws_security_group.glassbox.id
}
