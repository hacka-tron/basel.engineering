# Terraform GitHub Actions setup

`.github/workflows/terraform.yml` validates both Terraform roots on PRs and
pushes to `main`. It plans `infra/envs/prod` on same-repository PRs and pushes,
then applies `infra/envs/prod` after a merge to `main`. `infra/bootstrap` remains
a manually applied local-state root. Fork PRs receive formatting and
validation checks, but do not receive production state or Cloudflare access.

## One-time setup before merging the workflow

1. In GitHub repository Settings → Environments, create `terraform-plan` and
   `terraform-prod`. Add the owner as a **required reviewer** to both. Restrict
   `terraform-prod` deployments to `main`; leave `terraform-plan` available to
   PR refs. Keep these review gates enabled. The plan job executes PR Terraform
   code with access to production state, so review the PR before approving it.
2. In `terraform-plan`, add environment secret `CLOUDFLARE_API_TOKEN` with
   Cloudflare zone read permissions sufficient for DNS and Cache Rules reads.
   In `terraform-prod`, add a separate `CLOUDFLARE_API_TOKEN` with the existing
   zone-scoped DNS/Cache Rules edit permissions. In **each** environment, add
   variable `CLOUDFLARE_ZONE_ID` for `basel.engineering`. Do not use repository
   secrets for these values, because the environment gates their release.
3. From `infra/bootstrap`, using the owner's AWS credentials and the retained
   local `terraform.tfstate`, run `terraform init` and `terraform plan`. Review
   that the plan adds only `glassbox-ci-plan` and its policy, updates the
   `glassbox-ci` trust policy, and exposes the new output. Then run
   `terraform apply`. The workflow cannot update its own bootstrap roles.
4. Merge the reviewed workflow branch. The first `main` run will wait for
   `terraform-plan` approval, then for `terraform-prod` approval. Inspect the
   production plan log before approving apply. Confirm the run completes and
   review the resulting Terraform state changes.

The workflow uses OIDC and temporary AWS credentials. It never uploads a
binary Terraform plan: plan files can contain unredacted secrets. Its PR
comment reports only whether there are changes and links to the workflow run.
The apply job computes a fresh plan after approval, so review any drift visible
in its log. GitHub environment approval is the production change gate; a merge
alone does not apply infrastructure.
