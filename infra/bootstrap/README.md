# Glassbox Terraform bootstrap

Creates the S3 Terraform state bucket and the GitHub Actions OIDC provider +
`glassbox-ci` deploy role that `infra/envs/prod` and CI use. **Already
applied to the real AWS account** (`404379474987`) — this is not a
placeholder module.

This root has no remote backend; its own `terraform.tfstate` stays local
because the bucket doesn't exist until after the first apply. That state
file is the only record of what exists here — keep it and any backups
private and outside Git, and always run `terraform plan` before changing
anything, since a diff against stale local state can propose destroying
real resources.

```sh
terraform init
terraform plan
terraform apply
terraform output state_bucket_name
terraform output ci_role_arn
```

The owner applies changes to this module manually with their own AWS CLI
credentials — CI cannot bootstrap itself, since the role it would assume
doesn't exist until this module has already run. `infra/envs/prod/backend.tf`
already references the real bucket this module created; `state_bucket_name`
only needs re-checking if this module is ever re-run against a fresh
account.

Future project IAM roles, policies, and instance profiles managed by CI must
use the `glassbox-` name prefix to match the CI role's permissions scope. CI
can only `iam:PassRole` matching roles to EC2, and its S3 access is limited
to this one state bucket — it has no access to any application data bucket.
