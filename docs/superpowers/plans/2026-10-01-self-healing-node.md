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

**Redis caveat (found while scoping).** DD1 §6.5 describes a `reindex` Job that reloads embeddings from MySQL into Redis. It does not exist. `ingest/run.py` skips any document whose content hash and embedding model already match MySQL, and only writes `chunk:*` hashes for documents it re-embeds. So if Redis is lost but MySQL survives (or MySQL is restored from a dump), the next ingest skips everything and the vector index stays empty: every answer abstains. Today the workaround is `--clear` per corpus followed by a full re-embed. A reindex is planned in phase 3.

**Summary.** A replacement node today comes up as a bare k3s server with no workloads. Getting the site back is the manual "Manual apply / disaster recovery" sequence in `k8s/README.md`, run over an SSM session, plus a Flux re-bootstrap with a new GitHub credential and a Terraform apply for the EIP and zram. Content is rebuilt from Git for cents. The visitor question log is gone for good.

What protects the node today: EC2 simplified automatic recovery is on by default for supported instance types (no alarm, no notification), which moves the instance to new hardware on a failed **system** status check and keeps the instance ID, EIP and EBS volume. Nothing reacts to a failed **instance** status check or to a hung k3s; the 2026-09-30 memory incident needed the "Ops · Reboot node" runbook.

---

## 2. Design options (planned, not built yet)

None of these options is built. They are split into the cheap protections for the existing node and the options that replace it.

### 2.1 Cheap options that keep the current node (planned, not built yet)

| Option | What it is | Protects against | Recovery time | Data loss (RPO) | Cost per month | Blast radius and risk |
|---|---|---|---|---|---|---|
| (d) EC2 recovery and reboot alarms | CloudWatch alarm on `StatusCheckFailed_System` with the `recover` action (2 of 2 one-minute periods), a second on `StatusCheckFailed_Instance` with the `reboot` action (3 of 3), both notifying an SNS email topic. Set `maintenance_options { auto_recovery = "default" }` explicitly. | Host hardware/network failure; a kernel that stops answering status checks | 3 to 10 minutes; same instance, same IP, same disk | None | $0.20 for two alarms (free if the account stays within CloudWatch's 10 free alarms); SNS email free | Tiny. In-place, no instance change. A wrongly tuned reboot alarm could reboot a healthy node; status checks are binary, so that is unlikely. Does not help with instance termination, a lost volume or a k3s hang that still passes status checks. |
| (c) Nightly MySQL dump to S3, restore runbook | A `mysql-backup` CronJob in `data` runs `mysqldump --single-transaction glassbox`, gzips it and uploads to a private, versioned, encrypted S3 bucket with a 30-day lifecycle, using the instance role through IMDS (hop limit is already 2). An approval-gated "Ops · Restore MySQL" runbook loads a chosen dump; a read-only "restore check" loads it into a scratch database and compares row counts. | Loss of the volume or instance; a bad migration or `--clear` | Restore itself is 1 to 3 minutes for a dump this size, after the cluster is up | Up to 24 h of `queries` (hourly dumps cost the same in practice) | Under $0.05 (dumps are a few MB; 30 kept); PUT requests negligible | Small. Read-only on MySQL except during an explicit restore. Backups contain visitor questions (no raw IPs), so the bucket stays private and short-lived. |
| (e) Daily EBS snapshots of the root volume | Data Lifecycle Manager policy on the tagged root volume, 7 kept | Everything on the node, crash-consistent (InnoDB and SQLite recover from a crash like after a power cut) | 15 to 30 minutes, by hand: create a volume or AMI from the snapshot and swap it in through Terraform | Up to 24 h | Roughly $0.30 to $0.60 (incremental snapshots of the used blocks at $0.05/GB) | Restore is a manual, rarely practised path. A cheap second safety net, not self-healing. |
| (g) External uptime probe | Scheduled GitHub Actions workflow curls `https://basel.engineering/readyz` every 15 minutes and fails loudly (GitHub emails on failure). No AWS access. | Being down without anyone knowing | Detection only | n/a | $0 (public repository) | None. Alerting, not healing. |

### 2.2 Options that replace the node (planned, not built yet)

| Option | What it is | Protects against | Recovery time | Data loss (RPO) | Cost per month | Blast radius and risk |
|---|---|---|---|---|---|---|
| (a) ASG of one, rebuilt from scratch | `aws_launch_template` plus `aws_autoscaling_group` (min = max = desired = 1) in the existing public subnet. User data becomes an idempotent bootstrap: attach the EIP, install a pinned k3s, create Secrets from SSM, bootstrap Flux from an SSM-stored deploy key, restore the newest dump, let Flux roll out. | Instance termination, failed EC2 health checks, an impaired host the recover action cannot fix | 10 to 20 minutes (launch, k3s, image pulls, restore, migrate, ingest or reindex) | Whatever (c) gives: up to 24 h of `queries` | $0 for the ASG and launch template; a short-lived auto-assigned public IPv4 during boot (a few cents a year) | Large one-time change: the live instance is replaced. Afterwards each replacement is a full rebuild, so a false-positive health check costs a restore. |
| (b) Persistent data volume | A separate gp3 volume (10 GB) holding `/var/lib/rancher` (k3s SQLite and both local-path PVCs). The replacement attaches and mounts it before k3s starts. k3s must run with a fixed `--node-name`, because local-path PVs carry node affinity to the old hostname. | Instance loss with no data loss at all | 5 to 10 minutes (no re-ingest, no restore) | Near zero | $0.80 for 10 GB gp3, plus its snapshots | Medium. The volume and the instance are pinned to one Availability Zone (already true: one subnet). Moving today's data onto it needs a maintenance window. Attach/mount ordering, a volume still attached to a dying instance, and a crash-dirty SQLite are new failure modes, each needing a game-day test. |
| (f) Application health self-report | Node timer checks k3s and `/readyz` every minute; after 5 failures it calls `autoscaling:SetInstanceHealth Unhealthy` (DD2 §3.4) | k3s or Traefik hung while EC2 checks pass | 10 to 20 minutes, same as (a) | As (a) | $0 | A false positive replaces a healthy node. Only worth it after (a) is proven, and a reboot should come first. |
| Rejected | RDS ($14+/month, DD1 §10.5), a multi-node or HA control plane (DD2 §3.6, 2x to 3x node cost), EKS (control-plane fee), an EC2 interface endpoint for a private boot path (about $7/month) | | | | | Cost, for a site whose only irreplaceable data is a question log. |

**Why not (b) first.** It gives the best recovery point, but it changes where the live data lives, needs a maintenance window to move it, and adds the most new failure modes. The data it saves is either rebuildable from Git for cents or is the question log, which (c) already protects to within a day. It stays an owner option (decision 3 below).

---

## 3. Recommended staged plan (planned, not built yet)

Quick wins first: alarms, backups and a restore runbook protect the live node without replacing it. The Auto Scaling group comes last, after a rehearsal on a throwaway instance. Each phase is one small PR (phase 4 is three).

| # | Phase | Live effect | Owner approvals | Downtime | Cost per month |
|---|---|---|---|---|---|
| 1 | Recovery and reboot alarms, SNS email, uptime probe | Two alarms and an email topic | Bootstrap (CI role IAM), then Terraform | None | about $0.20 |
| 2 | MySQL backups to S3, backup-now and restore runbooks | New bucket, a nightly CronJob, two Ops runbooks | Bootstrap (CI and ops role IAM), Terraform (bucket, instance role, SSM documents), merge (Flux applies the CronJob) | None | under $0.05 |
| 3 | Reindex from MySQL, pinned versions, idempotent bootstrap script, retargeted zram association | Ingest repairs a missing vector index for free; nothing else changes on the node | Merge; Terraform for the zram association and the Flux key parameter; owner adds one deploy key in GitHub settings | None | $0 |
| 4a | Launch template and ASG at capacity 0, plus a throwaway rehearsal group | Nothing running until a rehearsal | Bootstrap (autoscaling IAM), Terraform | None | $0 (cents per rehearsal) |
| 4b | Cutover: ASG to 1, EIP moves to the new node | The node is replaced | Terraform, plus the backup runbook immediately before | 10 to 20 minutes, in a chosen window | $0 |
| 4c | Retire the old instance after a week | Old stopped instance and its volume deleted | Terraform | None | saves the old volume's $1.60 |
| 5 | Optional: application health self-report and a "Replace node" runbook | Hung k3s triggers a reboot, then a replacement | Bootstrap, Terraform | 10 to 20 minutes per replacement | $0 |
| opt | Option (e) snapshots or option (b) data volume, if the owner chooses | See §2 | Bootstrap, Terraform; (b) also needs a maintenance window | (b): one window of about 20 minutes | (e) about $0.50; (b) about $1 |

### Phase 1 (planned): recovery alarms and an uptime probe

**Files**
- `infra/modules/compute/alarms.tf` (new): `aws_sns_topic.alerts` (`glassbox-alerts`) and an email subscription (owner's address as a Terraform variable from the `terraform-prod` environment, not committed), `aws_cloudwatch_metric_alarm.recover` (`StatusCheckFailed_System`, Maximum, 60 s, 2 of 2, `treat_missing_data = "missing"`, actions `arn:aws:automate:us-east-1:ec2:recover` and the topic), `aws_cloudwatch_metric_alarm.reboot` (`StatusCheckFailed_Instance`, 60 s, 3 of 3, action `arn:aws:automate:us-east-1:ec2:reboot` and the topic). Different evaluation periods per AWS guidance, so the two never race.
- `infra/modules/compute/main.tf`: `maintenance_options { auto_recovery = "default" }` on the instance (records today's default; an in-place update).
- `infra/bootstrap/main.tf`: `glassbox-ci` gains `cloudwatch:PutMetricAlarm`, `DeleteAlarms`, `DescribeAlarms`, `ListTagsForResource`, `TagResource`, `UntagResource` on `alarm:glassbox-*`; `sns:*` on `glassbox-*` topics; `iam:CreateServiceLinkedRole` conditioned on `iam:AWSServiceName = events.amazonaws.com` (CloudWatch's EC2 alarm actions use the `AWSServiceRoleForCloudWatchEvents` service-linked role). `glassbox-ci-plan` gains the matching `cloudwatch:Describe*`/`List*` and `sns:Get*`/`List*` reads.
- `.github/workflows/uptime.yml` (new): every 15 minutes, `curl -fsS --max-time 20 https://basel.engineering/readyz`, three attempts. No AWS credentials, `permissions: {}` except `contents: read`.
- Docs: `infra/CI.md` (alarms and the probe), `k8s/README.md` "Incidents" (what an alarm email means).

**Order:** Bootstrap PR first and its approval-gated run, then the Terraform PR (plan shows 4 to add, 1 in-place change), then the owner confirms the SNS subscription email.
**Rollback:** revert the PR; the alarms and topic are destroyed in place. **Test without risk:** the alarms cannot be fired on purpose without impairing the node. Check them with "Ops · Diagnose": in this PR, `.github/scripts/ops-run.sh` prints the two alarms' states (`OK` expected) from the runner, which needs `cloudwatch:DescribeAlarms` on `glassbox-*` for `glassbox-ops-read` and `glassbox-ops` in the same Bootstrap change (the node's own role gets no CloudWatch access). Do not force an alarm state with `set-alarm-state`: the action would really reboot or recover the node. The probe is tested by a temporary run against a URL that returns 404.

### Phase 2 (planned): MySQL backups to S3 and a restore runbook

**Files**
- `infra/modules/backup/` (new module, wired in `infra/envs/prod/main.tf`): `aws_s3_bucket.backups` (`glassbox-backups-<random suffix>`), versioning, SSE-S3, public access block, TLS-only bucket policy, lifecycle expiring objects after 30 days and noncurrent versions after 7.
- `infra/modules/compute/main.tf`: instance role statement `s3:PutObject` on `backups/mysql/*` and `s3:GetObject`, `s3:ListBucket` on the bucket (restore reads it). No delete.
- `k8s/base/mysql-backup-cronjob.yaml` (new, in `data`): daily at 03:41 UTC (clear of `warm-answers` at :17). An init container from the pinned `mysql:8.0` image runs `mysqldump --single-transaction --routines --databases glassbox | gzip` into an `emptyDir`; the main container (`amazon/aws-cli`, pinned arm64 digest) uploads `mysql/YYYY/MM/DD/glassbox-<timestamp>.sql.gz`. Limits about 128Mi each, `concurrencyPolicy: Forbid`, `backoffLimit: 1`, label `app: mysql-backup`. Uses the existing `glassbox-mysql` Secret.
- `k8s/base/networkpolicy-data.yaml`: admit `app: mysql-backup` from the `data` namespace to MySQL on 3306.
- `infra/modules/ops`: documents `backup-mysql` (creates a Job from the CronJob and waits; prints the object key and size only) and `restore-mysql` with a `mode` enum: `check` loads the newest dump (or the dump of a date given as a `YYYY-MM-DD` parameter that SSM checks with `allowedPattern`) into a scratch database `glassbox_restore_check`, prints per-table row counts against the live database and drops it; `apply` suspends Flux `flux-system`, scales `api`, `retrieval-worker` and `warm-answers` to 0, loads the dump into `glassbox`, runs the reindex (phase 3; until then, documents the `--clear` plus re-ingest fallback), scales back and resumes Flux.
- `.github/workflows/ops-backup-mysql.yml`, `ops-restore-mysql.yml` (new wrappers) and `ops.yml` choices.
- `infra/bootstrap/main.tf`: `glassbox-ci` gains `s3:*` on `glassbox-backups-*` (bucket and objects); `glassbox-ci-plan` gains reads on it. `infra/bootstrap/runbooks.tf`: `glassbox-ops` may send the two new documents (already covered by `glassbox-ops-*`), nothing else.
- Docs: `infra/CI.md` runbook table, `k8s/README.md` "Manual apply / disaster recovery" rewritten around the restore runbook, `docs/DESIGN.md` §10.5 (backups now exist).

**Rollback:** delete the CronJob in Git; the bucket is kept (`prevent_destroy`) until the owner decides. **Test without risk:** "Ops · Backup MySQL now", then "Ops · Restore MySQL" in `check` mode. That touches only the scratch database and is the monthly drill. The `apply` mode is first exercised in the phase 4a rehearsal, never first on production.

### Phase 3 (planned): reindex, pinned versions and an idempotent bootstrap script

**Files**
- `services/glassbox/ingest/run.py` and `redis_index.py`: before scanning, compare MySQL's chunk ids per corpus and model with the `chunk:*` keys in Redis (`SCAN` or `FT.INFO` num_docs as a fast path) and rewrite missing hashes from the stored embeddings, with no Bedrock calls. Tests in `services/tests/test_ingest_run.py`. Closes the "reindex Job" gap in DD1 §6.5.
- `infra/modules/compute/bootstrap.sh` (new, not yet used by the live instance): the boot sequence for phase 4, every step safe to repeat: swap file; read the instance ID (IMDSv2); `aws ec2 associate-address --allow-reassociation` (skipped when `GLASSBOX_EIP_ALLOCATION` is empty, for rehearsals); install k3s at a pinned version with `--node-name glassbox` and `--kubelet-arg=fail-swap-on=false`; zram (same script as `zram-swap.sh`); `bootstrap-secrets.sh`; the ECR timer; restore the newest dump into an empty MySQL before Flux starts the app; `flux bootstrap git` against `deploy` with the deploy key from SSM, then let Flux apply.
- The SSM parameters module (`infra/modules/secrets`) gains an ED25519 `tls_private_key.flux`, stored as the SecureString parameter `/glassbox/flux/deploy-key`. The public half is a Terraform output. The owner adds it once as a write-enabled deploy key in GitHub's repository settings (no command). The private key also lives in the Terraform state, the same way the generated database credential already does.
- `infra/modules/compute/zram.tf`: association targets `tag:project = glassbox` and `tag:Name = glassbox` instead of the instance ID, so any replacement is covered. Plan shows an in-place association update, which re-runs the idempotent script once.
- Pin the Flux CLI version in `user_data.sh`'s successor and record the k3s version actually running (read from "Ops · Diagnose") as the pin.

**Rollback:** revert. **Test without risk:** unit tests for the reindex; `shellcheck` and an offline test of `bootstrap.sh` with stubbed `aws`, `kubectl` and `flux` (same pattern as `infra/modules/ops/tests`); the script runs for real only in the phase 4a rehearsal.

### Phase 4a (planned): launch template, ASG at zero, and a rehearsal group

**Files**
- `infra/modules/compute/asg.tf` (new): `aws_launch_template.glassbox` (same AMI parameter, `t4g.small`, 20 GB encrypted gp3, `credit_specification standard`, IMDSv2 with hop limit 2, instance profile, security group, `user_data = bootstrap.sh`, `network_interfaces { associate_public_ip_address = true }` so the instance can reach SSM, ECR and EC2 before the EIP is attached; AWS releases the auto-assigned address when the EIP is associated). `aws_autoscaling_group.glassbox` with `min = max = desired = 0`, the one public subnet, `health_check_type = "EC2"`, `health_check_grace_period = 900`, tags `Name = glassbox` and `project = glassbox` with `propagate_at_launch` (the ops roles and `ops-run.sh` find the node by exactly these tags).
- A second group `glassbox-rehearsal` from the same template, desired 0, tags `Name = glassbox-rehearsal` (so `ops-run.sh`'s "exactly one glassbox instance" check and the ops roles never see it), with `GLASSBOX_EIP_ALLOCATION` empty and the Flux image-update automation left suspended, so it never pushes to `deploy`.
- Instance role: `ec2:AssociateAddress` on the EIP allocation and on instances tagged `project = glassbox`; `ec2:DescribeAddresses`.
- `infra/bootstrap/main.tf`: `glassbox-ci` gains `autoscaling:*` on groups named `glassbox*` and `autoscaling:Describe*`, plus `iam:CreateServiceLinkedRole` for `autoscaling.amazonaws.com`; `glassbox-ci-plan` gains `autoscaling:Describe*`. `glassbox-ops` gains `autoscaling:SetDesiredCapacity` on `glassbox-rehearsal` only.
- `.github/workflows/ops-rehearsal.yml` (new): "Ops · Rehearsal node up/down" sets the rehearsal group to 1 or 0; a read-only diagnose variant targets it by its own tag.

**Game day (the test that does not risk production):** run "Ops · Backup MySQL now", then "Ops · Rehearsal node up". On the rehearsal node, measure time to `/readyz` ready through `curl` on the node (it is not behind Cloudflare), check the restored `queries` row count and the vector index size against production's diagnose, ask two questions through the local API, then "Rehearsal node down". Record the times in the status report. Cost: about two cents of instance time and well under $0.05 of embeddings.

### Phase 4b (planned): cutover

1. Owner picks a quiet window. "Ops · Backup MySQL now".
2. Terraform PR: ASG desired 1; `aws_eip.glassbox` loses its `instance` argument (the boot script owns the association from now on, `lifecycle { ignore_changes = [instance, network_interface] }`); the old `aws_instance` stays (stopped by a separate Ops step, not destroyed); the recovery alarms from phase 1 move to the ASG's instance or are replaced by ASG health checks; outputs gain `asg_name`.
3. Apply. The new instance boots, takes the EIP (the site is down from here), restores the dump, Flux rolls out, ingest reindexes. Expected 10 to 20 minutes of downtime; questions asked on the old node after step 1's backup are lost.
4. "Ops · Diagnose" (it now finds the ASG instance by tag), check the site and the restored query count.

**Rollback:** a prepared revert PR restores `instance = aws_instance.glassbox.id` on the EIP and sets the ASG to 0; applying it moves the EIP back to the untouched old instance. Questions logged on the new node meanwhile are lost.

### Phase 4c (planned): retire the old instance

After a week of normal operation and one successful nightly backup from the new node, remove `aws_instance.glassbox` and its SSM association target. Also update `docs/DESIGN.md` §10.4 (no more `ignore_changes = [ami]` replacement warning), `docs/architecture/deep-dive.md`, SNAPSHOT and the deep dive's planned section.

### Phase 5 (planned, optional): application health and a replace runbook

Systemd timer from DD2 §3.4, changed to reboot first and call `autoscaling:SetInstanceHealth` only if the node is still unhealthy 15 minutes after a reboot. Add "Ops · Replace node" (`autoscaling:TerminateInstanceInAutoScalingGroup` without decrementing capacity) to `glassbox-ops` through Bootstrap. Only after the owner has seen one real replacement go well.

---

## 4. IAM and workflow changes, collected (planned, not built yet)

| Role (where defined) | Adds | Phase |
|---|---|---|
| `glassbox-ci` (`infra/bootstrap/main.tf`) | CloudWatch alarms and SNS on `glassbox-*`; `iam:CreateServiceLinkedRole` for `events.amazonaws.com` | 1 |
| `glassbox-ci` | `s3:*` on `glassbox-backups-*` | 2 |
| `glassbox-ci` | `autoscaling:*` on `glassbox*` groups; service-linked role for `autoscaling.amazonaws.com` (already has `ec2:*` for launch templates and `iam:PassRole` for `glassbox-*` to EC2) | 4a |
| `glassbox-ci-plan` | Matching read-only actions for each of the above | 1, 2, 4a |
| `glassbox-instance` (`infra/modules/compute/main.tf`) | `s3:PutObject`/`GetObject`/`ListBucket` on the backup bucket; `ec2:AssociateAddress`, `ec2:DescribeAddresses`; later `autoscaling:SetInstanceHealth` on the group | 2, 4a, 5 |
| `glassbox-ops-read`, `glassbox-ops` (`infra/bootstrap/runbooks.tf`) | `cloudwatch:DescribeAlarms` on `glassbox-*` (alarm state in every runbook's diagnose) | 1 |
| `glassbox-ops` (`infra/bootstrap/runbooks.tf`) | New `glassbox-ops-*` documents need no change; `autoscaling:SetDesiredCapacity` on the rehearsal group; later terminate-in-group | 4a, 5 |
| GitHub workflows | `uptime.yml`; `ops-backup-mysql.yml`, `ops-restore-mysql.yml`, `ops-rehearsal.yml`; `ops.yml` choices; Terraform environment variable for the alert email | 1, 2, 4a |

Every IAM change reaches production only through the Bootstrap workflow's approval and fingerprint check, before the Terraform PR that needs it.

---

## 5. Open owner decisions on the planned work

1. **Cost ceiling.** Phases 1 to 4 add about $0.25 a month. Option (e) snapshots add about $0.50; option (b) a persistent volume about $1. Is $1 a month the ceiling?
2. **Acceptable downtime.** One planned window of 10 to 20 minutes for the cutover, and 10 to 20 minutes for each unplanned replacement afterwards (versus 3 to 10 minutes for a recover or reboot, which phase 1 already covers). If replacements must be under 10 minutes, choose option (b).
3. **Does the question log matter?** If losing up to a day of `queries` is fine, nightly dumps are enough. If not: hourly dumps (still about free) or option (b). If it does not matter at all, phase 2 could drop to a weekly dump.
4. **Alert email.** Which address the SNS topic and the uptime probe should notify.
5. **Go-ahead for phase 4b**, the one step that replaces the live node.
