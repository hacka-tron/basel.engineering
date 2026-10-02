output "instance_id" {
  value = aws_instance.glassbox.id
}

output "elastic_ip" {
  value = aws_eip.glassbox.public_ip
}

output "security_group_id" {
  value = aws_security_group.glassbox.id
}

output "alerts_topic_arn" {
  value = aws_sns_topic.alerts.arn
}

output "snapshot_policy_id" {
  value = aws_dlm_lifecycle_policy.daily_root.id
}
