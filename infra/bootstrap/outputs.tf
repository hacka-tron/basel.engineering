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
