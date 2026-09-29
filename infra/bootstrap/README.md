# Glassbox Terraform bootstrap

This root module creates the S3 bucket for later Terraform state and a GitHub
Actions OIDC provider and `glassbox-ci` deploy role. The role trusts only
`hacka-tron/basel.engineering` workflow runs on `main`.

The owner applies this module manually once, using their configured AWS CLI
credentials. CI cannot apply its own bootstrap: the role it would assume does
not exist until this module has been applied. Run from `infra/bootstrap`:

```sh
terraform init
terraform plan
terraform apply
terraform output state_bucket_name
terraform output ci_role_arn
```

Review the plan before applying. This module has no remote backend block;
its own `terraform.tfstate` stays local because the bucket does not exist at
initialization time. Keep that state file and any backups private and backed
up outside Git. Retain it for future bootstrap changes so Terraform continues
to track the bucket, OIDC provider, and role.

Put `state_bucket_name` in the future `infra/envs/prod/backend.tf` as its
`bucket` value. That backend should use `use_lockfile = true`; no DynamoDB
table is needed. Configure the same bucket name as a GitHub Actions repository
variable (for the future workflow), and configure `ci_role_arn` as another
repository variable or secret for OIDC role assumption. The production
backend and workflow are separate future tasks.

The `main`-only OIDC trust does not authorize pull request workflow runs to
assume this role. The future PR plan workflow in DD1 §12 will need a separate
credential/trust design if it must access AWS state.

Future project IAM roles, policies, and instance profiles managed by CI must
use the `glassbox-` name prefix to match the CI role's permissions. CI can
pass only matching roles to EC2. The role's S3 access is limited to this state
bucket; it has no permissions for application data buckets.
