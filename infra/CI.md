# Terraform GitHub Actions setup

`.github/workflows/terraform.yml` validates both Terraform roots on PRs and
pushes to `main`. It plans `infra/envs/prod` on same-repository PRs and pushes,
then applies `infra/envs/prod` after a merge to `main`. `infra/bootstrap` remains
a manually applied root (state in S3, unreachable by CI). Fork PRs receive formatting and
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
3. From `infra/bootstrap`, using the owner's AWS credentials (state is in S3 at
   `bootstrap/terraform.tfstate`, see `infra/bootstrap/README.md`), run `terraform init` and `terraform plan`. Review
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

## Runbooks (push-button operations)

Every routine production operation is a button: **Actions → "Ops · ..."**
→ Run workflow (branch `main`), set the one or two inputs that runbook has,
optionally write a `reason` (shown in the public run summary), and run.
GitHub can't show inputs conditionally, so each runbook is a thin
dispatch-only wrapper that calls the reusable core `.github/workflows/ops.yml`
with a fixed action:

| Workflow | Inputs | Action |
|---|---|---|
| Ops · Diagnose | reason | `diagnose` |
| Ops · Reboot node | reason | `reboot-node` |
| Ops · Restart deployment | deployment (api, retrieval-worker, traefik), reason | `restart-deployment` |
| Ops · Flux suspend or resume | operation, target, reason | `flux-suspend` / `flux-resume` |
| Ops · Flux reconcile | reason | `flux-reconcile` |
| Ops · KEDA on or off | state ("off (0)" / "on (1)"), reason | `scale-keda` (0/1) |
| Ops · Warm-up CronJob suspend or resume | operation, reason (`warm-answers` is fixed) | `cronjob-suspend` / `cronjob-resume` |
| Ops · Apply zram | reason | `apply-zram` |

The wrappers grant `contents: read` and `id-token: write`; the core keeps the
diagnose-before job (`ops-read`), the approval-gated act job (`ops`) and the
diagnose-after step. The OIDC `sub` of a job in a called workflow is the
caller's environment/ref subject, so the role trust policies don't change
(they don't condition on `job_workflow_ref`). The action names below are the
core's `action` values. There is no free-form command input. Each action runs one
Terraform-managed SSM Command document (`infra/modules/ops`, named
`glassbox-ops-<action>`) or one fixed EC2 call. The roles can't send any
other document, so nothing runs on the node unless it was reviewed here and
applied by the Terraform workflow. Document inputs are enums that SSM checks
against `allowedValues` before anything runs.

Every run starts with a read-only `diagnose` (no approval) so the approver
sees the node's state first. Mutating actions then wait for the owner in the
protected `ops` environment, act, and run `diagnose` again. Output appears in
the job log (collapsed groups) and in the run summary. The repository is
public, so diagnose is built to be safe to publish. It runs no `describe`
and no environment dump. Warning events print only namespace, last-seen time,
count, reason and involved object kind and name, never the message. k3s log
lines are never printed, only counted per category (timeouts, etcd/kine,
connection errors, TLS, image pull, memory, disk, probes; kine's "Slow SQL"
lines contain SQL arguments). The few remaining free-text columns (Flux
status, kernel OOM lines) pass through a redaction filter (`redact` in
`infra/modules/ops/scripts/lib.sh`): URL userinfo and query strings removed,
values after password/token/secret/key/authorization masked (also when the
value is on the following lines: YAML blocks, pretty-printed JSON, the
values of `data:`/`stringData:` maps, PEM blocks), JWT-like and 20+
character base64/hex strings masked, lines cut to 160-220 characters. If the
filter itself fails, the rest of the output is withheld, not printed raw. That
filter is a safety net, not a guarantee; it has an offline test
(`infra/modules/ops/tests/redact-test.sh`, run in CI). `ops-run.sh` runs the
same `redact` over everything SSM returns (stdout, stderr) and over the
`reason` input before printing or adding it to the step summary
(`ops-run-test.sh`). SSM keeps only the first
24,000 characters of a command's output.

| Action | What it does | When to use it | Approval |
|---|---|---|---|
| `diagnose` | Read-only snapshot: uptime, memory, swap and zram, memory/IO/CPU pressure (PSI), vmstat, largest processes, k3s service, nodes and conditions, `kubectl top`, all pods, deployments, CronJobs and Jobs, Flux sources, Kustomizations and HelmReleases, KEDA, the newest warning events, k3s error lines from the last 30 minutes, kernel OOM kills. Every command is time-bounded. | First step of any incident (521/522, slow site, stuck deploy). Any time you want to look. | None (`ops-read`) |
| `reboot-node` | EC2 `RebootInstances` on the tagged glassbox instance. This is an ACPI reboot that AWS forces after about 4 minutes. Reads the kernel boot ID first (read-only `glassbox-ops-boot-id` document), then waits up to 20 minutes for a different boot ID, so a ping from before the reboot can't pass for success; if the node can't answer beforehand it requires a boot time after the request instead. Fails clearly if it never changes. Then gives k3s 90s and runs diagnose. | The node is unresponsive: diagnose times out or the apiserver doesn't answer, or memory/IO pressure stays pegged and nothing else helps. | `ops` |
| `restart-deployment` (`deployment`: `api`, `retrieval-worker`, `traefik`) | `kubectl rollout restart` plus a wait of up to 10 minutes for the rollout. | A pod is Running but wedged (stuck streams, not serving), or Traefik is routing badly. `api` rolls with `maxSurge: 0`, so the site is down for a few seconds. | `ops` |
| `flux-suspend` (`flux_target`) | Sets `spec.suspend: true`, like `flux suspend`. Targets: Kustomizations `keda`, `keda-scaling`, `ingest`, `app-ready` and `flux-system`, and `helmrelease-keda` (the KEDA HelmRelease). | Stop Flux re-applying something during an incident, for example KEDA Helm retries thrashing the node (`helmrelease-keda`, `keda`), or skip an ingest run (`ingest`). `flux-system` freezes **all** deploys. | `ops` |
| `flux-resume` (`flux_target`) | Sets `spec.suspend: false` and requests a reconcile, waiting up to 5 minutes for it, like `flux resume`. | Undo a suspend once the incident is over. | `ops` |
| `flux-reconcile` | Refreshes the `deploy` branch source, reconciles the root `flux-system` Kustomization (waiting up to 7 minutes), then requests a reconcile of every other non-suspended Kustomization and HelmRelease. Refuses if `flux-system` is suspended. | Deploy now instead of waiting for Flux's interval, for example after merging a fix or resuming. | `ops` |
| `scale-keda` (`keda_replicas`: `0`, `1`) | Scales every Deployment in the `keda` namespace. At 1 it waits for the rollouts. | 0 frees roughly 150 MiB under memory pressure; the worker then stays at its current count. Suspend `helmrelease-keda` too, or a Helm upgrade brings KEDA back. Use 1 to restore it. | `ops` |
| `cronjob-suspend` / `cronjob-resume` (`cronjob`: `warm-answers`) | Sets `spec.suspend` on the CronJob. A Job that is already running finishes. | Stop the 2-hourly answer warm-up from adding load or LLM calls during an incident, then turn it back on. | `ops` |
| `apply-zram` | Runs the Terraform-managed `glassbox-zram-swap` document (`infra/modules/compute/zram-swap.sh`, mode `apply`). First checks that the applied document matches the script at the workflow's commit and refuses if it doesn't. Needs the zram change (PR #55). | Diagnose shows no `/dev/zram0` after a reboot, or you want the weekly self-heal to happen now. | `ops` |

Timeouts are generous because a swapping node is slow. SSM keeps trying to
deliver a command for 10 minutes, each script has its own on-node limit (5 to
15 minutes), and the workflow polls for up to 20 minutes. If the workflow
gives up first, the command can still finish on the node; run `diagnose` to
see the result.

Concurrency: one ops run at a time (`ops-prod` group, never cancelled
midway).

### Roles

Both roles are defined in `infra/bootstrap/runbooks.tf`. They reuse
`local.github_oidc_subject_prefix`, the immutable OIDC subject.

- `glassbox-ops-read` trusts the `ops-read` environment, plus `main` for
  jobs that have no environment. It can call `ssm:SendCommand` only with
  `glassbox-ops-diagnose`, and only on an instance tagged
  `Name=glassbox,project=glassbox`. It can also read command results and
  instance state (`ssm:GetCommandInvocation`,
  `ssm:ListCommandInvocations`, `ssm:DescribeInstanceInformation`,
  `ec2:DescribeInstances`, `ec2:DescribeInstanceStatus`), limited to
  us-east-1.
- `glassbox-ops` trusts only the `ops` environment. It can call
  `ssm:SendCommand` only with `glassbox-ops-*` and `glassbox-zram-swap`, on
  the tagged instance. It can call `ec2:RebootInstances` only on the tagged
  instance, and `ssm:GetDocument` only on `glassbox-zram-swap`. It has the
  same reads as `glassbox-ops-read`. `glassbox-ops-boot-id` is covered by its `glassbox-ops-*` document wildcard; `glassbox-ops-read` still sends only `glassbox-ops-diagnose`. It has no StartSession, no
  Stop/Terminate, no AWS-RunShellScript, and no document writes.

## Bootstrap via pipeline

`.github/workflows/bootstrap.yml` replaces the manual `terraform apply` of
`infra/bootstrap`:

- **On a PR touching `infra/bootstrap`**, the workflow plans with the
  read-only `glassbox-bootstrap-plan` role (`bootstrap-plan` environment, no
  approval) and comments the result.
- **To apply**, use Actions → Bootstrap → Run workflow on `main`. The plan
  job runs first; its log has the full plan and its summary has a
  fingerprint. If there are changes, the apply job waits for the owner in
  the protected `bootstrap` environment. After approval it re-plans with
  `glassbox-bootstrap` and applies that saved plan **only if its
  fingerprint matches the one you reviewed**. Otherwise it stops and applies
  nothing. The plan file never leaves the runner, because artifacts in this
  public repository would be downloadable.
- It **fails fast** unless `infra/bootstrap` has `backend "s3"` (PR #59). It
  also fails if the S3 state is empty (the state was never migrated), so it
  can never plan to recreate everything.

`glassbox-bootstrap` trusts only the `bootstrap` environment (owner
reviewer, `main` only). It is effectively administrator of CI's IAM. It has
`iam:*` on `role/glassbox-*`, `policy/glassbox-*` and the GitHub OIDC
provider, IAM reads, and `s3:*` on the state bucket. It can therefore change
any CI role, including its own: that is inherent in applying the root that
defines them. **The guardrail is the approval gate plus the fingerprint
match, not the policy.** The policy only keeps the blast radius to what
bootstrap manages. It has no EC2, SSM or ECR access, and it can't touch
non-`glassbox-` roles, users, or other buckets. The state bucket policy from
PR #59 denies `bootstrap/*` to `glassbox-ci`, `glassbox-ci-plan` and
`glassbox-ci-release` by ARN. The bootstrap roles aren't in that list, so
they can read and write the bootstrap state. The ops roles have no S3 grants
at all. Bucket deletion stays denied to everyone.

`glassbox-bootstrap-plan` trusts only `bootstrap-plan` (no reviewer, any
branch, like `terraform-plan`). It has IAM `Get*`/`List*`, bucket-level
`s3:Get*`/`s3:List*` on the state bucket, and `s3:GetObject` on
`bootstrap/terraform.tfstate`. It plans with `-lock=false`, so it has no
write access.

## One-time owner setup (the last manual step)

The new roles live in `infra/bootstrap`, so they can't create themselves.
Do this **once**, after PRs #55 (zram), #59 (bootstrap state in S3) and this
runbooks PR are merged. Run it from a shell with your admin AWS credentials
and `gh` logged in as the owner. It is safe to paste as one block. Only
`terraform init -migrate-state` (if the state is still local) and
`terraform apply` stop and ask for `yes`.

```sh
cd ~/Coding/basel.engineering && git switch main && git pull --ff-only
REPO=hacka-tron/basel.engineering
OWNER_ID=14956857   # gh api users/hacka-tron --jq .id

# 1. GitHub environments. ops-read: main only, no reviewer.
gh api -X PUT "repos/$REPO/environments/ops-read" --input - <<'JSON'
{"deployment_branch_policy": {"protected_branches": false, "custom_branch_policies": true}}
JSON
gh api -X POST "repos/$REPO/environments/ops-read/deployment-branch-policies" -f name=main -f type=branch

# ops and bootstrap: owner must approve, main only.
for env in ops bootstrap; do
  gh api -X PUT "repos/$REPO/environments/$env" --input - <<JSON
{"reviewers": [{"type": "User", "id": $OWNER_ID}], "prevent_self_review": false,
 "deployment_branch_policy": {"protected_branches": false, "custom_branch_policies": true}}
JSON
  gh api -X POST "repos/$REPO/environments/$env/deployment-branch-policies" -f name=main -f type=branch
done

# bootstrap-plan: any branch (PR plans), no reviewer, like terraform-plan.
gh api -X PUT "repos/$REPO/environments/bootstrap-plan"

# 2. Last manual bootstrap apply. Moves local state to S3 if it isn't there
#    yet (answer yes), then applies. Expected: 8 to add (the four
#    glassbox-ops*/glassbox-bootstrap* roles and their policies), plus
#    whatever of PR #55 (2 policy updates) and #59 (bucket policy, CI S3
#    scope) isn't applied yet. Any destroy or replace: answer no and stop.
cd infra/bootstrap
terraform init -migrate-state
terraform apply
terraform output runbook_role_arns
cd ../..
```

Then approve the pending **Terraform** run on `main` (`terraform-prod`). It
creates the `glassbox-ops-*` documents, and the zram document and
association if not already done. If that run already failed with
AccessDenied before step 2, re-run it. Confirm by running **Ops · Diagnose →
diagnose**.

**From then on, every operation is a button in Actions.** Use the Ops · workflows
for the node, Terraform for production infrastructure, and Bootstrap for the
CI roles themselves. Move the local `terraform.tfstate*` files out of the
repo as `infra/bootstrap/README.md` describes.
