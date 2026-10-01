# Glassbox Design Doc 003: Content Ingestion Pipeline

| | |
|---|---|
| **Status** | Draft v1 |
| **Owner** | Basel |
| **Last updated** | 2026-09-28 |
| **Builds on** | `DESIGN.md` ("DD1") and `DESIGN-002-followups.md` ("DD2") |
| **Supersedes** | DD1 6.4 (ingestion job) and parts of DD1 7.1 and DD2 4.x (see section 16) |

---

## 1. Summary

DD1 baked corpus files into the ingest container image and re-ran ingestion on every deploy. That couples content to code, forces a redeploy to fix a typo, and would leak private content through the public image registry.

This document replaces it with a standard production ingestion pipeline:

```
Sources            Connectors         Raw zone (S3)       Events (SQS)       Ingestion (KEDA ScaledJob)     Processed (MySQL)     Index (Redis)
Google Docs  --->  Drive sync  --->  raw/about_me/  --->  one message  --->  parse, validate, chunk,  ---> documents, chunks, ---> vector search
Git repo     --->  CI sync     --->  raw/about_system/    per change         embed, upsert, delete        embeddings           (rebuildable)
                                                              |
                                                              +--> dead-letter queue after 3 failures

Nightly reconciliation compares every stage and repairs drift.
```

Each stage has one job and can be rerun independently. Later stages can always be rebuilt from earlier ones.

**Guiding rule:** container images contain code only. Content flows through storage the system reads at runtime.

### 1.1 What the current ingest Job does about deleted files and Redis drift

Everything else in this document describes the connector pipeline. The ingest Job that runs today (`services/glassbox/ingest/run.py`, DD1 6.4) still reads the files baked into the image. Since 2026-10-01 it handles deleted and renamed files like this:

- **On by default (report only):** after a run has walked every file, it logs each indexed document whose file is gone, per corpus and embedding model ("would delete ..."). Nothing is deleted; those documents stay searchable.
- **Off by default (switch: `GLASSBOX_INGEST_SWEEP=apply` on the Job, or `--sweep`):** the same list is deleted: Redis `chunk:{id}` keys first, then the `corpus:ver:{corpus}` bump, then the MySQL chunk and document rows. A document keeps its row while it still has chunks for another embedding model.
- **Guards (both modes):** no sweep for a corpus whose scan found zero files; none when a source directory that has indexed documents (`infra`, `k8s`, `services`, `docs`, `corpus/about-me`) produced zero scanned files, since each is under the fraction limit on its own and an image that stopped copying one would otherwise lose it quietly; and none when more than 30% of a corpus's indexed documents would go (`GLASSBOX_INGEST_SWEEP_MAX_FRACTION`). Two or fewer deletions are always allowed, so renaming files in the five-file About Basel corpus works, which also means 2 of its 5 documents (40%) can go in one run without tripping the limit. `--force-sweep` lifts the directory and fraction guards, never the zero-file one. A refusal logs at `ERROR` and prints a `!!! STALE SWEEP REFUSED ... !!!` banner; the run still succeeds and nothing is recorded on `ingestion_runs`.
- **A failed `--clear`:** if it fails after deleting Redis keys, the MySQL rows remain, and the next ingest's reconcile (below) writes their keys back instead of finishing the wipe. Re-run `--clear` to finish, then ingest.
- **Redis reconcile (every run, since 2026-10-01):** after the scan and the sweep, the Job compares MySQL chunks with Redis `chunk:{id}` keys for the configured embedding model and repairs drift from MySQL alone (text, metadata and the stored vector; no embedding call): a missing key is written, a key whose `content_sha` (SHA-256 of the chunk text), corpus, model tag, document id or path disagrees with its row is rewritten (keys written before `content_sha` existed are rewritten once), and a key with no MySQL row is deleted along with its `chunktxt:{id}` cache. It runs even when no file changed. Before it, a lost Redis volume, a FLUSHALL or a MySQL restore left `idx:chunks` empty or partial with nothing to rebuild it, and every answer abstained. Guard: a corpus with zero MySQL chunks but Redis keys gets no deletions (`ERROR` plus a `!!! REDIS RECONCILE REFUSED ... !!!` banner). `corpus:ver:{corpus}` is bumped only when something changed, and the Job log prints `reconcile <corpus>: repaired=N rewritten=N removed=N`. `--reindex` rewrites every key from MySQL without scanning files (for after a MySQL restore); it is idempotent. Assumes one ingest at a time.
- **Operator commands:** `python -m services.glassbox.ingest.run --dry-run` prints the list without ingesting or writing anything. `--clear --corpus about_me|about_system [--model M] [--dry-run] [--yes]` wipes one corpus and model's documents, chunks and Redis keys for a clean re-ingest; it asks you to type the corpus name unless `--yes` is given, and the Job never runs it. `--reindex` rebuilds the Redis chunk keys from MySQL (see the reconcile above).

The connector design below (sections 8 and 11) replaces this with event-driven deletes and nightly reconciliation; the MySQL-to-Redis rows of section 11's table ("Chunks in MySQL missing from Redis index", "Vectors in Redis with no MySQL chunk") already run in every ingest Job.

---

## 2. Goals and non-goals

### Goals

- **Easy authoring:** "About Basel" content is written in Google Docs, editable from any device, live in the chatbot within about 15 minutes.
- **Private content stays private:** raw documents are never published in a public repo or image.
- **Correctness under failure:** duplicate events, out-of-order events, partial failures and missed events all converge to the correct state.
- **Deletes and renames work:** deleting a doc removes it from answers; renaming a doc updates it rather than duplicating it.
- **Bad content never goes live:** content failing validation is quarantined while the last good version keeps serving.
- **Safe model changes:** switching embedding models happens with zero downtime and an instant rollback.
- **Visible:** "is my latest edit live?" is answerable in seconds.

### Non-goals

- Permission-aware retrieval (all content is public-safe; see 15).
- Non-Doc Drive files (Sheets, PDFs, images) in v1. They are skipped with a warning.
- Real-time sync. A 15 minute delay is acceptable.

---

## 3. Sources

| Corpus | Authoring source | Connector | Identity |
|---|---|---|---|
| `about_me` | A shared Google Drive folder of Google Docs | Drive sync CronJob in the cluster (section 5) | Google Doc file ID |
| `about_system` | The public `glassbox` Git repo | GitHub Actions sync on merge to `main` (section 6) | Repo path |

No private Git repo is needed: Google Drive holds the private content.

A `corpus-sample/` folder of fake "about me" docs lives in the public repo so anyone cloning it can run the system locally (section 14).

---

## 4. Raw zone (S3)

### 4.1 Bucket

`glassbox-corpus-<account-id>`, one bucket with prefixes:

```
raw/about_me/gdrive/<drive-file-id>.md
raw/about_system/git/<repo-path>
quarantine/about_me/gdrive/<drive-file-id>.md
```

- **Keys use stable IDs, not titles.** Renaming a Google Doc changes its metadata, not its key, so no duplicate is created.
- **Object metadata** (user metadata, URL-encoded): `title`, `folder-path`, `source-revision`, `source-modified-at`, `content-sha256`.
- **Bucket settings:** Block Public Access on, SSE-S3 encryption, versioning on, bucket owner enforced.
- **Lifecycle:** noncurrent versions expire after 30 days; `quarantine/` objects expire after 30 days.
- Versioning means any bad write can be rolled back by restoring the previous object version.

### 4.2 Why a raw zone at all

The raw zone holds exact copies of what came from the source. If the chunking strategy or embedding model changes, everything is re-processed from S3 without touching Google or GitHub again. It also decouples connectors from ingestion: a connector's only job is "get the source's current state into S3."

---

## 5. Google Drive connector

### 5.1 Access

- A Google Cloud **service account** (for example `glassbox-reader@<project>.iam.gserviceaccount.com`).
- The "Glassbox Corpus" folder is shared with that email as **Viewer**. It can see nothing else.
- OAuth scope: `https://www.googleapis.com/auth/drive.readonly`.
- The service account key JSON is stored in SSM Parameter Store (SecureString) and loaded into a Kubernetes Secret at boot, like other secrets (DD1 10.6).
- **Upgrade path:** Workload Identity Federation, where Google trusts the AWS instance role directly and no key file exists. Recorded as a stretch goal.

### 5.2 Folder layout = labels

```
Glassbox Corpus/
  Roles/        -> type: role
  Projects/     -> type: project
  Bio           -> type: bio
  Skills        -> type: skills
  Bullet Bank   -> type: other
```

The first subfolder name (lowercased, singularized) becomes `type`. Docs at the top level get their type from a small mapping on the title, defaulting to `other`. The full folder path is stored as metadata.

**Authoring rule:** use real heading styles (Heading 1, Heading 2) for section titles. The Markdown export turns these into `#` headings, which the chunker splits on. Bold text is not a heading.

### 5.3 Sync algorithm

Runs as a Kubernetes **CronJob every 15 minutes**. At this scale (dozens of docs), the simplest correct approach is to list the whole folder tree every run rather than track incremental change tokens.

1. List all items under the root folder recursively (a handful of Drive API calls). Build the set `seen` of Google Doc IDs with `headRevisionId`/`modifiedTime`, title and folder path.
2. For each doc in `seen`:
   - If its revision matches `sync_state.source_revision`, skip.
   - Otherwise export it with `files.export` as `text/markdown`, compute SHA-256.
   - If the hash matches `sync_state.content_hash`, update revision/metadata only and skip (formatting-only edits).
   - Run validation (section 10). On failure, write to `quarantine/`, set status `quarantined`, record the error, and **leave `raw/` untouched** so the last good version keeps serving.
   - On success, `PutObject` to `raw/about_me/gdrive/<id>.md` with metadata. Set status `pending`.
   - If only metadata changed (rename or move), rewrite the object with new metadata so ingestion updates the title and type.
3. For each `about_me` doc in `sync_state` that is **not** in `seen` (deleted, trashed, or moved out of the folder): `DeleteObject` on its raw key.
4. Non-Doc files: skip, log a warning once per file.
5. Record the run in `connector_runs` (section 9).

S3 events from steps 2 and 3 drive ingestion (section 7). The connector never touches MySQL chunks or Redis.

**Scale note:** at thousands of documents, replace step 1 with the Drive Changes API (`changes.list` with a stored page token) and rely on reconciliation for deletions. Not needed here.

---

## 6. Git connector (system corpus)

A GitHub Actions job on merge to `main` in the public repo:

1. Run content validation and the secret scanner on the allowlisted paths. **Fail the job on errors** (nothing syncs).
2. Sync the allowlist to S3 with deletion:

```bash
aws s3 sync . "s3://$BUCKET/raw/about_system/git/" --delete \
  --exclude "*" \
  --include "infra/*" --include "k8s/*" --include "services/*" \
  --include "frontend/src/architecture.ts" --include "docs/*" --include "DESIGN*.md" \
  --exclude "*.tfvars" --exclude "*.tfstate*" --exclude ".env*" --exclude "*/secrets/*"
```

3. Authenticate with GitHub OIDC to an IAM role that may only write and delete under `raw/about_system/git/`.

Git history is the version history for this corpus, and pull request CI is the validation gate. A path is its identity, so a Git rename is processed as a delete plus a create, which is correct.

---

## 7. Events and queues

### 7.1 Flow

- S3 event notifications on prefix `raw/` for `s3:ObjectCreated:*` and `s3:ObjectRemoved:*` go to SQS queue `glassbox-ingest`.
- Queue policy allows `s3.amazonaws.com` to send, restricted by `aws:SourceArn` (the bucket) and `aws:SourceAccount`.
- **Visibility timeout:** 5 minutes (longer than processing any single document).
- **Long polling:** 20 seconds.
- **Redrive policy:** after `maxReceiveCount = 3`, messages move to `glassbox-ingest-dlq` (retention 14 days).

### 7.2 Event realities the design must handle

| Reality | Handling |
|---|---|
| Events can be delivered **more than once** | Idempotent processing by content hash (section 8.2) |
| Events can arrive **out of order** | Compare S3's per-key `sequencer` against `sync_state.last_sequencer`; ignore older events |
| Events can be **missed** (rare) | Nightly reconciliation (section 11) |
| S3 sends a **test event** when notifications are configured | Ignored by type |

### 7.3 Dead-letter handling

- A CloudWatch alarm on `ApproximateNumberOfMessagesVisible > 0` for the DLQ notifies by email through SNS.
- `make dlq-inspect` prints DLQ messages with the matching `sync_state.last_error`.
- `make dlq-redrive` moves messages back to the main queue (SQS `StartMessageMoveTask`) after a fix.

---

## 8. Ingestion worker

### 8.1 Runtime: KEDA ScaledJob

Ingestion runs only when there is work. KEDA watches the SQS queue and launches a Kubernetes Job when messages appear; the Job drains the queue and exits.

```yaml
apiVersion: keda.sh/v1alpha1
kind: ScaledJob
metadata:
  name: ingest
  namespace: app
spec:
  pollingInterval: 30
  maxReplicaCount: 1                 # serialized: one ingest Job at a time
  successfulJobsHistoryLimit: 3
  failedJobsHistoryLimit: 3
  jobTargetRef:
    backoffLimit: 0                  # SQS handles retries, not Kubernetes
    template:
      spec:
        serviceAccountName: ingest
        restartPolicy: Never
        containers:
          - name: ingest
            image: ghcr.io/<you>/glassbox:<sha>
            command: ["python", "-m", "glassbox.ingest", "drain"]
            resources:
              requests: { cpu: 100m, memory: 128Mi }
              limits:   { memory: 256Mi }
  triggers:
    - type: aws-sqs-queue
      authenticationRef:
        name: keda-aws
      metadata:
        queueURL: https://sqs.us-east-1.amazonaws.com/<account-id>/glassbox-ingest
        queueLength: "20"
        awsRegion: us-east-1
---
apiVersion: keda.sh/v1alpha1
kind: TriggerAuthentication
metadata:
  name: keda-aws
  namespace: app
spec:
  podIdentity:
    provider: aws                    # uses the node's instance role; verify syntax for the installed KEDA version
```

This uses KEDA two ways in one system: a `ScaledObject` scaling long-running retrieval workers on a Redis Stream (DD1 9.3), and a `ScaledJob` launching batch Jobs from an SQS queue.

### 8.2 Processing one message

```
receive batch (up to 10, long poll 20s)
for each S3 record:
    key, event, version_id, sequencer = parse(record)
    doc_uid = uid_from_key(key)                 # "gdrive:<id>" or "git:glassbox:<path>"

    if sequencer <= sync_state.last_sequencer: ack; continue      # stale event

    if event is ObjectRemoved:
        delete document (cascade chunks, embeddings) in MySQL
        delete its vectors from the active Redis index
        mark sync_state deleted
    else:
        body = GetObject(key, version_id)
        h = sha256(body)
        if h == sync_state.processed_hash and metadata unchanged: ack; continue   # idempotent
        chunks = chunk(body, type)
        for each chunk: reuse embedding if chunk_hash already embedded with active model, else call Bedrock
        MySQL transaction: upsert document by doc_uid, replace its chunks and embeddings
        Redis: remove old chunk vectors for this document, add new ones
        mark sync_state live, processed_hash = h

    update sync_state.last_sequencer; bump corpus version (cache invalidation, DD1 7.3)
    delete SQS message
exit when the queue is empty
```

Rules:

- **MySQL commits before Redis updates.** If Redis fails, the message is not deleted, SQS redelivers it, and the idempotent path finishes the Redis step. Reconciliation catches anything left over.
- **Chunk-level embedding reuse:** editing one section of a doc re-embeds only that section's chunks.
- **Mutual exclusion:** the Job takes a MySQL advisory lock (`GET_LOCK('glassbox_ingest', 0)`) so ingestion and reconciliation never run at the same time. If the lock is held, the Job exits and KEDA retries on the next poll.
- **Errors:** record `last_error` and increment `attempts` in `sync_state`, do not delete the message. SQS retries it, and after 3 attempts it lands in the DLQ.

### 8.3 Stable document IDs

| Source | `doc_uid` | Rename behavior |
|---|---|---|
| Google Drive | `gdrive:<file-id>` | Same ID; title and type updated in place |
| Git | `git:glassbox:<path>` | Path is identity; delete + create |

---

## 9. Data model changes

### 9.1 New tables

```sql
CREATE TABLE sync_state (
  doc_uid             VARCHAR(255) PRIMARY KEY,
  corpus              ENUM('about_me','about_system') NOT NULL,
  source              ENUM('gdrive','git') NOT NULL,
  source_id           VARCHAR(512) NOT NULL,
  title               VARCHAR(512),
  folder_path         VARCHAR(512),
  source_revision     VARCHAR(128),
  source_modified_at  TIMESTAMP NULL,
  raw_key             VARCHAR(1024) NOT NULL,
  content_hash        CHAR(64),         -- latest seen at the source
  processed_hash      CHAR(64),         -- latest successfully ingested
  last_sequencer      VARCHAR(64),
  status              ENUM('pending','live','quarantined','failed','deleted') NOT NULL,
  last_error          TEXT,
  attempts            INT NOT NULL DEFAULT 0,
  updated_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  KEY idx_status (status)
);

CREATE TABLE connector_runs (
  id              BIGINT PRIMARY KEY AUTO_INCREMENT,
  connector       ENUM('gdrive','git','reconcile') NOT NULL,
  started_at      TIMESTAMP NOT NULL,
  finished_at     TIMESTAMP NULL,
  docs_seen       INT, docs_changed INT, docs_deleted INT, docs_quarantined INT,
  drift_found     INT,
  status          ENUM('running','succeeded','failed') NOT NULL,
  error           TEXT
);

CREATE TABLE index_versions (
  id               INT PRIMARY KEY AUTO_INCREMENT,
  embedding_model  VARCHAR(128) NOT NULL,
  dims             INT NOT NULL,
  redis_index      VARCHAR(64) NOT NULL,      -- e.g. idx:chunks:v2
  status           ENUM('building','validating','active','retired','failed') NOT NULL,
  eval_recall_at_5 DECIMAL(5,4),
  created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  activated_at     TIMESTAMP NULL
);

CREATE TABLE chunk_embeddings (
  chunk_id          BIGINT NOT NULL,
  index_version_id  INT NOT NULL,
  embedding         BLOB NOT NULL,
  PRIMARY KEY (chunk_id, index_version_id),
  FOREIGN KEY (chunk_id) REFERENCES chunks(id) ON DELETE CASCADE,
  FOREIGN KEY (index_version_id) REFERENCES index_versions(id)
);
```

### 9.2 Changes to DD1 tables

```sql
ALTER TABLE documents
  ADD COLUMN doc_uid   VARCHAR(255) NOT NULL,
  ADD COLUMN source    ENUM('gdrive','git') NOT NULL,
  ADD UNIQUE KEY uq_doc_uid (doc_uid),
  DROP INDEX uq_doc;                 -- (corpus, source_path) no longer the identity

ALTER TABLE chunks
  ADD COLUMN chunk_hash CHAR(64) NOT NULL,
  DROP COLUMN embedding,             -- moved to chunk_embeddings
  DROP COLUMN embedding_model;
```

Embeddings move to `chunk_embeddings` so two embedding models can coexist during a blue-green switch (section 12).

---

## 10. Validation and quarantine

Checks (from DD2 4.5), now run in both connectors:

| Check | Level | Drive connector | Git connector |
|---|---|---|---|
| Secret scanner match | Error | Quarantine | Fail CI, nothing syncs |
| Export failed or empty document | Error | Quarantine | n/a |
| Invalid front matter (Git) | Error | n/a | Fail CI |
| Section over 800 words without subheadings | Warning | Log + status note | PR comment |
| Chunks under 40 tokens | Warning | Log | PR comment |
| Dangling reference openers ("As mentioned above") | Warning | Log | PR comment |
| No headings at all (likely bold used as headings) | Warning | Log | PR comment |

**Quarantine semantics:** a quarantined doc's new version goes to `quarantine/`, its `raw/` object is left as-is, and its status shows `quarantined` with the reason. The chatbot keeps serving the last good version. Fixing the doc clears the quarantine on the next sync.

---

## 11. Reconciliation

A CronJob at 03:00 UTC. Events make the pipeline fast; reconciliation makes it correct.

Compares four views: **source** (Drive listing, and the Git allowlist at the deployed commit), **raw zone** (S3 listing), **state** (`sync_state`), **index** (MySQL documents/chunks and the active Redis index).

| Drift | Repair |
|---|---|
| In source, missing or stale in S3 | Re-run the connector for that doc |
| In S3, not in source | Delete from S3 (event triggers cleanup) |
| S3 hash differs from `processed_hash` | Enqueue a synthetic event for that key |
| `sync_state` says live, document missing in MySQL | Reprocess |
| Chunks in MySQL missing from Redis index | Re-add vectors |
| Vectors in Redis with no MySQL chunk | Remove |
| Document stuck `pending` over 1 hour | Reprocess, and warn |

Writes a `connector_runs` row with `drift_found`. **Non-zero drift is a signal**, since it means an event path failed; it is logged and shown on the pipeline panel (section 13).

---

## 12. Re-embedding: blue-green index

Vectors from different embedding models are not comparable, so a model change must never mix them.

1. Insert an `index_versions` row (`building`) with the new model, dims and a new Redis index name (`idx:chunks:v2`).
2. A `reembed` Job embeds every chunk with the new model into `chunk_embeddings` and builds the new Redis index. The active index keeps serving throughout.
3. Status `validating`: run the retrieval eval (DD1 15) against the new index. It must not regress recall@5 by more than 2 points.
4. **Switch:** set the Redis key `index:active` to the new index and mark it `active` in MySQL. The API and workers read `index:active` per request, so the switch is instant. Bump corpus versions to invalidate caches; embedding cache keys include the model name.
5. Keep the old index for 7 days as `retired`. **Rollback is flipping `index:active` back.**
6. After 7 days, drop the old Redis index and its `chunk_embeddings` rows.

During steps 1 to 3, ingestion writes new or changed documents to **both** indexes so the new one doesn't fall behind.

---

## 13. Observability

**Metrics** (Prometheus, from the ingest Job and connectors):

- Sync lag: source `modified_at` to `live`, per document (target p95 under 20 minutes).
- Documents by status.
- SQS queue depth and DLQ depth.
- Bedrock embedding calls, and chunks reused vs. re-embedded.
- Reconciliation drift count.

**Alerts:** DLQ non-empty; any document `failed`; reconciliation drift above zero two nights in a row; connector run failed.

**Pipeline panel (public):** in the "About This System" view, a small panel shows aggregate counts only: documents live, last sync time, last reconcile result, queue and DLQ depth. It demonstrates the pipeline without exposing private titles or content.

**Owner CLI:** `make content-status` prints every document with status, last update and any error, answering "is my latest edit live?"

---

## 14. Local development

- Docker Compose adds **LocalStack** (S3 + SQS) alongside MySQL and Redis.
- A `filesystem` connector syncs `corpus-sample/` into the local bucket, standing in for Google Drive.
- Real Drive sync can be tested locally by providing a service account key through an env var.

---

## 15. Security

| Concern | Control |
|---|---|
| Drive access | Service account with `drive.readonly`, sees only the shared folder |
| Drive credential | Key in SSM SecureString; Kubernetes Secret mounted only into the Drive connector pod; stretch: Workload Identity Federation |
| Git sync credential | GitHub OIDC role limited to `raw/about_system/git/*` |
| Bucket exposure | Block Public Access, no public policy, encryption, versioning |
| Pod access to AWS | See note below |
| Secrets in content | Scanner in both connectors; quarantine or CI failure |

**Pod credentials note (applies to DD1 too):** pods use the EC2 instance role through the instance metadata service. The launch template must set `metadata_options { http_tokens = "required", http_put_response_hop_limit = 2 }`, because the default hop limit of 1 blocks containers from reaching it. On a single node, all pods can then assume the same role. Mitigations: an egress NetworkPolicy denying `169.254.169.254` to pods that don't need AWS (Redis, Traefik), and a least-privilege instance role. **Production answer:** per-pod IAM roles via IRSA or EKS Pod Identity.

**Instance role additions:**

| Action | Resource |
|---|---|
| `s3:GetObject`, `s3:ListBucket` | the corpus bucket |
| `s3:PutObject`, `s3:DeleteObject` | `raw/about_me/gdrive/*`, `quarantine/*` |
| `sqs:ReceiveMessage`, `sqs:DeleteMessage`, `sqs:GetQueueAttributes`, `sqs:SendMessage` | `glassbox-ingest` (send is for reconciliation's synthetic events) |
| `sqs:GetQueueAttributes` | `glassbox-ingest-dlq` |

**Future: permission-aware retrieval.** In enterprise RAG, each chunk carries access metadata (users or groups allowed) copied from the source's permissions, and retrieval filters by the asker's identity. Not needed here, but the `sync_state`/`documents` model leaves room for an `acl` column.

---

## 16. What this supersedes

| Earlier design | Replaced by |
|---|---|
| DD1 6.4: ingest image contains a repo snapshot; Job runs per deploy | Images contain code only; S3 raw zone; ScaledJob on SQS; nightly reconcile |
| DD1 7.1: `chunks.embedding`, `chunks.embedding_model`; `documents` identity by `(corpus, source_path)` | `chunk_embeddings` table; `documents.doc_uid` |
| DD1 12: "ingest Job" step in the Flux deploy order | Removed; deploys no longer trigger ingestion |
| DD2 4.1 / 4.2: `about_me` Markdown in `corpus/about-me/` | Google Drive folder; `corpus-sample/` for local dev |
| DD2 4.4: front matter for `type` | Drive folder path for `about_me`; front matter still supported for Git |
| DD2 4.5: validation in CI only | Validation in both connectors, with quarantine for Drive |
| DD1 4.6 citation links for `about_me` | No external link (content is private); popover text only |

---

## 17. Failure scenarios

| Scenario | Outcome |
|---|---|
| Doc edited with a pasted API key | Quarantined; previous version keeps serving; status shows reason |
| Same S3 event delivered twice | Second is a no-op (hash match) |
| Delete event arrives before the preceding update event | Update ignored as stale by sequencer |
| Bedrock throttles during ingestion | Message retried by SQS; DLQ after 3 attempts; alert |
| Node dies mid-ingest | Message becomes visible again after 5 minutes; replacement node's Job reprocesses idempotently |
| Redis wiped (node replaced) | Reindex from `chunk_embeddings` for the active index version (DD2 3.3). Today: the ingest Job's reconcile rewrites every chunk key from MySQL `chunks` (section 1.1) |
| An S3 event is lost | Reconciliation repairs it overnight and reports drift |
| New embedding model is worse | Fails eval gate; never activated. Or, if activated, flip `index:active` back |
| Doc moved out of the shared folder | Treated as deleted; removed from answers within 15 minutes |

---

## 18. Infrastructure additions (Terraform)

New module `modules/ingestion`:

- `aws_s3_bucket` + versioning, encryption, public access block, lifecycle rules.
- `aws_sqs_queue` `glassbox-ingest` and `glassbox-ingest-dlq` with redrive policy; queue policy for S3.
- `aws_s3_bucket_notification` on `raw/` to the queue.
- `aws_cloudwatch_metric_alarm` on DLQ depth + `aws_sns_topic` with email subscription.
- IAM: instance role statements (section 15); GitHub OIDC role scoped to `raw/about_system/git/*`.
- `aws_ssm_parameter` for the Drive service account key (value set manually, not in Terraform state).
- `modules/compute` launch template: `metadata_options` hop limit 2.

Kubernetes (`k8s/base/ingestion/`): ScaledJob + TriggerAuthentication, Drive connector CronJob, reconcile CronJob, `reembed` Job template, egress NetworkPolicies, ServiceAccounts.

---

## 19. Cost

| Item | Monthly |
|---|---|
| S3 storage and requests (a few MB) | ~$0.01 |
| SQS (well under the always-free 1M requests) | $0 |
| CloudWatch alarm | ~$0.10 |
| SNS email | $0 within free allowance |
| Google Drive API | $0 |
| Bedrock embeddings (chunk reuse keeps this tiny) | pennies |
| **Total added** | **under $0.25** |

---

## 20. Build plan

| Phase | Work | Done when |
|---|---|---|
| I-1 | Schema migrations (9); LocalStack in Compose; filesystem connector; ingest worker draining a local queue | Adding, editing and deleting a file in `corpus-sample/` updates answers locally |
| I-2 | Idempotency, sequencer ordering, chunk-level embedding reuse, advisory lock | Replaying the same events twice leaves identical state (automated test) |
| I-3 | Terraform `modules/ingestion`; ScaledJob; Git connector in CI | A merge to `main` updates "About This System" answers without a deploy |
| I-4 | Drive connector CronJob; folder-to-type mapping; quarantine | Editing a Google Doc updates answers within 15 minutes; a doc with a fake key is quarantined |
| I-5 | Reconciliation; DLQ alarm and redrive tooling; metrics; pipeline panel; `make content-status` | Deleting an S3 object by hand is repaired overnight and reported as drift |
| I-6 | Blue-green re-embedding with eval gate | Switching dimension 512 to 1024 happens with zero failed queries; rollback tested |

Recommended ordering relative to DD1: I-1 and I-2 during DD1 Phase 1 and 2; I-3 during Phase 4; I-4 and I-5 during Phase 6; I-6 during Phase 7.

---

## 21. How to explain it in an interview

- "Connectors land raw content in S3; S3 events feed SQS; KEDA launches ingestion Jobs only when there's work."
- "Processing is idempotent by content hash and ordered by S3 sequencer, so duplicate and out-of-order events are harmless."
- "Events give speed; a nightly reconciliation gives correctness, and drift is tracked as a health signal."
- "Bad content is quarantined while the last good version keeps serving."
- "Embedding model changes are blue-green: build alongside, gate on a retrieval eval, flip a pointer, keep the old index for rollback."
- "What I'd add at a company: per-pod IAM roles and permission-aware retrieval."

---

## 22. Resume bullets

Fill in numbers only after measuring.

- Built an event-driven document ingestion pipeline (Google Drive and Git connectors, S3, SQS with dead-lettering, KEDA ScaledJobs) with idempotent, order-safe processing and nightly reconciliation, keeping p95 source-to-live lag under [X] minutes.
- Implemented blue-green re-indexing for embedding model changes, gated on a retrieval eval (recall@5 [X]), with zero-downtime cutover and instant rollback.
- Added content validation and quarantine so malformed or sensitive documents never reached production answers.

---

## 23. Open questions

| Question | Options | Leaning |
|---|---|---|
| Drive sync interval | 5, 15 or 30 minutes | 15 |
| Support Sheets/PDFs | v1 vs. later | Later |
| Drive credential | Key in SSM vs. Workload Identity Federation | Key first, WIF as stretch |
| Pipeline panel | Public aggregate panel vs. owner CLI only | Both; panel shows counts only |
