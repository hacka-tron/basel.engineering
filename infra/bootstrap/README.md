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
Once the migration and the one-time owner setup in `infra/CI.md` ("One-time
owner setup") are done, changes go through `.github/workflows/bootstrap.yml`
(plan on PRs, apply after owner approval) instead of a manual local apply.

```sh
terraform init
terraform plan
terraform apply
terraform output state_bucket_name
terraform output ci_role_arn
terraform output plan_role_arn
terraform output release_role_arn
```

The first apply and the one-time setup are manual, with the owner's own AWS
CLI credentials: CI cannot bootstrap itself, since the roles it would assume
don't exist until this module has already run. `infra/envs/prod/backend.tf`
already references the real bucket this module created; `state_bucket_name`
only needs re-checking if this module is ever re-run against a fresh
account.

Before the pipeline is set up, a bootstrap code change must be reviewed and
applied manually by the owner before merging a workflow that depends on the
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
`bootstrap/terraform.tfstate`). Only the `glassbox-bootstrap*` pipeline roles (PR #62) and the owner can
read or write this key; the app CI roles are denied by the bucket policy. The bucket is `glassbox-tfstate-404379474987-ab88985b66efc96f`.

Prerequisite: other pending bootstrap changes (PR #55's SSM permissions on
branch `fix/zram-swap`, PR #62's runbook roles) must be **merged to `main`
first** and never applied from their branches. Otherwise the post-migration
plan would propose removing whatever a branch added. Run everything below
from an up-to-date `main` checkout. Migration only copies the state; the plan
in step 5 is the review point for everything still pending.

1. `git checkout main && git pull`, then `cd infra/bootstrap`.
2. Do not run `plan` yet. Terraform refuses to plan after a backend change
   until it is re-initialized ("Backend initialization required").
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
     `s3:ListBucket` restricted by prefix, plus #55's SSM document permissions);
   - additions for anything else merged but not yet applied (#62: four
     `glassbox-ops-*`/`glassbox-bootstrap*` roles and their policies).
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
