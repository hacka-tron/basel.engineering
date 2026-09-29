output "state_bucket_name" {
  description = "S3 bucket name for the future envs/prod backend configuration."
  value       = aws_s3_bucket.state.id
}

output "ci_role_arn" {
  description = "GitHub Actions role ARN for the future CI workflow."
  value       = aws_iam_role.ci.arn
}
