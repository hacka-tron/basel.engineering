# Status-check alarms on the node, with an email topic.
#
#   glassbox-node-recover  StatusCheckFailed_System (AWS's side: host
#                          hardware, power, network) for 2 of 2 minutes:
#                          EC2 "recover" moves the instance to healthy
#                          hardware. Same instance ID, Elastic IP, private IP
#                          and EBS root volume, so nothing on the disk is lost.
#   glassbox-node-reboot   StatusCheckFailed_Instance (our side: kernel hung,
#                          out of memory, network stack down) for 3 of 3
#                          minutes: EC2 reboot. Same as "Ops · Reboot node",
#                          without waiting for someone to press it.
#
# The periods differ so the two actions never race (AWS guidance). Status
# check metrics are free and published every minute even with basic
# monitoring. Both alarms email the glassbox-alerts topic when they fire and
# when they clear.
#
# Missing data stays "missing" (no state change): a stopped instance or a
# CloudWatch gap must not trigger a reboot. Don't test these by forcing the
# alarm state (set-alarm-state): the action would really reboot or recover
# the node. See infra/CI.md "Alarms and uptime probe".
#
# These alarms watch one fixed instance ID. If the node is ever replaced by a
# new instance, Terraform recreates them for the new ID in the same apply.

resource "aws_sns_topic" "alerts" {
  name = "glassbox-alerts"
  # Not KMS-encrypted on purpose: CloudWatch alarms can't publish to a topic
  # encrypted with the AWS managed aws/sns key, and the messages hold only
  # alarm names and states.
}

# Email subscriptions start as "pending confirmation": the owner must click
# the link in the AWS email before anything is delivered. Terraform can't
# delete a pending subscription; if the address changes before it is
# confirmed, the old pending one lingers until AWS expires it (about 3
# days). Harmless.
resource "aws_sns_topic_subscription" "alerts_email" {
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

locals {
  status_alarms = {
    recover = {
      metric             = "StatusCheckFailed_System"
      evaluation_periods = 2
      action             = "arn:aws:automate:${var.aws_region}:ec2:recover"
      description        = "Host-side status check failed 2 of 2 minutes: EC2 recovers the instance onto healthy hardware (same ID, IP and disk)."
    }
    reboot = {
      metric             = "StatusCheckFailed_Instance"
      evaluation_periods = 3
      action             = "arn:aws:automate:${var.aws_region}:ec2:reboot"
      description        = "Instance status check failed 3 of 3 minutes: EC2 reboots the instance (like Ops - Reboot node)."
    }
  }
}

resource "aws_cloudwatch_metric_alarm" "status" {
  for_each = local.status_alarms

  alarm_name          = "glassbox-node-${each.key}"
  alarm_description   = each.value.description
  namespace           = "AWS/EC2"
  metric_name         = each.value.metric
  dimensions          = { InstanceId = aws_instance.glassbox.id }
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = each.value.evaluation_periods
  datapoints_to_alarm = each.value.evaluation_periods
  comparison_operator = "GreaterThanOrEqualToThreshold"
  threshold           = 1
  treat_missing_data  = "missing"

  alarm_actions = [each.value.action, aws_sns_topic.alerts.arn]
  ok_actions    = [aws_sns_topic.alerts.arn]
}
