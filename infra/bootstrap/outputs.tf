output "state_bucket_name" {
  description = "S3 bucket name for the future envs/prod backend configuration."
  value       = aws_s3_bucket.state.id
}

output "ci_role_arn" {
  description = "GitHub Actions role ARN for infrastructure changes."
  value       = aws_iam_role.ci.arn
}

output "release_role_arn" {
  description = "GitHub Actions role ARN for pushing the application image to ECR."
  value       = aws_iam_role.release.arn
}
