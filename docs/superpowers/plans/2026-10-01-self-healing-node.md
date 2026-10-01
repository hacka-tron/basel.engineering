# Self-healing node: design and staged plan (planned, not built yet)

**Milestone:** M3 (DD2) remainder, "self-healing node (ASG + Elastic IP reassociation)" in `project/BACKLOG.md`.
**Design it revises:** `docs/DESIGN-002-followups.md` §3, whose premise ("all durable state is outside the node, RDS for data") stopped being true when MySQL moved in-cluster (`docs/DESIGN.md` §10.5). The short version of this plan is in DESIGN-002 §3.9.
**Scope of this document:** design and plan only. Nothing here is built, and no infrastructure code changes in the PR that adds it.

Hard rules for every phase below, restated for every implementer and reviewer dispatch: never read, open or copy any `terraform.tfstate` or plan file; no `terraform plan`/`apply` against real backends and no AWS CLI, SSM or kubectl calls from agents; no workflow triggers. Production changes go through the Terraform, Bootstrap and "Ops · ..." workflows with the owner's approval click. Never run `git stash`.

---

## 1. What is lost today if the node dies or is replaced

Everything runs on one `t4g.small` (`aws_instance.glassbox` in `infra/modules/compute/main.tf`) with a single 20 GB gp3 root volume. There is no second volume, no snapshot policy and no backup of the MySQL volume today. The table says what happens to each piece of state if that instance is terminated, or replaced by `terraform apply -replace` (a stop/start or reboot keeps the root volume, so it keeps everything).

| State | Where it lives (evidence) | Lost on replace? | Rebuilt automatically? |
|---|---|---|---|
| k3s cluster state | SQLite (kine) at `/var/lib/rancher/k3s/server/db/` on the root volume; k3s is installed by `user_data.sh` (unpinned `get.k3s.io`) | Yes | Yes, but only on a new instance's first boot, and to whatever k3s version is current that day. The user data installs k3s and nothing more. |
| MySQL data: `documents`, `chunks` (text plus 512-dim embeddings), `ingestion_runs`, `alembic_version` | `mysql-data` PVC, `storageClassName: local-path` (`k8s/base/mysql-statefulset.yaml`), i.e. `/var/lib/rancher/k3s/storage` on the root volume | Yes | Yes. A fresh `migrate` Job creates the schema, and the `ingest` Job re-embeds the whole repo (about 1.5 MB of text, roughly 400k tokens, well under $0.05 with Titan V2). |
| MySQL `queries` table (every question asked, mode, timings, tokens, rewrite) | Same PVC | Yes | **No.** It is the only table that is not derived from Git (`docs/DESIGN.md` §10.5). |
| Redis: vector index `idx:chunks` and `chunk:*` hashes, answer/retrieval/embedding caches, `retrieval:jobs` stream, rate-limit and daily-budget counters, kill switch, `warm:budget:*` | `redis-data` PVC, also `local-path` (`k8s/base/redis-statefulset.yaml`) | Yes | Only together with MySQL. See the caveat below. |
| Flux (`flux-system`) | Controllers and sync objects are in Git on the `deploy` branch (`k8s/overlays/prod/flux-system/`); the Git credential is the `flux-system` Secret, which exists only in the cluster | Yes | **No.** Nothing runs `flux bootstrap` on boot (`user_data.sh` installs the Flux CLI only). Re-bootstrapping needs a GitHub credential with write access to `deploy` that is stored nowhere in Terraform or SSM. |
| Kubernetes Secrets `glassbox-mysql` (app, data) and `glassbox-app` | Created by hand from SSM with `k8s/bootstrap-secrets.sh` | Yes | The values survive (SSM `/glassbox/mysql/password`, `/glassbox/ip_hash_salt`, Terraform-managed in `infra/modules/secrets`), but nothing runs the script on boot. |
| ECR pull secret `regcred` | Systemd timer written by `user_data.sh` | Yes | Yes, on a new instance's first boot. |
| zram swap | SSM association `glassbox-zram-swap` (`infra/modules/compute/zram.tf`) | Host config is lost | **Not for a new instance ID.** The association targets `InstanceIds = [aws_instance.glassbox.id]`, so a replacement is not covered until a Terraform apply retargets it. |
| Public address | `aws_eip.glassbox` with `instance = aws_instance.glassbox.id`; Cloudflare's A record points at it (`infra/modules/edge`) | Kept (the EIP is its own resource) | Only through a Terraform apply. The subnet sets `map_public_ip_on_launch = false`, so a new instance has no internet access at all until the EIP is attached. |

**Redis caveat (found while scoping).** DD1 §6.5 describes a `reindex` Job that reloads embeddings from MySQL into Redis. It does not exist. `ingest/run.py` skips any document whose content hash and embedding model already match MySQL, and only writes `chunk:*` hashes for documents it re-embeds. So if Redis is lost but MySQL survives (or MySQL is restored from a dump), the next ingest skips everything and the vector index stays empty: every answer abstains. Today the workaround is `--clear` per corpus followed by a full re-embed. A two-way reindex/reconcile fix is planned in a separate PR (phase 3).

**Summary.** A replacement node today comes up as a bare k3s server with no workloads. Getting the site back is the manual "Manual apply / disaster recovery" sequence in `k8s/README.md`, run over an SSM session, plus a Flux re-bootstrap with a new GitHub credential and a Terraform apply for the EIP and zram. Content is rebuilt from Git for cents. The visitor question log is gone for good.

What protects the node today: EC2 simplified automatic recovery is on by default for supported instance types (no alarm, no notification), which moves the instance to new hardware on a failed **system** status check and keeps the instance ID, EIP and EBS volume. Nothing reacts to a failed **instance** status check or to a hung k3s; the 2026-09-30 memory incident needed the "Ops · Reboot node" runbook.

---

## 2. Design options (planned, not built yet)

None of these options is built. They are split into the cheap protections for the existing node and the options that replace it.

### 2.1 Cheap options that keep the current node (planned, not built yet)

| Option | What it is | Protects against | Recovery time | Data loss (RPO) | Cost per month | Blast radius and risk |
|---|---|---|---|---|---|---|
| (d) EC2 recovery and reboot alarms | CloudWatch alarm on `StatusCheckFailed_System` with the `recover` action (2 of 2 one-minute periods), a second on `StatusCheckFailed_Instance` with the `reboot` action (3 of 3), both notifying an SNS email topic. Set `maintenance_options { auto_recovery = "default" }` explicitly. | Host hardware/network failure; a kernel that stops answering status checks | 3 to 10 minutes; same instance, same IP, same disk | None | $0.20 for two alarms (free if the account stays within CloudWatch's 10 free alarms); SNS email free | Tiny. In-place, no instance change. A wrongly tuned reboot alarm could reboot a healthy node; status checks are binary, so that is unlikely. Does not help with instance termination, a lost volume or a k3s hang that still passes status checks. Works for the standalone instance only: recover actions are not supported for Auto Scaling group members, so phase 4b replaces these alarms with the group's health checks and notifications. |
| (c) Nightly MySQL dump to S3, restore runbook | A `mysql-backup` CronJob in `data` runs `mysqldump --single-transaction glassbox`, gzips it and uploads to a private, versioned, encrypted S3 bucket with a 30-day lifecycle, using the instance role through IMDS (hop limit is already 2). An approval-gated "Ops · Restore MySQL" runbook loads a chosen dump; a read-only "restore check" loads it into a scratch database and compares row counts. | Loss of the volume or instance; a bad migration or `--clear` | Restore itself is 1 to 3 minutes for a dump this size, after the cluster is up | Up to 24 h of `queries` (hourly dumps cost the same in practice) | Under $0.05 (dumps are a few MB; 30 kept); PUT requests negligible | Small. Read-only on MySQL except during an explicit restore. Backups contain visitor questions (no raw IPs), so the bucket stays private and short-lived. |
| (e) Daily EBS snapshots of the root volume | Data Lifecycle Manager policy on the tagged root volume, 7 kept | Everything on the node, crash-consistent (InnoDB and SQLite recover from a crash like after a power cut) | 15 to 30 minutes, by hand: create a volume or AMI from the snapshot and swap it in through Terraform | Up to 24 h | Roughly $0.30 to $0.60 (incremental snapshots of the used blocks at $0.05/GB) | Restore is a manual, rarely practised path. A cheap second safety net, not self-healing. |
| (g) External uptime probe | Scheduled GitHub Actions workflow curls `https://basel.engineering/readyz` every 15 minutes and fails loudly (GitHub emails on failure). No AWS access. | Being down without anyone knowing | Detection only | n/a | $0 (public repository) | None. Alerting, not healing. |

### 2.2 Options that replace the node (planned, not built yet)

| Option | What it is | Protects against | Recovery time | Data loss (RPO) | Cost per month | Blast radius and risk |
|---|---|---|---|---|---|---|
| (a) ASG of one, rebuilt from scratch | `aws_launch_template` plus `aws_autoscaling_group` (min = max = desired = 1) in the existing public subnet. User data becomes an idempotent bootstrap: install a pinned k3s, create Secrets from SSM, start MySQL and restore the newest dump, install Flux, let it migrate and roll out, and attach the EIP last, once the local `/readyz` passes. | Instance termination, failed EC2 health checks, an impaired host the recover action cannot fix | 10 to 20 minutes for an unplanned replacement (launch, k3s, image pulls, restore, migrate, reindex); seconds for a planned one, because the EIP moves last | Whatever (c) gives: up to 24 h of `queries` | $0 for the ASG and launch template; a short-lived auto-assigned public IPv4 during boot (a few cents a year) | Large one-time change: the live instance is replaced. Afterwards each replacement is a full rebuild, so a false-positive health check costs a restore. |
| (b) Persistent data volume | A separate gp3 volume (10 GB) holding `/var/lib/rancher` (k3s SQLite and both local-path PVCs). The replacement attaches and mounts it before k3s starts. k3s must run with a fixed `--node-name`, because local-path PVs carry node affinity to the old hostname. | Instance loss with no data loss at all | 5 to 10 minutes (no re-ingest, no restore) | Near zero | $0.80 for 10 GB gp3, plus its snapshots | Medium. The volume and the instance are pinned to one Availability Zone (already true: one subnet). Moving today's data onto it needs a maintenance window. Attach/mount ordering, a volume still attached to a dying instance, and a crash-dirty SQLite are new failure modes, each needing a game-day test. |
| (f) Application health self-report | Node timer checks k3s and `/readyz` every minute; after 5 failures it calls `autoscaling:SetInstanceHealth Unhealthy` (DD2 §3.4) | k3s or Traefik hung while EC2 checks pass | 10 to 20 minutes, same as (a) | As (a) | $0 | A false positive replaces a healthy node. Only worth it after (a) is proven, and a reboot should come first. |

### 2.3 Rejected options, and why (b) is not first (planned, not built yet)

**Rejected:** RDS ($14+/month, DD1 §10.5), a multi-node or HA control plane (DD2 §3.6, 2x to 3x node cost), EKS (control-plane fee), an EC2 interface endpoint for a private boot path (about $7/month). Cost, for a site whose only irreplaceable data is a question log.

**Why not (b) first.** It gives the best recovery point, but it changes where the live data lives, needs a maintenance window to move it, and adds the most new failure modes. The data it saves is either rebuildable from Git for cents or is the question log, which (c) already protects to within a day. It stays an owner option (decision 3 below).

---

## 3. Recommended staged plan (planned, not built yet)

Quick wins come first: alarms, backups and a restore runbook protect the live node without replacing it. The Auto Scaling group comes last, after a rehearsal on a throwaway instance that cannot touch production. Each phase is one small PR; phase 4 is three.

| # | Phase | Live effect | Owner approvals | Downtime | Cost per month |
|---|---|---|---|---|---|
| 1 | Recovery and reboot alarms, SNS email, uptime probe | Two alarms and an email topic | Bootstrap (CI and ops role IAM), then Terraform; confirm the SNS email | None | about $0.20 |
| 2 | MySQL backups to S3, backup-now and restore runbooks | New bucket, a nightly CronJob, two Ops runbooks | Bootstrap (CI and ops role IAM), Terraform (bucket, instance role, SSM documents), merge (Flux applies the CronJob) | None | under $0.05 |
| 3 | Reindex/reconcile, pinned versions, idempotent boot script, retargeted zram association, Flux credential decision | Ingest repairs the vector index for free; nothing else changes on the node | Merge; Terraform for the zram association; owner sets a GitHub branch ruleset (and adds a deploy key only if option F2 is chosen) | None | $0 |
| 4a | Launch template and ASG at capacity 0, plus a separate rehearsal launch template, role and group | Nothing runs until a rehearsal | Bootstrap (autoscaling IAM, rehearsal role), Terraform; each rehearsal is an Ops click | None | $0 (cents per rehearsal) |
| 4b | Cutover: ASG to 1, the new node takes the EIP once it is ready; old node retagged, then stopped | The node is replaced | **Owner's explicit go-ahead** for the window, a merge freeze, the backup runbook, then two Terraform applies | Seconds (the EIP move), plus any questions asked after the last backup | $0 |
| 4c | Retire the old instance after a week | Old stopped instance and its volume deleted | Terraform | None | saves the old volume's $1.60 |
| 5 | Optional: application health self-report and a "Replace node" runbook | Hung k3s triggers a reboot, then a replacement | Bootstrap, Terraform | 10 to 20 minutes per unplanned replacement | $0 |
| opt | Option (e) snapshots or option (b) data volume, if the owner chooses | See §2 | Bootstrap, Terraform; (b) also needs a maintenance window | (b): one window of about 20 minutes | (e) about $0.50; (b) about $1 |

### Phase 1 (planned): recovery alarms and an uptime probe

**Files**
- `infra/modules/compute/alarms.tf` (new): `aws_sns_topic.alerts` (`glassbox-alerts`) and an email subscription. The owner's address is a Terraform variable marked `sensitive = true`, supplied from the `terraform-prod` environment and never committed. `aws_cloudwatch_metric_alarm.recover` watches `StatusCheckFailed_System` (Maximum, 60 s, 2 of 2, `treat_missing_data = "missing"`) with the actions `arn:aws:automate:us-east-1:ec2:recover` and the topic. `aws_cloudwatch_metric_alarm.reboot` watches `StatusCheckFailed_Instance` (60 s, 3 of 3) with `arn:aws:automate:us-east-1:ec2:reboot` and the topic. The evaluation periods differ, per AWS guidance, so the two never race.
- `infra/modules/compute/main.tf`: `maintenance_options { auto_recovery = "default" }` on the instance. This records today's default and is an in-place update.
- `infra/bootstrap/main.tf`: `glassbox-ci` gains `cloudwatch:PutMetricAlarm`, `DeleteAlarms`, `DescribeAlarms`, `ListTagsForResource`, `TagResource` and `UntagResource` on `alarm:glassbox-*`. It also gains `sns:*` on `glassbox-*` topics, and `iam:CreateServiceLinkedRole` conditioned on `iam:AWSServiceName = events.amazonaws.com` (CloudWatch's EC2 alarm actions use the `AWSServiceRoleForCloudWatchEvents` service-linked role). `glassbox-ci-plan` gains the matching `cloudwatch:Describe*`/`List*` and `sns:Get*`/`List*` reads. `infra/bootstrap/runbooks.tf`: `glassbox-ops-read` and `glassbox-ops` gain `cloudwatch:DescribeAlarms` on `glassbox-*`.
- `.github/scripts/ops-run.sh`: prints the two alarms' states in every diagnose, from the runner. The node's own role gets no CloudWatch access.
- `.github/workflows/uptime.yml` (new): every 15 minutes, `curl -fsS --max-time 20 https://basel.engineering/readyz` with three attempts. No AWS credentials; `permissions` is only `contents: read`. GitHub disables scheduled workflows in a public repository after 60 days without repository activity, so the plan records a check in `project/BACKLOG.md` (re-enable it, or have a monthly manual run keep it alive).
- Docs: `infra/CI.md` (alarms and the probe), `k8s/README.md` "Incidents" (what an alarm email means).

**Order:** the Bootstrap PR and its approval-gated run first, then the Terraform PR (its plan shows 4 to add and 1 in-place change), then the owner confirms the SNS subscription email. An unconfirmed email subscription stays "pending confirmation", and Terraform cannot delete it until it expires (AWS removes it after about 3 days); a changed address therefore leaves a dangling pending subscription for a few days, which is harmless.
**Rollback:** revert the PR; the alarms and topic are destroyed in place. **Test without risk:** the alarms cannot be fired on purpose without impairing the node. Check that both show `OK` in "Ops · Diagnose". Do not force an alarm state with `set-alarm-state`: the action would really reboot or recover the node. Test the probe with one temporary run against a URL that returns 404.

**Lifetime:** these two alarms protect the standalone instance only. CloudWatch's recover action is not supported for instances in an Auto Scaling group, and an alarm's dimension is a fixed instance ID, so neither can follow the node into the group. Phase 4b deletes them. After that, the group's EC2 health checks replace an impaired instance, and the group's notifications (`aws_autoscaling_notification` for launch, terminate and their failures, sent to the same topic) plus the uptime probe tell the owner.

### Phase 2 (planned): MySQL backups to S3 and a restore runbook

**Files**
- `infra/modules/backup/` (new module, wired in `infra/envs/prod/main.tf`): `aws_s3_bucket.backups` (`glassbox-backups-<random suffix>`) with versioning, SSE-S3, a public access block, a TLS-only bucket policy, and a lifecycle that expires objects after 30 days and noncurrent versions after 7.
- `infra/modules/compute/main.tf`: an instance role statement allowing `s3:PutObject` on `mysql/*`, and `s3:GetObject` on `mysql/*` plus `s3:ListBucket` restricted to the `mysql/` prefix (for restores). There is no delete. **Note:** the role is reachable from every pod through IMDS (hop limit 2), so any pod could read the dumps, which contain the visitor question log. That is the same exposure as the live database credential in SSM today. Keep the grant on the `mysql/` prefix only, never the whole bucket, so a later prefix (for example rehearsal output) stays out of reach.
- `k8s/base/mysql-backup-cronjob.yaml` (new, in `data`): daily at 03:41 UTC, clear of `warm-answers` at :17. An init container from the pinned `mysql:8.0` image runs `mysqldump --single-transaction --routines --databases glassbox | gzip` into an `emptyDir`. It **refuses to produce a dump if `documents` is empty or `alembic_version` is missing**, so a freshly rebuilt, empty node never becomes the newest backup. The main container (`amazon/aws-cli`, pinned arm64 digest) uploads `mysql/YYYY/MM/DD/glassbox-<timestamp>.sql.gz`. Limits are 128Mi for each container; they run one after the other, so the nightly peak is about 128Mi. `concurrencyPolicy: Forbid`, `backoffLimit: 1`, label `app: mysql-backup`. It uses the existing `glassbox-mysql` Secret.
- `k8s/base/networkpolicy-data.yaml`: admit `app: mysql-backup` from the `data` namespace to MySQL on 3306.

#### Phase 2 runbooks, permissions and docs (planned)

- `infra/modules/ops`: two new documents.
  - `backup-mysql` creates a Job from the CronJob and waits. It prints the object key and size only.
  - `restore-mysql` takes a `mode` enum. `check` loads the newest dump (or the dump for a date passed as a `YYYY-MM-DD` parameter, which SSM validates with `allowedPattern`) into a scratch database `glassbox_restore_check`, prints per-table row counts against the live database, and drops the scratch database. `apply` suspends Flux `flux-system`, scales `api`, `retrieval-worker` and `warm-answers` to 0, and loads the dump into `glassbox`. It then **drops the Redis vector index and every `chunk:*` key** and lets the reindex/reconcile fix (separate PR, see phase 3) rebuild them from MySQL. Finally it bumps `corpus:ver:*` for both corpora, so retrieval and answer caches built on the pre-restore data are never replayed, scales back and resumes Flux.
- `.github/workflows/ops-backup-mysql.yml`, `ops-restore-mysql.yml` (new wrappers) and the matching `ops.yml` choices.
- `infra/bootstrap/main.tf`: `glassbox-ci` gains `s3:*` on `glassbox-backups-*` (bucket and objects); `glassbox-ci-plan` gains reads on it. `glassbox-ops` can already send the two new documents through its `glassbox-ops-*` grant.
- Docs: the `infra/CI.md` runbook table; `k8s/README.md` "Manual apply / disaster recovery" rewritten around the restore runbook; `docs/DESIGN.md` §10.5 (backups now exist); a `mysql-backup` row (about 128Mi, nightly and transient) in `docs/DESIGN.md` §9.7's memory table.

**Rollback:** delete the CronJob in Git. The bucket is kept (`prevent_destroy`) until the owner decides. **Test without risk:** "Ops · Backup MySQL now", then "Ops · Restore MySQL" in `check` mode. That touches only the scratch database, and it becomes the monthly drill. The `apply` mode is first exercised in the phase 4a rehearsal, never first on production.

### Phase 3 (planned): reconcile, pinned versions, boot script and the Flux credential

**Files**
- **Reindex:** the reindex/reconcile fix (separate PR). Its required behaviour, so restores and replacements rely on it: reconcile both ways between MySQL and Redis for the current embedding model. That means writing missing `chunk:*` hashes from the stored embeddings, deleting `chunk:*` keys that have no MySQL row, rewriting keys whose row or text changed, and bumping `corpus:ver:<corpus>` whenever it changed anything. It makes no Bedrock calls. The restore runbook's index drop is the blunt version of the same thing.
- `infra/modules/compute/bootstrap.sh` (new; the live instance does not use it): the replacement's boot sequence. Every step is safe to repeat. The **order is enforced by the script itself, before Flux exists**, because MySQL is otherwise a Flux workload that would start alongside migrate and the app:
  1. Swap file, then zram (same script as `zram-swap.sh`).
  2. Install k3s at a pinned version with `--node-name glassbox` and `--kubelet-arg=fail-swap-on=false`.
  3. `bootstrap-secrets.sh` (Secrets from SSM) and the ECR pull-secret timer.
  4. **MySQL ready:** fetch the `deploy` branch over public HTTPS at its current commit and `kubectl apply` only the `data` namespace, the MySQL Service and StatefulSet, and the data NetworkPolicies. Wait for `mysql-0` to be Ready.
  5. **Restore:** if the `glassbox` database has no `alembic_version` table, load the newest `mysql/` dump. If no dump exists, continue empty (first-ever boot). With an existing database it does nothing.
  6. **Flux:** install Flux (see the credential choice below) pointing at `deploy`. Flux adopts the already-applied MySQL objects (same manifests, so there is no change) and applies the rest.
  7. **Migrate, then the app, then ingest:** the recreated `migrate` Job upgrades the restored schema. The existing `wait-for-migrations` initContainers hold `api` and `retrieval-worker` until it finishes. The existing `app-ready` → `ingest` Kustomization chain runs ingest (and the reconcile) only after both are Ready. The CronJobs (`mysql-backup`, `warm-answers`) arrive with Flux, after the restore, and the backup refuses an empty database anyway.
  8. **EIP last:** poll `curl -H 'Host: basel.engineering' http://127.0.0.1/readyz` until it returns ready (give up after 30 minutes and leave the EIP where it is). Only then run `aws ec2 associate-address --allow-reassociation`. This step is skipped when `GLASSBOX_EIP_ALLOCATION` is empty, which is how the rehearsal template is built. The cutover then costs seconds, not the whole boot.

#### Phase 3 Flux credential, zram and versions (planned)

- **Flux credential (owner choice; F1 recommended):**
  - **F1, no write key on the node.** Move the image-tag bump out of the cluster: `release.yml` (or `sync-deploy-branch.yml`, which already pushes to `deploy` with the workflow token) commits the new `build-N` to `deploy`, and Flux keeps only its read side (ImageRepository and ImagePolicy can go too). The repository is public, so the node reads `deploy` over HTTPS with no credential at all, and a compromised pod has nothing to push with. This is a prerequisite PR to the deploy pipeline, reviewed on its own.
  - **F2, keep Flux image automation.** An ED25519 deploy key is generated by Terraform (`tls_private_key.flux`) and stored as a SecureString at `/glassbox-flux/deploy-key`. That path is deliberately **outside `/glassbox/*`**, so the instance role's existing wildcard does not cover it. A separate, exact-ARN `ssm:GetParameter` statement grants it, which keeps the grant visible and easy to remove. The owner adds the public half as a write-enabled deploy key in GitHub settings. **State it plainly:** the boot script reads it with the instance role, and every pod can reach that role through IMDS (hop limit 2, which Flux's ECR scan and the Bedrock calls need), so any compromised pod can read the key. Deploy keys are repository-wide, not branch-scoped.
  - **Either way, required before phase 4:** a GitHub branch ruleset on every branch except `deploy`, plus tags, that restricts creations, updates and deletions with **no bypass for deploy keys**. A leaked key (F2), or any other non-owner credential, can then push only to `deploy`. `deploy` is already equivalent to cluster control, which is why F1 is preferred.
- `infra/modules/compute/zram.tf`: the association targets `tag:project = glassbox` and `tag:Name = glassbox` instead of the instance ID, so any replacement is covered. The plan shows an in-place association update, which re-runs the idempotent script once.
- Pin the Flux CLI and k3s versions. Record the k3s version actually running (read from "Ops · Diagnose") as the pin.

**Rollback:** revert. **Test without risk:** the reconcile PR's unit and integration tests; `shellcheck` plus an offline test of `bootstrap.sh` with stubbed `aws`, `kubectl` and `flux` that asserts the step order (same pattern as `infra/modules/ops/tests`). The script runs for real only in the phase 4a rehearsal.

### Phase 4a (planned): launch templates, ASG at zero, and an isolated rehearsal

**Files**
- `infra/modules/compute/asg.tf` (new): `aws_launch_template.glassbox`. It uses the same AMI parameter, `t4g.small`, a 20 GB encrypted gp3 volume, `credit_specification standard`, IMDSv2 with hop limit 2, the existing instance profile and security group, and `user_data = bootstrap.sh` with the prod EIP allocation ID. `network_interfaces { associate_public_ip_address = true }` lets the instance reach SSM, ECR, S3 and EC2 before it has the EIP; AWS releases the auto-assigned address when the EIP is associated. `aws_autoscaling_group.glassbox` starts at `min = max = desired = 0`, in the one public subnet, with `health_check_type = "EC2"` and `health_check_grace_period = 1800`. The grace period covers a full boot, because the EIP now moves last. Tags `Name = glassbox` and `project = glassbox` use `propagate_at_launch` (the ops roles and `ops-run.sh` find the node by exactly these tags).

#### Phase 4a rehearsal isolation and permissions (planned)

- **Rehearsal, isolated from production by construction:**
  - (a) **Its own launch template**, `aws_launch_template.rehearsal`, whose user data has an empty `GLASSBOX_EIP_ALLOCATION` and `GLASSBOX_MODE=rehearsal`. The script never reads an allocation from tags or SSM, so the rehearsal has no EIP to take.
  - (b) **Its own role and instance profile**, `glassbox-rehearsal-instance`, with no `ec2:AssociateAddress` at all. The production role's `ec2:AssociateAddress` is limited to the one EIP allocation and to instances with `aws:ResourceTag/Name = glassbox`. A bug in either script therefore cannot move the production address.
  - (c) **Read-only Flux:** plain `flux install` (no image-automation controllers) and a `GitRepository` on the public HTTPS URL of `deploy`. No key is involved, so it can never push to `deploy`. Its Kustomization uses a new `k8s/overlays/rehearsal` overlay on top of prod.
  - (d) The rehearsal overlay **suspends the `mysql-backup` CronJob**. The rehearsal role may only `s3:GetObject` under `mysql/` (to restore) and `s3:PutObject` under `rehearsal/`, so even a manual backup there cannot land among production dumps.
  - (e) The same overlay **suspends the `warm-answers` CronJob** and sets `GLASSBOX_WARM_DAILY_LLM_CAP=0`, so the rehearsal makes no warm-up LLM calls. Ingest's re-embed is the only Bedrock spend, well under $0.05.
  - The group `glassbox-rehearsal` (desired 0) is tagged `Name = glassbox-rehearsal`, so `ops-run.sh`'s "exactly one glassbox instance" check and the production ops grants never see it.
- Production instance role: `ec2:AssociateAddress` (conditioned as above) and `ec2:DescribeAddresses`.
- `infra/bootstrap/main.tf`: `glassbox-ci` gains `autoscaling:*` on groups named `glassbox*`, `autoscaling:Describe*`, and `iam:CreateServiceLinkedRole` for `autoscaling.amazonaws.com`. `glassbox-ci-plan` gains `autoscaling:Describe*`. `infra/bootstrap/runbooks.tf`: `glassbox-ops` gains `autoscaling:SetDesiredCapacity` on `glassbox-rehearsal` only. `glassbox-ops-read` and `glassbox-ops` gain `ssm:SendCommand` with `glassbox-ops-diagnose` on instances tagged `Name = glassbox-rehearsal`.
- `.github/workflows/ops-rehearsal.yml` (new): "Ops · Rehearsal node up/down" sets the rehearsal group to 1 or 0. A "Diagnose rehearsal" choice runs the read-only diagnose against the rehearsal tag.

**Game day (the test that does not risk production):**
1. Run "Ops · Backup MySQL now", then "Ops · Rehearsal node up".
2. With "Diagnose rehearsal", measure the time until the node's local `/readyz` is ready (the node is not behind Cloudflare).
3. Compare the restored `queries` row count and the vector index size with production's diagnose. Ask two questions through the local API.
4. Run the `restore-mysql` `apply` mode there once.
5. "Rehearsal node down".

Record the times in the status report. Cost: about two cents of instance time.

### Phase 4b (planned): cutover

Both nodes run at once for a few minutes, so the old one is retagged first. Otherwise `ops-run.sh`'s tag lookup (it matches `Name` and `project` tags, including stopped instances) would find two instances and refuse every runbook.

1. **Owner go-ahead** for a quiet window. Freeze merges to `main` for the window, so neither node's Flux deploys or bumps a release mid-cutover. Run "Ops · Diagnose", then "Ops · Backup MySQL now" on the old node.
2. **Terraform PR 4b-1:**
   - Retag `aws_instance.glassbox` to `Name = glassbox-legacy` (an in-place tag change). The ops roles and runbooks then see only the ASG member; the old node is intentionally out of their reach for the rest of the window.
   - Set the ASG to desired 1.
   - Remove the `instance` argument from `aws_eip.glassbox` and add `lifecycle { ignore_changes = [instance, network_interface] }`, because the boot script owns the association from now on.
   - Delete phase 1's two instance alarms and add the ASG notifications.
   - Add the output `asg_name`.

   Apply it. The new node boots, restores the newest dump, comes up behind its local `/readyz`, and only then takes the EIP. Visitors see a blip of seconds. Questions asked on the old node after step 1's backup are lost.
3. Run "Ops · Diagnose" (it now finds the ASG instance). Check the site and the restored query count.
4. **Terraform PR 4b-2:** an `aws_ec2_instance_state` resource for the legacy instance with `state = "stopped"`. The old node stops; its volume is kept for rollback.

**Rollback (prepared as a PR before step 2):**
- Set the legacy instance's `aws_ec2_instance_state` to `running`, and retag it `Name = glassbox`.
- Set the ASG to desired 0 in the same change, so the ops lookup finds exactly one instance again.
- Add an `aws_eip_association` from the EIP to the legacy instance.

Applying it starts the old node and moves the address back. Questions logged on the new node meanwhile are lost.

### Phase 4c (planned): retire the old instance

After a week of normal operation and one successful nightly backup from the new node, remove `aws_instance.glassbox`, its state resource and its zram association target. Update `docs/DESIGN.md` §10.4 (no more `ignore_changes = [ami]` replacement warning), `docs/architecture/deep-dive.md`, SNAPSHOT and the deep dive's planned section.

### Phase 5 (planned, optional): application health and a replace runbook

The systemd timer from DD2 §3.4, changed to reboot first and to call `autoscaling:SetInstanceHealth` only if the node is still unhealthy 15 minutes after a reboot. Add "Ops · Replace node" (`autoscaling:TerminateInstanceInAutoScalingGroup` without decrementing capacity) to `glassbox-ops` through Bootstrap. Only after the owner has seen one real replacement go well.

---

## 4. IAM and workflow changes, collected (planned, not built yet)

| Role (where defined) | Adds | Phase |
|---|---|---|
| `glassbox-ci` (`infra/bootstrap/main.tf`) | CloudWatch alarms and SNS on `glassbox-*`; `iam:CreateServiceLinkedRole` for `events.amazonaws.com` | 1 |
| `glassbox-ci` | `s3:*` on `glassbox-backups-*` | 2 |
| `glassbox-ci` | `autoscaling:*` on `glassbox*` groups; service-linked role for `autoscaling.amazonaws.com` (it already has `ec2:*` for launch templates and `iam:PassRole` for `glassbox-*` to EC2, which covers the rehearsal role) | 4a |
| `glassbox-ci-plan` | Matching read-only actions for each of the above | 1, 2, 4a |
| `glassbox-instance` (`infra/modules/compute/main.tf`) | `s3:PutObject` and `GetObject` on `mysql/*`, `s3:ListBucket` on the `mysql/` prefix; `ec2:AssociateAddress` on the one allocation for instances tagged `Name = glassbox`, and `ec2:DescribeAddresses`; option F2 only: exact-ARN `ssm:GetParameter` on `/glassbox-flux/deploy-key`; later `autoscaling:SetInstanceHealth` on the group | 2, 3, 4a, 5 |
| `glassbox-rehearsal-instance` (new, `infra/modules/compute`) | Same Bedrock, ECR and `/glassbox/*` reads as production; `s3:GetObject` on `mysql/*`, `s3:PutObject` on `rehearsal/*`; **no** `ec2:AssociateAddress` and no Flux key | 4a |
| `glassbox-ops-read`, `glassbox-ops` (`infra/bootstrap/runbooks.tf`) | `cloudwatch:DescribeAlarms` on `glassbox-*` | 1 |
| `glassbox-ops-read`, `glassbox-ops` | `ssm:SendCommand` with `glassbox-ops-diagnose` on instances tagged `Name = glassbox-rehearsal` | 4a |
| `glassbox-ops` | New `glassbox-ops-*` documents need no change; `autoscaling:SetDesiredCapacity` on `glassbox-rehearsal`; later terminate-in-group | 4a, 5 |
| GitHub | `uptime.yml`; `ops-backup-mysql.yml`, `ops-restore-mysql.yml`, `ops-rehearsal.yml`; `ops.yml` choices; the alert email as a sensitive Terraform variable in `terraform-prod`; a branch ruleset that leaves deploy keys only `deploy`; option F1: the tag bump in `release.yml` or `sync-deploy-branch.yml` | 1, 2, 3, 4a |

Every IAM change reaches production only through the Bootstrap workflow's approval and fingerprint check, before the Terraform PR that needs it.

---

## 5. Open owner decisions on the planned work

1. **Cost ceiling.** Phases 1 to 4 add about $0.25 a month. Option (e) snapshots add about $0.50; option (b), a persistent volume, about $1. Is $1 a month the ceiling?
2. **Acceptable downtime.** The planned cutover costs seconds because the EIP moves last. An unplanned replacement costs 10 to 20 minutes (versus 3 to 10 minutes for a recover or reboot, which phase 1 covers until the cutover). If replacements must be under 10 minutes, choose option (b).
3. **Does the question log matter?** If losing up to a day of `queries` is fine, nightly dumps are enough. If not, choose hourly dumps (still about free) or option (b). If it does not matter at all, phase 2 could drop to a weekly dump.
4. **Flux credential:** F1 (no write key on the node; the release workflow bumps `deploy`), recommended, or F2 (an SSM-stored deploy key every pod could read).
5. **Alert email.** Which address the SNS topic notifies (stored as a sensitive variable, never in Git).
6. **Go-ahead for phase 4b**, the one step that replaces the live node.
