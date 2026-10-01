output "state_bucket_name" {
  description = "S3 bucket name for the future envs/prod backend configuration."
  value       = aws_s3_bucket.state.id
}

output "ci_role_arn" {
  description = "GitHub Actions role ARN for the protected production apply job."
  value       = aws_iam_role.ci.arn
}

output "plan_role_arn" {
  description = "GitHub Actions role ARN for protected Terraform plan jobs."
  value       = aws_iam_role.plan.arn
}

output "release_role_arn" {
  description = "GitHub Actions role ARN for pushing the application image to ECR."
  value       = aws_iam_role.release.arn
}

output "runbook_role_arns" {
  description = "GitHub Actions role ARNs for ops.yml (glassbox-ops-read, glassbox-ops) and bootstrap.yml (glassbox-bootstrap-plan, glassbox-bootstrap)."
  value       = { for name, role in aws_iam_role.runbook : name => role.arn }
}
