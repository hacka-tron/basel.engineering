# Glassbox Terraform bootstrap

Creates the S3 Terraform state bucket, the GitHub Actions OIDC provider, and
three roles: `glassbox-ci` (production apply, trusts the protected
`terraform-prod` environment), `glassbox-ci-plan` (read-only Terraform plan,
trusts the protected `terraform-plan` environment), and `glassbox-ci-release`
(pushes the application image to ECR, trusts the `release` environment).
**Already applied to the real AWS account** (`404379474987`) — this is not a
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
terraform output plan_role_arn
terraform output release_role_arn
```

The owner applies changes to this module manually with their own AWS CLI
credentials — CI cannot bootstrap itself, since the roles it would assume
don't exist until this module has already run. `infra/envs/prod/backend.tf`
already references the real bucket this module created; `state_bucket_name`
only needs re-checking if this module is ever re-run against a fresh
account.

After a bootstrap code change, the owner must review and apply this
local-state root manually before merging a workflow that depends on the
changed roles. `glassbox-ci-plan` can read production state and project SSM
parameters, and can write only the production state lockfile.
`glassbox-ci-release` can only push to the `glassbox` ECR repository.
`glassbox-ci` can apply infrastructure changes. GitHub environment approval
must be configured before any of these roles is used; see `infra/CI.md`.

All three roles' OIDC trust conditions use this account's actual `sub` claim
format (`repo:hacka-tron@14956857/basel.engineering@1394092219:...`), not the
plain `repo:owner/repo:...` format GitHub's docs lead with — this account has
GitHub's "immutable subject" feature on by default
(`gh api repos/hacka-tron/basel.engineering/actions/oidc/customization/sub`).
Confirmed the hard way: a real workflow run's first `AssumeRoleWithWebIdentity`
call failed until this was corrected. Any new OIDC-trusted role added here
must use `local.github_oidc_subject_prefix`, not a hardcoded plain-format
string.

Future project IAM roles, policies, and instance profiles managed by CI must
use the `glassbox-` name prefix to match the CI role's permissions scope. CI
can only `iam:PassRole` matching roles to EC2, and its S3 access is limited
to this one state bucket — it has no access to any application data bucket.

## Runbook and bootstrap-pipeline roles

`runbooks.tf` adds four more OIDC roles. `glassbox-ops-read` and
`glassbox-ops` are for `.github/workflows/ops.yml` (push-button node
operations through Terraform-managed SSM documents only).
`glassbox-bootstrap-plan` and `glassbox-bootstrap` are for
`.github/workflows/bootstrap.yml`, which plans and, after owner approval,
applies this root from CI once its state is in S3. Their trust conditions and
permissions, and the one-time manual apply that creates them, are in
`infra/CI.md` ("Runbooks", "Bootstrap via pipeline", "One-time owner
setup"). After that one apply, bootstrap changes go through the Bootstrap
workflow instead of a local `terraform apply`.

If this root's state ever has to be rebuilt by import, these roles need
importing too:

```sh
for r in glassbox-ops-read glassbox-ops glassbox-bootstrap-plan glassbox-bootstrap; do
  terraform import "aws_iam_role.runbook[\"$r\"]" "$r"
done
terraform import aws_iam_role_policy.ops_read glassbox-ops-read:glassbox-ops-read
terraform import aws_iam_role_policy.ops glassbox-ops:glassbox-ops
terraform import aws_iam_role_policy.bootstrap_plan glassbox-bootstrap-plan:glassbox-bootstrap-plan
terraform import aws_iam_role_policy.bootstrap glassbox-bootstrap:glassbox-bootstrap
```
