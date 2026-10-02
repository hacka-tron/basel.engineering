# Glassbox Design Doc 003: Content Ingestion Pipeline

| | |
|---|---|
| **Status** | Planned for Milestone 4, not built yet. Two sections describe what runs: §1.1 (the ingest Job) and §1.2 (the private About Basel repo, live from the first release after PR #126; the owner added the deploy key on 2026-10-01). The Google Drive connector (§5) was dropped on 2026-10-01. |
| **Owner** | Basel |
| **Last updated** | 2026-10-01 |
| **Builds on** | `DESIGN.md` ("DD1") and `DESIGN-002-followups.md` ("DD2") |
| **Supersedes** | DD1 6.4 (ingestion job) and parts of DD1 7.1 and DD2 4.x (see section 16) |

---

## 1. Summary (planned, not built yet)

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

## 1.1 What runs today: the current ingest Job, deleted files and Redis drift

Everything else in this document describes the connector pipeline. The ingest Job that runs today (`services/glassbox/ingest/run.py`, DD1 6.4) still reads the files baked into the image. Since 2026-10-01 it handles deleted and renamed files like this:

- **On by default (report only):** after a run has walked every file, it logs each indexed document whose file is gone, per corpus and embedding model ("would delete ..."). Nothing is deleted; those documents stay searchable.
- **Off by default (switch: `GLASSBOX_INGEST_SWEEP=apply` on the Job, or `--sweep`):** the same list is deleted: Redis `chunk:{id}` keys first, then the `corpus:ver:{corpus}` bump, then the MySQL chunk and document rows. A document keeps its row while it still has chunks for another embedding model.
- **Guards (both modes):** no sweep for a corpus whose scan found zero files; none when a source directory that has indexed documents (`infra`, `k8s`, `services`, `docs`, `private`) produced zero scanned files, since each is under the fraction limit on its own and an image that stopped copying one would otherwise lose it quietly; and none when more than 30% of a corpus's indexed documents would go (`GLASSBOX_INGEST_SWEEP_MAX_FRACTION`). Two or fewer deletions are always allowed, so renaming files in the five-file About Basel corpus works, which also means 2 of its 5 documents (40%) can go in one run without tripping the limit. `--force-sweep` lifts the directory and fraction guards, never the zero-file one. A refusal logs at `ERROR` and prints a `!!! STALE SWEEP REFUSED ... !!!` banner; the run still succeeds and nothing is recorded on `ingestion_runs`.
- **A failed `--clear`:** if it fails after deleting Redis keys, the MySQL rows remain, and the next ingest's reconcile (below) writes their keys back instead of finishing the wipe. Re-run `--clear` to finish, then ingest.
- **Redis reconcile (every run, even with no file changed):** for the configured embedding model, rebuilds Redis `chunk:{id}` keys from MySQL rows and their stored vectors, with no embedding call: missing keys are written, keys whose `content_sha` (chunk text hash), corpus, model, document or path disagree are rewritten, keys with no MySQL row are deleted. A corpus with zero MySQL chunks, or one where over 30% of its keys would go, loses no keys (`REDIS RECONCILE REFUSED` banner; `--reindex` lifts the 30% guard). One run at a time (Redis lock `ingest:lock`). `corpus:ver` is bumped only on change. Details: DD1 6.5.
- **Operator commands:** `python -m services.glassbox.ingest.run --dry-run` prints the list without ingesting or writing anything. `--clear --corpus about_me|about_system [--model M] [--dry-run] [--yes]` wipes one corpus and model's documents, chunks and Redis keys for a clean re-ingest; it asks you to type the corpus name unless `--yes` is given, and the Job never runs it. `--reindex` rewrites every Redis chunk key from MySQL.

The connector design below (sections 8 and 11) replaces this with event-driven deletes and nightly reconciliation (its MySQL-to-Redis rows already run as the reconcile above).

## 1.2 Private About Basel repo (live since the 2026-10-02 release)

Owner decision, 2026-10-01: About Basel content moves to a private GitHub repo, `hacka-tron/basel.engineering-docs` (Markdown under its `about-me/` folder). The Google Drive design (§5) is dropped. About This System stays in this repo. The owner added the deploy key on 2026-10-01 and everything below has run since the 2026-10-02 release. A release without the key ships no About Basel files at all (the public copies were deleted); the previously indexed documents keep serving.

- **Release-time checkout.** `release.yml` checks the private repo out into `corpus/about-me-private/` before the image build, with a read-only deploy key (`ABOUT_ME_DEPLOY_KEY`, a `release` environment secret), `persist-credentials: false` and `fetch-depth: 1`. Without the secret the step is skipped and the image carries no About Basel files (`corpus/README.md` keeps the Dockerfile's `COPY corpus/` valid). A key that is set but fails fails the release. The content is baked into the image, which lives in private ECR. A deploy key reads one repo and nothing else and belongs to no person; a fine-grained token would be tied to the owner's account, expire, and need a broader scope to reach a repo of another name.
- **What reaches the image.** `.dockerignore` keeps only `corpus/about-me-private/about-me/**/*.md`, never the private repo's `.git`, README or LICENSE. `.gitignore` keeps the checkout out of this public repo. No workflow step lists or prints files, and the build record artifact is turned off.
- **No GitHub Actions cache for private builds.** The public build exports its layer cache to the GitHub Actions cache, which other workflow runs in this public repo (fork pull requests included) can restore. So `release.yml` has two build steps with mutually exclusive conditions: without the checkout, the cached build; with it, a build with no cache settings at all (no import, no export), no build record artifact and no job summary. Two steps rather than one computed `cache-to`, because a GitHub expression like `cond && '' || 'type=gha'` always yields the second value (an empty string is falsy); review caught exactly that before merge. A test evaluates both cases from the parsed workflow. Builds with the private corpus are slower.
- **Citation paths show private file names** (`private/bio.md`): name files neutrally.
### 1.2.1 Ingest and deletes (live with §1.2)

- **Ingest.** The scanner reads `about-me/**/*.md` from the checkout (hidden paths and symlinks skipped) as `about_me` documents with source path `private/<path under about-me/>`. They pass the secret scanner and the personal-data guard (DD1 §11) like every About Basel file. The content hash makes edits re-embed only the changed file.
- **The public copies are gone.** The first release with the checkout (2026-10-02) ingested the private repo and a live About Basel answer cited `private/...` sources, so the public `corpus/about-me/*.md` files were deleted (the shadowing had already removed their indexed documents, so the index did not change). The scanner still reads `corpus/about-me/` if the directory exists and still skips a public file that has a private twin; with the directory absent both are no-ops. Without the checkout a release now has no About Basel files at all: the sweep refuses (zero files for the corpus) and keeps the last indexed documents, and new private edits do not go live until a release with the checkout.
- **Deletes.** A file removed from the private repo goes through the stale sweep (report mode in production) and the Redis reconcile. A release built without the checkout (no secret, or the secret removed) scans zero private files; the sweep refuses to delete `private/` documents then, and `--force-sweep` does not override that (`--clear` is the deliberate wipe).
- **Freshness.** Releases trigger on changes in this repo only. After editing the private repo the owner runs the Release workflow by hand on `main` (the existing manual dispatch). A `repository_dispatch` from the private repo would need a token with access to this repo, so it is not set up.

---

## 2. Goals and non-goals (planned, not built yet)

### Goals (planned)

- **Easy authoring:** "About Basel" content is written in Google Docs, editable from any device, live in the chatbot within about 15 minutes.
- **Private content stays private:** raw documents are never published in a public repo or image.
- **Correctness under failure:** duplicate events, out-of-order events, partial failures and missed events all converge to the correct state.
- **Deletes and renames work:** deleting a doc removes it from answers; renaming a doc updates it rather than duplicating it.
- **Bad content never goes live:** content failing validation is quarantined while the last good version keeps serving.
- **Safe model changes:** switching embedding models happens with zero downtime and an instant rollback.
- **Visible:** "is my latest edit live?" is answerable in seconds.

### Non-goals (planned)

- Permission-aware retrieval (all content is public-safe; see 15).
- Non-Doc Drive files (Sheets, PDFs, images) in v1. They are skipped with a warning.
- Real-time sync. A 15 minute delay is acceptable.

---

## 3. Sources (planned, not built yet)

| Corpus | Authoring source | Connector | Identity |
|---|---|---|---|
| `about_me` | A shared Google Drive folder of Google Docs | Drive sync CronJob in the cluster (section 5) | Google Doc file ID |
| `about_system` | The public `glassbox` Git repo | GitHub Actions sync on merge to `main` (section 6) | Repo path |

No private Git repo is needed: Google Drive holds the private content.

A `corpus-sample/` folder of fake "about me" docs lives in the public repo so anyone cloning it can run the system locally (section 14).

---

## 4. Raw zone (S3) (planned, not built yet)

### 4.1 Bucket (planned)

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

### 4.2 Why a raw zone at all (planned)

The raw zone holds exact copies of what came from the source. If the chunking strategy or embedding model changes, everything is re-processed from S3 without touching Google or GitHub again. It also decouples connectors from ingestion: a connector's only job is "get the source's current state into S3."

---

## 5. Google Drive connector (dropped 2026-10-01, not built)

The owner dropped Google Drive as the About Basel source on 2026-10-01 in favour of the private GitHub repo in §1.2. Nothing of the Drive connector is built: no service account, no Drive API client, no sync CronJob, no S3 raw zone writes. The original design (a shared folder read with `drive.readonly`, Google Docs exported as Markdown, folder names as type labels, a 15-minute CronJob writing to the S3 raw zone) is kept in Git history only. Sections 2 to 23 still mention Google Docs as the authoring source; read that as the private repo.

---

## 6. Git connector (system corpus) (planned, not built yet)

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

## 7. Events and queues (planned, not built yet)

### 7.1 Flow (planned)

- S3 event notifications on prefix `raw/` for `s3:ObjectCreated:*` and `s3:ObjectRemoved:*` go to SQS queue `glassbox-ingest`.
- Queue policy allows `s3.amazonaws.com` to send, restricted by `aws:SourceArn` (the bucket) and `aws:SourceAccount`.
- **Visibility timeout:** 5 minutes (longer than processing any single document).
- **Long polling:** 20 seconds.
- **Redrive policy:** after `maxReceiveCount = 3`, messages move to `glassbox-ingest-dlq` (retention 14 days).

### 7.2 Event realities the design must handle (planned)

| Reality | Handling |
|---|---|
| Events can be delivered **more than once** | Idempotent processing by content hash (section 8.2) |
| Events can arrive **out of order** | Compare S3's per-key `sequencer` against `sync_state.last_sequencer`; ignore older events |
| Events can be **missed** (rare) | Nightly reconciliation (section 11) |
| S3 sends a **test event** when notifications are configured | Ignored by type |

### 7.3 Dead-letter handling (planned)

- A CloudWatch alarm on `ApproximateNumberOfMessagesVisible > 0` for the DLQ notifies by email through SNS.
- `make dlq-inspect` prints DLQ messages with the matching `sync_state.last_error`.
- `make dlq-redrive` moves messages back to the main queue (SQS `StartMessageMoveTask`) after a fix.

---

## 8. Ingestion worker (planned, not built yet)

### 8.1 Runtime: KEDA ScaledJob (planned)

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

### 8.2 Processing one message (planned)

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

### 8.3 Stable document IDs (planned)

| Source | `doc_uid` | Rename behavior |
|---|---|---|
| Google Drive | `gdrive:<file-id>` | Same ID; title and type updated in place |
| Git | `git:glassbox:<path>` | Path is identity; delete + create |

---

## 9. Data model changes (planned, not built yet)

### 9.1 New tables (planned)

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

### 9.2 Changes to DD1 tables (planned)

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

## 10. Validation and quarantine (planned, not built yet)

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

## 11. Reconciliation (planned, not built yet)

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

## 12. Re-embedding: blue-green index (planned, not built yet)

Vectors from different embedding models are not comparable, so a model change must never mix them.

1. Insert an `index_versions` row (`building`) with the new model, dims and a new Redis index name (`idx:chunks:v2`).
2. A `reembed` Job embeds every chunk with the new model into `chunk_embeddings` and builds the new Redis index. The active index keeps serving throughout.
3. Status `validating`: run the retrieval eval (DD1 15) against the new index. It must not regress recall@5 by more than 2 points.
4. **Switch:** set the Redis key `index:active` to the new index and mark it `active` in MySQL. The API and workers read `index:active` per request, so the switch is instant. Bump corpus versions to invalidate caches; embedding cache keys include the model name.
5. Keep the old index for 7 days as `retired`. **Rollback is flipping `index:active` back.**
6. After 7 days, drop the old Redis index and its `chunk_embeddings` rows.

During steps 1 to 3, ingestion writes new or changed documents to **both** indexes so the new one doesn't fall behind.

---

## 13. Observability (planned, not built yet)

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

## 14. Local development (planned, not built yet)

- Docker Compose adds **LocalStack** (S3 + SQS) alongside MySQL and Redis.
- A `filesystem` connector syncs `corpus-sample/` into the local bucket, standing in for Google Drive.
- Real Drive sync can be tested locally by providing a service account key through an env var.

---

## 15. Security (planned, not built yet)

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

## 16. What this supersedes (planned, not built yet)

| Earlier design | Replaced by |
|---|---|
| DD1 6.4: ingest image contains a repo snapshot; Job runs per deploy | Images contain code only; S3 raw zone; ScaledJob on SQS; nightly reconcile |
| DD1 7.1: `chunks.embedding`, `chunks.embedding_model`; `documents` identity by `(corpus, source_path)` | `chunk_embeddings` table; `documents.doc_uid` |
| DD1 12: "ingest Job" step in the Flux deploy order | Removed; deploys no longer trigger ingestion |
| DD2 4.1 / 4.2: `about_me` Markdown in the private repo's `about-me/` folder (was `corpus/about-me/`) | Google Drive folder; `corpus-sample/` for local dev |
| DD2 4.4: front matter for `type` | Drive folder path for `about_me`; front matter still supported for Git |
| DD2 4.5: validation in CI only | Validation in both connectors, with quarantine for Drive |
| DD1 4.6 citation links for `about_me` | No external link (content is private); popover text only |

---

## 17. Failure scenarios (planned, not built yet)

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

## 18. Infrastructure additions (Terraform) (planned, not built yet)

New module `modules/ingestion`:

- `aws_s3_bucket` + versioning, encryption, public access block, lifecycle rules.
- `aws_sqs_queue` `glassbox-ingest` and `glassbox-ingest-dlq` with redrive policy; queue policy for S3.
- `aws_s3_bucket_notification` on `raw/` to the queue.
- `aws_cloudwatch_metric_alarm` on DLQ depth + `aws_sns_topic` with email subscription.
- IAM: instance role statements (section 15); GitHub OIDC role scoped to `raw/about_system/git/*`.
- `aws_ssm_parameter` for the Drive service account key (dropped with the Drive connector, §5).
- `modules/compute` launch template: `metadata_options` hop limit 2.

Kubernetes (`k8s/base/ingestion/`): ScaledJob + TriggerAuthentication, Drive connector CronJob, reconcile CronJob, `reembed` Job template, egress NetworkPolicies, ServiceAccounts.

---

## 19. Cost (planned, not built yet)

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

## 20. Build plan (planned, not built yet)

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

## 21. How to explain it in an interview (planned, not built yet)

- "Connectors land raw content in S3; S3 events feed SQS; KEDA launches ingestion Jobs only when there's work."
- "Processing is idempotent by content hash and ordered by S3 sequencer, so duplicate and out-of-order events are harmless."
- "Events give speed; a nightly reconciliation gives correctness, and drift is tracked as a health signal."
- "Bad content is quarantined while the last good version keeps serving."
- "Embedding model changes are blue-green: build alongside, gate on a retrieval eval, flip a pointer, keep the old index for rollback."
- "What I'd add at a company: per-pod IAM roles and permission-aware retrieval."

---

## 22. Resume bullets (planned, not built yet)

Fill in numbers only after measuring.

- Built an event-driven document ingestion pipeline (Google Drive and Git connectors, S3, SQS with dead-lettering, KEDA ScaledJobs) with idempotent, order-safe processing and nightly reconciliation, keeping p95 source-to-live lag under [X] minutes.
- Implemented blue-green re-indexing for embedding model changes, gated on a retrieval eval (recall@5 [X]), with zero-downtime cutover and instant rollback.
- Added content validation and quarantine so malformed or sensitive documents never reached production answers.

---

## 23. Open questions (planned, not built yet)

| Question | Options | Leaning |
|---|---|---|
| Drive sync interval | 5, 15 or 30 minutes | 15 |
| Support Sheets/PDFs | v1 vs. later | Markdown only (§1.2) |
| Drive credential | Key in SSM vs. Workload Identity Federation | Moot: Drive dropped 2026-10-01; the private repo uses a read-only deploy key (§1.2) |
| Pipeline panel | Public aggregate panel vs. owner CLI only | Both; panel shows counts only |
