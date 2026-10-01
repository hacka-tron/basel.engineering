# Glassbox Terraform bootstrap

Creates the S3 Terraform state bucket, the GitHub Actions OIDC provider, and
three roles: `glassbox-ci` (production apply, trusts the protected
`terraform-prod` environment), `glassbox-ci-plan` (read-only Terraform plan,
trusts the protected `terraform-plan` environment), and `glassbox-ci-release`
(pushes the application image to ECR, trusts the `release` environment).
**Already applied to the real AWS account** (`404379474987`) — this is not a
placeholder module.

This root's state is moving from a local `terraform.tfstate` to the bucket it
created, under the key `bootstrap/terraform.tfstate` (see "Moving state to S3"
below). Until the owner runs that one-time migration, the local file is still
the only record of what exists here — keep it and any backups private and
outside Git, and always run `terraform plan` before changing anything, since a
diff against stale state can propose destroying real resources. After the
migration the S3 object is the source of truth (the bucket is versioned,
encrypted, and protected against deletion).

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

## Moving state to S3 (one-time, owner only)

`versions.tf` now has a `backend "s3"` block (same bucket, region, encryption
and `use_lockfile = true` as `infra/envs/prod/backend.tf`, key
`bootstrap/terraform.tfstate`). Bootstrap stays human-applied: CI cannot read
or write this key. The bucket is `glassbox-tfstate-404379474987-ab88985b66efc96f`.

This happens in two phases, because Terraform must be re-initialized as soon
as the checkout contains the new `backend "s3"` block and refuses to plan
until it is. The local-state check has to happen **before** this PR is on
`main`.

**Phase A: before this PR merges (local backend).** Pending bootstrap
changes, such as PR #55's SSM permissions (branch `fix/zram-swap`), must be
merged to `main` and applied from local state first. Do not apply #55 from its
branch and then migrate from a `main` that lacks it; the next plan would
remove those permissions.

1. `git checkout main && git pull` (this PR not yet merged), then
   `cd infra/bootstrap`. `terraform plan` (then `terraform apply` if it shows
   the expected pending changes) must end with a plan showing **no changes**.
   Any unexplained diff means the local state and `main` disagree; resolve it
   first and do not migrate.

**Phase B: after this PR merges (switch to S3).**

2. `git pull` so the checkout has the `backend "s3"` block. Do not run
   `plan` yet; it would fail with "Backend initialization required".
3. Back up the local state privately, before touching the backend:
   `cp terraform.tfstate ~/glassbox-bootstrap-state-backup-$(date +%F).tfstate && chmod 600 ~/glassbox-bootstrap-state-backup-$(date +%F).tfstate`
4. `terraform init -migrate-state`. Terraform sees the new backend and the
   existing local `terraform.tfstate` and asks whether to copy it to S3.
   Answer `yes`. It uploads the state to `bootstrap/terraform.tfstate`; it does
   not change any AWS resource.
5. `terraform plan`. It must show only the expected changes:
   - `aws_s3_bucket_policy.state` will be created (TLS-only, DenyDeleteBucket,
     CI roles denied on `bootstrap/*`);
   - `aws_iam_role_policy.ci` and `aws_iam_role_policy.plan` will be updated
     in place (state S3 access narrowed from the whole bucket to `envs/prod/*`,
     `s3:ListBucket` restricted by prefix).
   Anything else, especially a destroy or replace, means stop and investigate.
   `prevent_destroy` on the bucket and `random_id` adds no plan diff.
6. `terraform apply`.
7. Verify: `aws s3 ls s3://glassbox-tfstate-404379474987-ab88985b66efc96f/bootstrap/`
   should list `terraform.tfstate`. Run `terraform plan` once more: no changes.
8. Delete the local `terraform.tfstate` and `terraform.tfstate.backup` from the
   repo directory (the private backup from step 3 is your copy; keep it until
   you are comfortable). Never commit them.

If the state or bucket is ever lost, recreate the bucket's contents from
versions (the bucket is versioned) first. If the bucket itself is gone, recreate
it, then re-import the resources into an empty state (`terraform init`, then):

```sh
B=glassbox-tfstate-404379474987-ab88985b66efc96f
terraform import random_id.state_bucket q4iYW2bvyW8  # base64url of the 8-byte id; hex is ab88985b66efc96f
terraform import aws_s3_bucket.state $B
terraform import aws_s3_bucket_versioning.state $B
terraform import aws_s3_bucket_server_side_encryption_configuration.state $B
terraform import aws_s3_bucket_public_access_block.state $B
terraform import aws_s3_bucket_policy.state $B
terraform import aws_iam_openid_connect_provider.github arn:aws:iam::404379474987:oidc-provider/token.actions.githubusercontent.com
terraform import aws_iam_role.ci glassbox-ci
terraform import aws_iam_role_policy.ci glassbox-ci:glassbox-ci-deploy
terraform import aws_iam_role.plan glassbox-ci-plan
terraform import aws_iam_role_policy.plan glassbox-ci-plan:glassbox-ci-plan
terraform import aws_iam_role.release glassbox-ci-release
terraform import aws_iam_role_policy.release glassbox-ci-release:glassbox-ci-release
```

The `random_id` import takes the id in base64url form (derived from the bucket
suffix), and `random_id.state_bucket.hex` must then equal `ab88985b66efc96f`.
Finish with `terraform plan` until it shows no changes. To recover only the
state file, restore the latest good version of
`bootstrap/terraform.tfstate` from the bucket's version history.
