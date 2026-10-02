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


**Action pinning:** Third-party actions are pinned by full 40-char SHA with a plain `# vX.Y.Z` comment (not `# vX (vX.Y.Z)`; Dependabot keeps only the plain form current). Never use a bare tag or branch in `uses:`. Dependabot (`.github/dependabot.yml`) opens one grouped `ci:` PR per month for all github-actions updates.

**Runner pin:** Workflows run on `ubuntu-24.04` (and `ubuntu-24.04-arm` for the release build), not `ubuntu-latest`, so the 2026-10-19 move of `ubuntu-latest` to Ubuntu 26 cannot silently change our runners (Python 3.12 and Node 22 toolcache availability on 26.04 is unproven). BACKLOG: revisit the `ubuntu-24.04` runner pin after Ubuntu 26 images are proven.
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

## Concurrency: PR plans never cancel a main apply

Until 2026-10-01 every Terraform run (PR plans, Dependabot included, and the
main plan and apply) shared one group, `terraform-prod-state`. GitHub's rule
for a concurrency group is: at most one run in progress, and with the default
queue (`single`) at most one pending; a newly queued run **replaces** the
pending one. `cancel-in-progress: false` doesn't change that, it only spares
the running one. So each merge cancelled the previous main run, and a
Dependabot PR's plan replaced main's queued apply before it ever reached the
`terraform-prod` approval.

Now the groups are split by event (workflow-level `concurrency`):

| Run | Group | Cancel in progress | Locking |
|---|---|---|---|
| Terraform, PR | `terraform-pr-<number>` | yes (a new push supersedes the old plan) | `plan -lock=false` |
| Terraform, push to `main` | `terraform-prod-main` | no | `plan`/`apply -lock-timeout=10m` |
| Bootstrap, PR | `bootstrap-pr-<number>` | yes | `plan -lock=false` (always was) |
| Bootstrap, Run workflow | `terraform-bootstrap-state` | no | plan `-lock=false`, apply re-plan locks |

- PR plans can't touch a main run's queue, and since they don't lock, they
  can't make an apply fail with "Error acquiring the state lock" either. A
  PR plan that overlaps an apply reads the state before or after the write
  (S3 writes are atomic); it's a preview, and the main run re-plans anyway.
- Main runs are one at a time end to end: a run waiting for approval holds
  the group. Merges that land meanwhile queue behind it, and only the newest
  stays pending (older pending ones show as cancelled). That is intended:
  the newest run plans and applies `main`'s head, so it covers every merge
  before it, and the owner approves once instead of once per merge. Reject a
  waiting run you don't want, and the pending one starts.
- `-lock-timeout=10m` on the main plan and apply covers a lock still held by
  something else (for example a PR run started from an older copy of this
  workflow, which still locked).
- **Switch-over:** runs already queued under the old `terraform-prod-state`
  group (started before this change merged) aren't serialized with the new
  `terraform-prod-main` group. Until they have all finished, approve only the
  newest main run and reject older waiting ones.
- GitHub also offers `queue: max` (up to 100 pending, FIFO). Not used: it
  would make the owner approve every superseded main run in turn.

## Release and the deploy branch

`release.yml` builds the image on every push to `main` that touches an image
input and pushes it to ECR as `<short-sha>`, `build-N` and `latest`, where N
is `github.run_number`. Flux's `ImagePolicy` deploys the highest `build-N`.
Two safeguards keep that honest:

- **Manual runs build only `main`'s current head.** Actions → Release → Run
  workflow is for rebuilding after a failed or flaky run. Because N grows with
  every new run whatever commit it builds, a manual run on another branch or
  tag, or on a `main` commit that is no longer the head (for example a run
  that queued behind another release while a merge landed), would get the
  highest number and Flux would roll production back to it. Three layers stop
  that:
  - **The gate: the release role's trust requires `refs/heads/main`** (see
    "Release role trust" below; applied 2026-10-01). A manual run executes
    the `release.yml` of the ref it was started on, so a branch or tag cut
    before the in-workflow check existed has no check at all, but its token
    carries that branch's or tag's ref, so AWS refuses the role and nothing
    is built or pushed.
  - **The `release` environment's deployment-branch policy (owner setting):**
    GitHub → Settings → Environments → `release` → Deployment branches and
    tags → Selected branches and tags → `main` only. A second layer: GitHub
    then never issues the environment's token to another ref.
  - **The in-workflow check, for refs that contain it.** The first step
    (`.github/scripts/release-provenance.sh`) refuses a manual run before
    AWS credentials are requested unless `github.ref` is `refs/heads/main`
    and `github.sha` equals `git ls-remote origin refs/heads/main`.
    Equality, not "is an ancestor of main": every old release is an
    ancestor. With the branch policy in place, this is what still catches
    a run on `main` whose commit is no longer the head. If it refuses,
    start a new run on `main`.

  Push runs skip the check: they only fire on `main`, and the `release-main`
  concurrency group runs them in order, so a newer commit always gets a
  higher number. A **re-run** of any earlier run keeps its `run_number`
  (only `run_attempt` changes), so it re-pushes its own `build-N`, which
  can't outrank newer builds; a re-run of an old manual run is refused
  anyway, because its commit is no longer `main`'s head. One side effect
  remains: a re-run of an old push run also moves `:latest` (and its SHA
  tag) to that old commit, because ECR tags are mutable
  (`infra/modules/registry`). Production is unaffected, since Flux selects
  only by the numeric `build-N` policy, but don't treat `:latest` as "what
  is deployed".
- **The deploy-branch sync retries instead of losing a race.**
  `sync-deploy-branch.yml` merges `main` into `deploy` on every push to
  `main`; Flux's `ImageUpdateAutomation` commits tag bumps to the same
  branch. When Flux pushes between the sync's fetch and push, the sync's push
  is rejected as non-fast-forward. `.github/scripts/sync-deploy-branch.sh`
  then re-fetches both branches, rebuilds the merge on the new `deploy` tip
  and pushes again, up to 5 attempts with 5, 10, 15 and 20 s waits. It never
  force-pushes, so Flux's commits are never dropped. A merge conflict is not
  retried (exit 2), and running out of attempts fails the job (exit 1); in
  both cases `deploy` is left as it was, and the fix is to resolve the
  conflict or re-run the job. The workflow is one-at-a-time
  (`sync-deploy-branch` concurrency group, not cancelled midway). Flux's own
  rejected push is retried at its next 1-minute reconcile from the new tip.

Both scripts have offline tests against local bare repositories, run by the
Terraform workflow's validate job (`.github/scripts/tests/`). The sync test
uses a `git` shim that pushes a competing "Flux" commit just before the
script's push, so the retry path sees a real non-fast-forward rejection.

## Post-deploy stream check

`.github/workflows/stream-check.yml` checks that streaming still works end to
end through Cloudflare (`docs/DESIGN-002-followups.md` §7.5, §7.8). It runs
when a Release run on `main` succeeds (`workflow_run`), daily at 05:41 UTC,
and by hand (Actions → Stream check → Run workflow). No secrets, no AWS,
`contents: read` only, `ubuntu-24.04`.

- **Waiting for the release.** The image carries its own tag: `release.yml`
  passes `GLASSBOX_BUILD=build-N` as a Docker build argument and
  `GET /api/version` returns it. After a release, the check polls that
  endpoint every 20 s until it reports the Release run's `build-N` (or a
  newer one) on 3 reads in a row, for up to 30 minutes, then runs the checks.
  "Not live after 30 minutes" fails the run with exit 2: look at Flux (Ops ·
  Diagnose) for a stuck image automation, migration or rollout.
- **What it checks** (`.github/scripts/stream-check.py`): `http://` 301s to
  `https://`; the page and the stream carry the app's security headers;
  `/api/cluster/stream` answers 200 with `text/event-stream`, no
  `Content-Encoding` (the request offers gzip, br and zstd), `no-cache`, and
  `cf-cache-status` `DYNAMIC` or `BYPASS`; over 50 s the first event arrives
  within 5 s, a `: ping` arrives whenever no event came for 15 s, and no gap
  exceeds 20 s. A 5xx on the page (another release's rollout) is retried
  twice, 30 s apart. The
  arrival table is in the run's job summary.
- **Why not `/api/ask`.** Every real question spends a slot of the daily LLM
  budget and an embedding call, so the check streams the free cluster
  stream, which shares the `/api/*` cache rule, the Traefik route and the
  heartbeat wrapper.
- **A failure** is only a failed run, emailed like the uptime probe's. It
  never blocks, gates or rolls back a deploy. A newer release cancels a check
  still waiting for an older one (concurrency group per trigger). Like every
  scheduled workflow in a public repository, the daily run is disabled after
  60 days without repository activity.
- Offline tests: `services/tests/test_stream_check.py` (CI's backend tests).

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
| Ops · List snapshots | reason | `list-snapshots` (read-only) |
| Ops · Restore from snapshot | snapshot ("latest" or a `snap-` ID), old_volume (keep / delete), reason | `restore-snapshot` |
| Ops · Reindex | reason | `reindex` |

The wrappers grant `contents: read` and `id-token: write`; the core keeps the
diagnose-before job (`ops-read`), the approval-gated act job (`ops`) and the
diagnose-after step. The OIDC `sub` of a job in a called workflow is the
caller's environment/ref subject, so the role trust policies don't change
(they don't condition on `job_workflow_ref`). The action names below are the
core's `action` values. There is no free-form command input. Each action runs one
Terraform-managed SSM Command document (`infra/modules/ops`, named
`glassbox-ops-<action>`) or one fixed EC2 call (reboot, replace root
volume). The roles can't send any
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
filter itself fails, the rest of the output is withheld, not printed raw.
Ops scripts must never read Secrets (`get secret`, jsonpath into `.data`):
a bare value with no key around it can't be recognised. That
filter is a safety net, not a guarantee; it has an offline test
(`infra/modules/ops/tests/redact-test.sh`, run in CI). `ops-run.sh` runs the
same `redact` over everything SSM returns (stdout, stderr) and over the
`reason` input before printing or adding it to the step summary
(`ops-run-test.sh`). SSM keeps only the first
24,000 characters of a command's output.

| Action | What it does | When to use it | Approval |
|---|---|---|---|
| `diagnose` | Read-only snapshot: uptime, memory, swap and zram, memory/IO/CPU pressure (PSI), vmstat, largest processes, k3s service, nodes and conditions, `kubectl top`, all pods, deployments, CronJobs and Jobs, Flux sources, Kustomizations and HelmReleases, KEDA, the newest warning events, counts of the API's CSP Report-Only violation log lines from the last 24 hours (directive and blocked origin only), k3s error lines from the last 30 minutes, kernel OOM kills. Every command is time-bounded. | First step of any incident (521/522, slow site, stuck deploy). Any time you want to look. | None (`ops-read`) |
| `reboot-node` | EC2 `RebootInstances` on the tagged glassbox instance. This is an ACPI reboot that AWS forces after about 4 minutes. Reads the kernel boot ID first (read-only `glassbox-ops-boot-id` document), then waits up to 20 minutes for a different boot ID, so a ping from before the reboot can't pass for success; if the node can't answer beforehand it requires a boot time after the request instead. Fails clearly if it never changes. Then gives k3s 90s and runs diagnose. | The node is unresponsive: diagnose times out or the apiserver doesn't answer, or memory/IO pressure stays pegged and nothing else helps. | `ops` |
| `restart-deployment` (`deployment`: `api`, `retrieval-worker`, `traefik`) | `kubectl rollout restart` plus a wait of up to 10 minutes for the rollout. | A pod is Running but wedged (stuck streams, not serving), or Traefik is routing badly. `api` rolls with `maxSurge: 0`, so the site is down for a few seconds. | `ops` |
| `flux-suspend` (`flux_target`) | Sets `spec.suspend: true`, like `flux suspend`. Targets: Kustomizations `keda`, `keda-scaling`, `ingest`, `app-ready` and `flux-system`, and `helmrelease-keda` (the KEDA HelmRelease). | Stop Flux re-applying something during an incident, for example KEDA Helm retries thrashing the node (`helmrelease-keda`, `keda`), or skip an ingest run (`ingest`). `flux-system` freezes **all** deploys. | `ops` |
| `flux-resume` (`flux_target`) | Sets `spec.suspend: false` and requests a reconcile, waiting up to 5 minutes for it, like `flux resume`. | Undo a suspend once the incident is over. | `ops` |
| `flux-reconcile` | Refreshes the `deploy` branch source, reconciles the root `flux-system` Kustomization (waiting up to 7 minutes), then requests a reconcile of every other non-suspended Kustomization and HelmRelease. Refuses if `flux-system` is suspended. | Deploy now instead of waiting for Flux's interval, for example after merging a fix or resuming. | `ops` |
| `scale-keda` (`keda_replicas`: `0`, `1`) | Scales every Deployment in the `keda` namespace. At 1 it waits for the rollouts. | 0 frees roughly 150 MiB under memory pressure; the worker then stays at its current count. Suspend `helmrelease-keda` too, or a Helm upgrade brings KEDA back. Use 1 to restore it. | `ops` |
| `cronjob-suspend` / `cronjob-resume` (`cronjob`: `warm-answers`) | Sets `spec.suspend` on the CronJob. A Job that is already running finishes. | Stop the 2-hourly answer warm-up from adding load or LLM calls during an incident, then turn it back on. | `ops` |
| `list-snapshots` | Read-only, from the AWS API (works with the node down): the two status-check alarms with their state and whether their actions are enabled (`False` means paused, for example by an interrupted restore), the node's daily snapshots (ID, start time, state, size; oldest first), its root volume replacement tasks, and every detached volume in the region (old root volumes kept by restores) with an estimated monthly cost. Every `diagnose` prints the same section after the node's own output. Missing permissions or resources (before the Bootstrap run or the Terraform apply) print a note instead of failing. | To pick a snapshot for a restore, check the alarms, or see whether a restore finished. | None (`ops-read`) |
| `restore-snapshot` (`snapshot`: `latest` or `snap-...`; `old_volume`: `keep`, `delete`) | EC2 replace root volume (`CreateReplaceRootVolumeTask`) from one of the node's daily snapshots: EC2 creates a new root volume from it, reboots the instance onto it, and keeps the instance ID, Elastic IP, private IP and network interface. Before asking EC2 for anything it refuses (fails closed): a `snapshot` that isn't `latest` or a well-formed snapshot ID, an `old_volume` other than `keep`/`delete` (both checked before any AWS call), an instance that isn't `running`, a restore already in flight, and a snapshot that isn't a completed daily snapshot of this instance (by its DLM tags and `instance-id` tag). `latest` is the newest completed one. Just before the replacement it pauses the actions of `glassbox-node-reboot` (`DisableAlarmActions`), so a slow boot can't trigger a second reboot mid-restore, and re-enables them on every exit path (success, failure, cancel); if the re-enable fails the run fails and says so, and the listing shows the alarm's actions as `False`. If the pause itself fails it restores anyway (the alarm needs 3 straight minutes of failed checks). Then it polls the task for up to 20 minutes, waits up to 10 minutes for the SSM agent, gives k3s 90 s, and the after-diagnose runs, all inside the act job's 60 minutes and the one-hour OIDC session. `keep` leaves the replaced volume detached (about $1.60 a month for 20 GB until someone deletes it in the console; the ops roles can't delete volumes); `delete` has EC2 delete it once the replacement succeeds. | The disk state is bad and a day's loss is acceptable: a broken upgrade or migration, corrupted k3s or MySQL data, lost data. Everything written after the snapshot (questions asked, cache, budget counters, Flux's progress) is lost; Flux re-applies the `deploy` branch after the boot. Not for a hung node (Reboot node) or a failing host (the recover alarm). | `ops` |
| `reindex` | Runs `python -m services.glassbox.ingest.run --reindex` once as a one-off Job `ops-reindex-<UTC timestamp>` in `app` (`infra/modules/ops/scripts/reindex.sh`): the image the `api` Deployment runs, the ingest Job's ConfigMap, MySQL password reference, pull secret and `app: ingest` label (so the data NetworkPolicies admit it), no retries (`backoffLimit: 0`), stopped by Kubernetes after 15 minutes, deleted a day after it ends. It rewrites every Redis `chunk:{id}` key of the configured embedding model from MySQL (stored vectors: no file scan, no Bedrock call) and drops each written id's `chunktxt:{id}` text cache; orphan keys are deleted even past the 30% guard (never when MySQL has zero chunks for a corpus). It refuses (creates nothing) while the `ingest` Job or an earlier reindex Job is running, while `api` is mid-rollout, until this release's `ingest` Job has completed with the api's image (otherwise the reindex could hold `ingest:lock` when that ingest starts, and a locked-out ingest skips the release's corpus changes until the next release), and if its image isn't the project's ECR `glassbox` image. A release that starts after these checks can still meet a running reindex, with the same outcome. At the 15-minute deadline Kubernetes sends SIGTERM and `--reindex` releases `ingest:lock` as it stops (exit 143); only a hard kill would leave the lock held until its 30-minute TTL, skipping a release ingest in that window. The Job's last 100 log lines (the per-corpus reconcile summary, redacted) are printed. Exit 75 means another ingest or reindex held the Redis lock `ingest:lock`, so it did nothing: run it again later. Offline test: `infra/modules/ops/tests/reindex-test.sh` (run in CI). | After MySQL alone was wiped or restored while Redis kept its keys (Restore from snapshot puts both back from the same disk, so it doesn't need this), or when the ingest log shows `REDIS RECONCILE REFUSED` for orphan keys you do want gone. Redis data loss alone needs nothing: every release's ingest reconcile repairs it. | `ops` |
| `apply-zram` | Runs the Terraform-managed `glassbox-zram-swap` document (`infra/modules/compute/zram-swap.sh`, mode `apply`). First checks that the applied document matches the script at the workflow's commit and refuses if it doesn't. Needs the zram change (PR #55). | Diagnose shows no `/dev/zram0` after a reboot, or you want the weekly self-heal to happen now. | `ops` |

Timeouts are generous because a swapping node is slow. SSM keeps trying to
deliver a command for 10 minutes, each script has its own on-node limit (5 to
15 minutes, 30 for `reindex`), and the workflow polls for up to 20 minutes (31 for
`reindex`). If the workflow
gives up first, the command can still finish on the node; run `diagnose` to
see the result.

Concurrency: one ops run at a time (`ops-prod` group, never cancelled
midway).

### Roles

Both roles are defined in `infra/bootstrap/runbooks.tf`. They reuse
`local.github_oidc_subject_prefix`, the immutable OIDC subject.

- `glassbox-ops-read` trusts only the `ops-read` environment (no reviewer,
  `main`-only branch policy). It used to trust plain `main` jobs with no
  environment as well; that subject was removed because nothing used it
  (`ops.yml`'s diagnose job is the only caller and always runs in
  `ops-read`), and it let any `main` job with `id-token: write` run
  diagnose. A new workflow that needs diagnose must use the `ops-read`
  environment. It can call `ssm:SendCommand` only with
  `glassbox-ops-diagnose`, and only on an instance tagged
  `Name=glassbox,project=glassbox`. It can also read command results and
  instance state (`ssm:GetCommandInvocation`,
  `ssm:ListCommandInvocations`, `ssm:DescribeInstanceInformation`,
  `ec2:DescribeInstances`, `ec2:DescribeInstanceStatus`), limited to
  us-east-1. For `list-snapshots` and diagnose it can list snapshots, root
  volume replacement tasks and alarms (`ec2:DescribeSnapshots`,
  `ec2:DescribeReplaceRootVolumeTasks`, `cloudwatch:DescribeAlarms`,
  us-east-1; these have no resource-level scoping).
- `glassbox-ops` trusts only the `ops` environment. It can call
  `ssm:SendCommand` only with `glassbox-ops-*` and `glassbox-zram-swap`, on
  the tagged instance. It can call `ec2:RebootInstances` only on the tagged
  instance, and `ssm:GetDocument` only on `glassbox-zram-swap`. It can call
  `ec2:CreateReplaceRootVolumeTask` only on the tagged instance, and, when
  the request names a snapshot, only one tagged `project=glassbox` and
  `glassbox-backup=daily-root` (the DLM policy's tags). It also has the
  call on `volume/*` and `replace-root-volume-task/*`. **Residual risk:**
  IAM does not force a snapshot to be named. A launch-state replacement (no
  snapshot: the disk as the AMI first created it) or one from an existing
  detached volume (`--volume-id`; volumes have no tag condition key for
  this action) would likely be allowed. No condition key separates those
  modes: `ec2:SnapshotID` exists only on the snapshot resource, so a
  Null-condition Deny would also deny the instance and task parts of every
  valid request. Those modes are blocked only by the reviewed script (it
  always passes a validated daily snapshot) plus the owner's `ops`
  approval, the same trust every other action of this role rests on. It
  may use the EBS KMS key only through EC2 (`kms:ViaService`; `CreateGrant`
  also only for AWS resources), and may disable and enable the actions of
  the `glassbox-node-reboot` alarm only. It has no DeleteVolume,
  DeleteSnapshot or CreateSnapshot. The read role's listing also gets
  `ec2:DescribeVolumes` (no resource-level scoping). It has the
  same reads as `glassbox-ops-read`. `glassbox-ops-boot-id` and `glassbox-ops-reindex` are covered by its `glassbox-ops-*` document wildcard (no IAM change to add them); `glassbox-ops-read` still sends only `glassbox-ops-diagnose`. It has no StartSession, no
  Stop/Terminate, no AWS-RunShellScript, and no document writes.

## Alarms, uptime probe and daily snapshots

Added on 2026-10-01 (owner's choice instead of the self-healing plan's
phases 2 to 4, see `docs/superpowers/plans/2026-10-01-self-healing-node.md`).
The AWS parts take effect once applied (order below).

**Status-check alarms** (`infra/modules/compute/alarms.tf`):

| Alarm | Metric | Fires after | Action |
|---|---|---|---|
| `glassbox-node-recover` | `StatusCheckFailed_System` (host hardware, power, network) | 2 of 2 one-minute periods | EC2 recover: same instance on healthy hardware, same ID, IPs and disk |
| `glassbox-node-reboot` | `StatusCheckFailed_Instance` (kernel hung, OOM, guest network) | 3 of 3 one-minute periods | EC2 reboot (like Ops · Reboot node) |

Both notify the SNS topic `glassbox-alerts` on ALARM and on OK, which emails
`alert_email` (set in `infra/envs/prod/main.tf`; already public on the site,
so a plain value). AWS first sends a "Subscription Confirmation" email: the
owner must click it, or nothing is delivered. Terraform can't delete a
subscription that is still pending; if the address changes before it is
confirmed, the old pending one lingers until AWS expires it (about 3 days).
Missing data never triggers an action (`treat_missing_data = missing`).
Don't test the alarms with `set-alarm-state`: the action would really
reboot or recover the node. "Ops · List snapshots" shows their states.
They watch one fixed instance ID; a replacement instance gets new alarms in
the same Terraform apply. The instance also has `maintenance_options {
auto_recovery = "default" }` written down (EC2's default, no change).

**Uptime probe** (`.github/workflows/uptime.yml`): at :07, :22, :37 and
:52 every hour, GETs `https://basel.engineering/readyz` and fails unless it
is HTTP 200 with `"ready": true` within three tries 30 s apart. No secrets,
no AWS, `permissions: {}`. GitHub emails a scheduled run's failure to the
user who last changed its `cron` line, if their notification settings
(Settings → Notifications → Actions) allow it. This repository's commits
carry a local author email, so check that the first failure email really
arrives (or that the Actions tab shows the failure badge you watch). GitHub
disables scheduled workflows in a public repository after 60 days without
repository activity: re-enable it in Actions → Uptime probe if that ever
happens. Run workflow by hand to test it.

**Daily snapshots** (`infra/modules/compute/snapshots.tf`): a Data
Lifecycle Manager policy, run as the `glassbox-dlm` role (AWS managed
`AWSDataLifecycleManagerServiceRole`), snapshots the instance tagged
`Name=glassbox` every 24 hours starting within an hour after 04:00 UTC and
keeps the newest 7. The node has one EBS volume, the root volume, holding
the k3s SQLite datastore and both local-path volumes (MySQL, Redis), so a
snapshot is the whole server. Snapshots are tagged `Name=glassbox-daily`,
`project=glassbox`, `glassbox-backup=daily-root` and `instance-id=<id>`;
the restore runbook and the `glassbox-ops` role accept only those. The
policy targets the instance, not the volume, so it keeps working after a
restore gives the instance a new root volume.

Consistency: snapshots are crash-consistent (the disk as after a power cut
at that instant). InnoDB and SQLite recover from that by design (redo log
and WAL); Redis reloads its last persisted state. No pre-snapshot `sync`
script: DLM pre-scripts need an SSM document and SSM permissions on a
second role, and a `sync` only shortens the window of unflushed writes,
it doesn't make the databases more consistent than their crash recovery.

**Restore** is the `restore-snapshot` runbook above. Restoring only works
from snapshots of this instance's current or previous root volumes (EC2's
rule), which is exactly what the policy produces. After a restore, the
next Terraform plan may show an in-place tag update on the new root volume;
that is harmless.

**Cost:** two alarms $0.20/month (free within CloudWatch's 10 free alarms),
SNS email free, DLM free, snapshots about $0.05 per GB-month of stored
blocks: the first is the used part of the disk, the daily ones are
incremental, so roughly $0.40 to $0.80/month in total. A kept old volume
after a restore adds $1.60/month until deleted.

**Apply order** (IAM first, or the Terraform apply fails with
AccessDenied): (1) merge; (2) Actions → Bootstrap → Run workflow on `main`,
approve; the plan should show only in-place updates to the `glassbox-ci`,
`glassbox-ci-plan`, `glassbox-ops-read` and `glassbox-ops` role policies;
(3) approve the pending Terraform run on `main` (adds the topic,
subscription, two alarms, the `glassbox-dlm` role and its attachment, and
the DLM policy: 7 to add; the instance shows no change. The 1 in-place
change is `module.ops.aws_ssm_document.ops["diagnose"]`, from the earlier
merged Diagnose changes #116/#121, and is expected); (4) click the AWS confirmation email;
(5) run Ops · List snapshots (alarms `OK`; snapshots appear after the
next 04:00 UTC window) and Ops · Diagnose.

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

**Bucket policy in the plan.** The state bucket policy names the CI roles
it denies `bootstrap/*` to. It used to take their ARNs from
`aws_iam_role.*.arn`, so whenever one of those roles had a pending change
(PR #98's release-role trust edit), Terraform deferred reading the policy
document to apply time and the plan also listed
`aws_s3_bucket_policy.state` as "updated in-place ... (known after apply)".
Apply then found the JSON identical and changed nothing ("2 to change", 1
changed). The ARNs are now built from the account ID and role names (same
strings), with `depends_on` on the roles, so a role change plans as just
that role. It was never a perpetual diff: plans with no role change were
already clean.

`glassbox-bootstrap-plan` trusts only `bootstrap-plan` (no reviewer, any
branch, like `terraform-plan`). It has IAM `Get*`/`List*`, bucket-level
`s3:Get*`/`s3:List*` on the state bucket, and `s3:GetObject` on
`bootstrap/terraform.tfstate`. It plans with `-lock=false`, so it has no
write access.

## Release role trust

Applied through the Bootstrap workflow on 2026-10-01 (PR #98).

`glassbox-ci-release` (`release_trust` in `infra/bootstrap/main.tf`) is
assumed only by `release.yml`'s `build-and-push` job. Its trust requires
both:

- `sub` = `<immutable prefix>:environment:release`, and
- `token.actions.githubusercontent.com:ref` = `refs/heads/main`.

A manual Release run on any other branch or tag then can't get AWS
credentials, even if the `release` environment's branch policy is missing or
loosened. STS has accepted GitHub claims (`ref`, `job_workflow_ref`,
`environment`, `workflow`, `repository_id` and others) as trust-policy
condition keys since January 2026 (AWS IAM User Guide, "IAM and AWS STS
condition context keys", OIDC federation, GitHub tab). The immutable subject
changes only `sub`; `ref` is the plain git ref. `job_workflow_ref` was not
used: it carries the mutable owner/repo names, and it would break releases
if `release.yml` were renamed. (AWS documents it for reusable workflows, but
GitHub emits it for every job; that doesn't change the choice.)
This check does not catch a run on `main` whose commit is no longer the
head; the in-workflow provenance check covers that. `sync-deploy-branch.yml`
uses no AWS role.

The other roles still trust only their environment `sub`. `terraform-prod`, `ops` and `bootstrap` are `main`-only
environments, so a `ref` condition there would be defence in depth only. The
plan roles must keep working on PR refs (`refs/pull/N/merge`), so they can't
be pinned to `main`.

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
