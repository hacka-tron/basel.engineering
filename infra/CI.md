# Terraform GitHub Actions setup

`.github/workflows/terraform.yml` validates both Terraform roots on PRs and
pushes to `main`. It plans `infra/envs/prod` on same-repository PRs and pushes,
then applies `infra/envs/prod` after a merge to `main`. `infra/bootstrap` remains
a manually applied local-state root. Fork PRs receive formatting and
validation checks, but do not receive production state or Cloudflare access.

As of 2026-09-30, both environments, their Cloudflare secrets and zone ID
variables, and the bootstrap IAM roles are configured. The owner approved and
applied the plan role's read-policy fix in draft PR #13. Protected PR run
`36675954502` then completed successfully and reported no production changes.
The owner subsequently chose automatic previews: `terraform-plan` has no
required reviewer, while `terraform-prod` still requires owner approval and
accepts only `main`.

## Environment setup and recovery

1. In GitHub repository Settings → Environments, create `terraform-plan` and
   `terraform-prod`. Add the owner as a **required reviewer** to `terraform-prod`
   only. Restrict `terraform-prod` deployments to `main`; leave `terraform-plan`
   available to PR refs with no reviewer. The plan job runs automatically for
   same-repository PRs and reads production state and Cloudflare with scoped
   credentials. Fork PRs receive validation only. Review Terraform code before
   merging; adding a repository write collaborator also grants that person the
   ability to run a privileged same-repository preview.
2. In `terraform-plan`, add environment secret `CLOUDFLARE_API_TOKEN` scoped to
   the `basel.engineering` zone with Zone Read, DNS Read, and Cache Rules Read.
   In `terraform-prod`, add a separate `CLOUDFLARE_API_TOKEN` scoped to the same
   zone with Zone Read, DNS Edit, and Cache Rules Edit. In **each** environment, add
   variable `CLOUDFLARE_ZONE_ID` for `basel.engineering`. Do not use repository
   secrets for these values, because the environment gates their release.
3. From `infra/bootstrap`, using the owner's AWS credentials and the retained
   local `terraform.tfstate`, run `terraform init` and `terraform plan`. Review
   that the plan adds only `glassbox-ci-plan` and its policy, updates the
   `glassbox-ci` trust policy, and exposes the new output. Then run
   `terraform apply`. The workflow cannot update its own bootstrap roles.
4. Merge the reviewed workflow branch. The `main` plan runs automatically,
   then the apply job waits for `terraform-prod` approval. Inspect the fresh
   production plan log before approving apply. Confirm the run completes and
   review the resulting Terraform state changes.

The workflow uses OIDC and temporary AWS credentials. It never uploads a
binary Terraform plan: plan files can contain unredacted secrets. Its PR
comment reports only whether there are changes and links to the workflow run.
The apply job computes a fresh plan after approval, so review any drift visible
in its log. GitHub environment approval is the production change gate; a merge
alone does not apply infrastructure.
