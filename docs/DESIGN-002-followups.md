# Glassbox Design Doc 002: Follow-up Features

| | |
|---|---|
| **Status** | Partly built. Features 3 and 4 (conversational chat, live chat UX) and the heartbeat and Stop parts of feature 5 are live. Section 3b (alarms, daily snapshots, restore runbook) is merged and takes effect once applied. Each unbuilt section says so in its heading. |
| **Owner** | Basel |
| **Last updated** | 2026-10-01 |
| **Builds on** | `DESIGN.md` (referred to below as "DD1") |

---

## 1. Summary

This document adds five features to the base design:

1. **Self-healing node recovery** with a size-1 Auto Scaling group, so the site rebuilds itself if the EC2 instance dies. Deferred on 2026-10-01 in favour of the smaller protections in section 3b.
2. **Corpus authoring guide and ingest validation**, defining how content is organized, labeled and checked before it reaches the index. Reading front-matter values and the validation step are not built yet (the scanner only strips front matter).
3. **Conversational chat**: multi-turn memory, follow-up question rewriting, and correct cache behavior for follow-ups (live).
4. **Live chat UX**: conversations saved in the browser's localStorage, typing states, a stop button that actually stops generation server-side, and smart auto-scroll (live, with stop-and-send, Up-arrow recall and Retry).
5. **Streaming delivery hardening** so token-by-token output survives Cloudflare and proxies in production. Heartbeats and server-side Stop are live.
   The scripted post-deploy streaming check and time-to-first-token logging are not built yet.

Plus one small network change: a free **S3 gateway endpoint** (not built yet).

Section 8 lists every change to DD1's contracts and schema in one place. Section 9 maps the work onto DD1's build phases.

---

## 2. Goals and non-goals

### Goals

- **Recovery without a human (not built yet):** if the node is terminated, the site returns on its own within 10 minutes, with no data loss.
- **Follow-up questions work:** "tell me more about that" retrieves the right documents and answers in context.
- **Stopping means stopping:** pressing Stop ends LLM token spend within about a second.
- **Streaming in production matches local (not measured yet):** time to first token through Cloudflare is within 300 ms of local.
- **Content quality is enforced (not built yet):** badly structured corpus files produce warnings in CI, not silently bad answers.

### Non-goals

- A highly available control plane (options documented in 3.6, not built).
- Server-side chat sessions or stored conversation history per visitor.
- WebSockets. SSE remains the transport (see 7.1).

---

## 3. Feature 1: Self-healing node recovery (not built)

### 3.1 Problem (not built)

DD1 runs k3s on a single EC2 instance. If that instance fails, the whole site is down until someone manually rebuilds it. This design was written when the data was meant to live on RDS; section 3a, after this feature, describes what a replacement means for the in-cluster MySQL today, and 3.9 revises the plan.

### 3.2 Design (not built)

Replace the standalone `aws_instance` with:

- **`aws_launch_template`**: the instance definition (AMI, `t4g.small`, root volume, IAM instance profile, security group, `credit_specification = standard`, user data).
- **`aws_autoscaling_group`** with `min_size = max_size = desired_capacity = 1`, spanning both public subnets. If the instance is unhealthy or terminated, the group launches a replacement, possibly in the other availability zone.

The Elastic IP from DD1 is kept, because Cloudflare's DNS record points at it. A replacement instance gets a new address by default, so the boot script re-attaches the Elastic IP to itself (see 3.3).

### 3.3 Boot sequence (user data) (not built)

Every instance, first boot or replacement, runs the same script:

1. Create a 1 GiB swap file.
2. Read its own instance ID from instance metadata (IMDSv2).
3. **Associate the Elastic IP** to itself (`aws ec2 associate-address --allow-reassociation`).
4. Read the k3s cluster token and the Flux deploy key from SSM Parameter Store.
5. Install k3s (pinned version).
6. Create the Kubernetes Secrets from SSM (DB password, origin-verify header value).
7. Bootstrap Flux against the repo. Flux then applies everything in `k8s/overlays/prod`: Redis, API, workers, KEDA, and the jobs.
8. The `ingest` Job runs after the rollout; its reconcile step finds the empty Redis vector index and rewrites every chunk key from MySQL (no `reindex` Job exists; see DESIGN.md §6.5).

Nothing in this script is specific to first boot, which is the whole point: a fresh machine and a replacement follow the identical path.

### 3.4 Health detection (not built)

EC2 status checks only catch hardware and OS failures. A machine can pass them while k3s is broken. Add an application-level check:

- A systemd timer on the node runs every 60 seconds and checks `k3s` readiness plus `http://localhost/readyz` through Traefik.
- After 5 consecutive failures, it calls `aws autoscaling set-instance-health --health-status Unhealthy`, and the group replaces the instance.
- Grace period of 600 seconds after launch so a booting node isn't killed before it's ready.

### 3.5 IAM additions (instance role) (not built)

| Action | Resource | Why |
|---|---|---|
| `ec2:AssociateAddress` | the Elastic IP allocation and instances tagged `project=glassbox` | Reattach the public IP on boot |
| `autoscaling:SetInstanceHealth` | the Glassbox Auto Scaling group | Self-report unhealthy |
| `ssm:GetParameter` | `/glassbox/k3s/*`, `/glassbox/flux/*` (added to existing `/glassbox/*`) | Cluster token and deploy key |

### 3.6 Documented future path: control plane HA (not built)

Recorded here as the production answer, with trade-offs:

| Option | How it works | Cost impact |
|---|---|---|
| 3 k3s servers with embedded etcd | Replicated state with leader election; survives losing 1 of 3 | 3x node cost |
| k3s with an external datastore | k3s stores cluster state in MySQL; 2+ stateless servers behind a load balancer. Would need a managed database such as RDS (there is none today) | 2x node cost + load balancer |
| EKS | AWS runs a multi-AZ control plane | Control plane fee + nodes |

### 3.7 Verification ("game day") (not built)

1. Note the current site state and a few test questions.
2. Terminate the instance from the console.
3. Measure time until the site answers questions again. Target: under 10 minutes.
4. Confirm answers match, query logs survived, and the vector index was rebuilt.
5. Record the measured recovery time in the README.

### 3.8 Cost (not built)

No change. Auto Scaling groups and launch templates are free; there is still one instance.

### 3.9 Revised plan: backups first, then the Auto Scaling group (planned, deferred 2026-10-01)

The scoped plan is `docs/superpowers/plans/2026-10-01-self-healing-node.md`; this is its summary. Why the original 3.1 to 3.8 had to change is in section 3a, which describes the node as it runs today. On 2026-10-01 the owner chose alarms and daily drive snapshots (section 3b) instead of phases 2 to 4, which are deferred.

- **Phase 1:** superseded by section 3b.
- **Phase 2:** nightly `mysqldump` to a private S3 bucket (30-day retention), plus "Ops" runbooks to back up now, check a restore in a scratch database, and restore. Under $0.05 a month, no downtime.
- **Phase 3:** a two-way reindex/reconcile between MySQL and Redis (separate PR), pinned k3s and Flux versions, and an idempotent boot script that enforces the order MySQL, restore, Flux, migrate, app, ingest, then the Elastic IP last. It also includes a tag-targeted zram association and a Flux credential decision (preferably no write key on the node), plus a branch ruleset that limits deploy keys to `deploy`.
- **Phase 4:** a launch template and a size-1 group at capacity 0, then a rehearsal on an isolated group with its own template and role, no Elastic IP, read-only Flux and its CronJobs suspended. Then a cutover: the old node is retagged, the new node takes the Elastic IP once its local `/readyz` passes (seconds of downtime), and the old node is stopped. A prepared rollback restarts the old node and moves the address back.
- **Not chosen first:** a persistent data volume (best recovery point, but more failure modes and a maintenance window to move the data).
- **Owner decisions still open for the deferred phases:** acceptable downtime for a replacement, whether the question log needs more than daily protection, the Flux credential, and the go-ahead for the cutover.


---

## 3a. Where the node's state lives today

Note for section 3, as of 2026-10-01: this section describes the running system, for comparison with Feature 1 above. MySQL runs in-cluster as a StatefulSet on a `local-path` volume on the node's root disk (DD1 §10.5), not on RDS as section 3 assumed, and Redis does the same. Git holds all configuration. Replacing the node today means an empty database, and the following would go with the instance:

- The k3s datastore (SQLite on the root volume) and both data volumes, including the `queries` log, which is the only table not rebuilt from Git.
- Flux's Git credential, which exists only as a Secret in the cluster.
- The Kubernetes Secrets made by hand from SSM with `k8s/bootstrap-secrets.sh` (their values stay safe in SSM).
- The zram association's target, which is the instance ID.

Documents and chunks would be rebuilt from the corpus baked into the image by the `migrate` and `ingest` Jobs, for a few cents of embeddings; the `queries` log is lost unless the volume is moved or backed up first. Ingest skips documents whose content hash is unchanged, so a Redis index lost on its own is not rebuilt from MySQL today. The EC2 default of simplified automatic recovery moves the instance to new hardware on a failed system status check and keeps its disk and address.

Section 3b adds alarms, daily snapshots of the whole disk and a one-click restore, which take effect once applied.

---

## 3b. Alarms, uptime probe and daily snapshots (owner's choice, 2026-10-01)

This is what the owner chose on 2026-10-01 to protect the single node. It is merged code that takes effect once the owner runs the Bootstrap workflow (IAM) and approves the Terraform apply; the uptime probe runs from the merge on. Details and runbooks: `infra/CI.md` "Alarms, uptime probe and daily snapshots".

- **Two status-check alarms.** `glassbox-node-recover` fires when the host-side check (`StatusCheckFailed_System`) fails for 2 minutes in a row and asks EC2 to recover the instance onto healthy hardware, keeping its ID, Elastic IP and disk. `glassbox-node-reboot` fires when the instance check (`StatusCheckFailed_Instance`) fails for 3 minutes in a row and reboots it, the same as "Ops · Reboot node". Both email the owner through the SNS topic `glassbox-alerts`, when they fire and when they clear.
- **Uptime probe.** A GitHub Actions workflow requests `https://basel.engineering/readyz` every 15 minutes and fails, which makes GitHub email the owner, if the site is not ready after three tries. It uses no secrets and no AWS access.
- **Daily snapshots of the whole disk.** The node has one EBS volume, the 20 GB root volume, holding the k3s datastore and both data volumes (MySQL and Redis). An AWS Data Lifecycle Manager policy snapshots it every day around 04:00 UTC and keeps the last 7. The snapshots are crash-consistent: restoring one is like the disk after a power cut at that moment, which InnoDB and SQLite recover from on start.
- **One-click restore.** "Ops · Restore from snapshot" (owner approval) puts the disk back to a chosen snapshot, or the newest, with EC2's replace-root-volume operation. The instance keeps its ID and Elastic IP and reboots onto the restored disk. Everything written since that snapshot, including questions asked, is lost. "Ops · List snapshots" and every "Ops · Diagnose" print the snapshot IDs and dates.
- **Cost:** about $0.40 to $0.80 a month, almost all of it snapshot storage. Two alarms are $0.20 a month, or free within CloudWatch's 10 free alarms; SNS email and the probe are free.
- **Not covered:** losing the instance itself (terminated) or its Availability Zone. The snapshots survive that, but turning one into a new node is a manual rebuild.

---

## 4. Feature 2: Corpus authoring guide and ingest validation

### 4.1 How content is organized

The only required labeling is **location**. Folder decides corpus:

| Location | Corpus |
|---|---|
| `corpus/about-me/**/*.md` | `about_me` |
| Allowlisted repo paths (DD1 6.4): `infra/`, `k8s/`, `services/`, `docs/` (`frontend/` is not scanned) | `about_system` |

The ingest job derives everything else automatically: source path, title (nearest heading), line range and chunk type (by extension). Not built yet: the GitHub URL at the deployed commit.

### 4.2 Recommended `about_me` file set

A recommendation, not the live list: the corpus today is `bio.md`, `google.md`, `microsoft.md`, `projects.md` and `skills.md`.

```
corpus/about-me/
  bio.md           # who you are, what you're looking for, in 3 to 5 short sections
  microsoft.md     # one file per role
  youtube.md
  fitbit.md
  projects.md      # one heading per project, including Glassbox itself
  skills.md        # grouped by area, each with where it was used
  bullet-bank.md   # exported from the bullet bank doc
```

### 4.3 Writing rules (the chunker rewards structure)

1. **Use headings generously.** Markdown is split at headings; each heading becomes a chunk's title and citation label.
2. **Make every section self-contained.** Name the subject in each section ("At YouTube, I built...", not "There, I built..."). Retrieval pulls sections out of context.
3. **One topic per section.** Mixed sections get retrieved for either topic and answer both badly.
4. **Aim for 100 to 400 words per section.** Much shorter chunks lack context; much longer ones get split at arbitrary points.
5. **Only public-safe content.** Everything in the corpus can be quoted to any visitor.

### 4.4 Optional front matter

Built: files may start with a small YAML block between `---` lines; the scanner strips it (`strip_front_matter` in `services/glassbox/ingest/scanner.py`) so it never reaches the index. Nothing requires it.

Not built yet: reading its values for filtering and boosting, in this format:

```yaml
---
type: role            # role | project | skills | bio | other
tags: [kubernetes, distributed-systems]
priority: normal      # normal | high (high gets a small ranking boost)
---
```

Not built yet: storing it in a new `documents.metadata` JSON column and copying it to the Redis chunk hashes as tag fields so vector search can filter on them.

### 4.5 Ingest validation (not built)

A `validate` step runs in CI on every pull request and at the start of every ingest Job. It never sends content anywhere; it only inspects files.

| Check | Level |
|---|---|
| Secret scanner match or denylisted path | **Error** (fails CI and the Job) |
| Invalid front matter | **Error** |
| Markdown section over 800 words with no subheadings | Warning |
| Chunk under 40 tokens after splitting | Warning |
| Section starting with a dangling reference ("As mentioned above", "There,", "This") | Warning |
| Duplicate headings within one file | Warning |

Warnings are printed as a PR comment with file and line, so content problems get fixed the same way code problems do.

---

## 5. Feature 3: Conversational chat

### 5.1 Conversation memory

The server stays stateless. The browser keeps the conversation, persists it in `localStorage` so it survives refreshes and return visits (5.5), and sends recent history with each question:

```json
{
  "question": "tell me more about that",
  "corpus": "about_me",
  "history": [
    { "role": "user", "content": "What did Basel do at YouTube?" },
    { "role": "assistant", "content": "At YouTube, Basel worked on ..." }
  ]
}
```

Server-side limits (enforced, not trusted from the client):

- At most the last **6 messages** (3 exchanges).
- At most **4,000 characters** total; older messages are dropped first.
- History is treated as untrusted user input. The system prompt states that prior assistant messages may be inaccurate and the retrieved context wins.
- Each corpus has its own conversation. Switching the toggle swaps to that corpus's saved conversation instead of mixing the two.

### 5.2 Follow-up question rewriting

"Tell me more about that" embeds into a vector that matches nothing useful. When history is present, the API first rewrites the question into a standalone query:

- **Input:** the last few messages + the new question.
- **Output:** a standalone question, e.g. "What else did Basel work on at YouTube beyond the ingestion pipeline?"
- **Model:** the answer model, Amazon Nova Lite (Haiku streaming is blocked by the account's first-time-use form), max 60 output tokens.
- **Skipped** when history is empty (first question), so the common recruiter path pays nothing extra.
- The rewrite is used for **retrieval**. The **answer** prompt gets the original question plus history, so the reply still sounds conversational.
- The rewrite appears as its own stage (`rewrite`) on the diagram, and the rewritten query is shown in small text under the sources, which makes the mechanism visible to engineers.

### 5.3 Cache behavior for follow-ups

| Cache | First question | Follow-up |
|---|---|---|
| Semantic answer cache | Read and write | **Skipped both ways.** The answer depends on the conversation, not just the words. |
| Embedding cache | Keyed on the question | Keyed on the rewritten query |
| Retrieval cache | Keyed on the question | Keyed on the rewritten query |

### 5.3.1 Implementation notes (feature/chat-context)

Where the build had to choose, it chose as follows:

- **Accepted input vs. used history.** `AskRequest.history` accepts up to 50 messages of 1–4,000 characters each and rejects anything larger (HTTP 422). The server then keeps the newest 6 messages within 4,000 total characters, dropping the oldest first. Roles are limited to `user`/`assistant`.
- **Follow-up traces.** The `answer_cache` stage is not emitted for a follow-up, since the cache is not consulted, and no answer-cache lock is taken. A follow-up whose question text matches a cached first question can therefore never be served that cached answer.
- **System prompt.** First questions keep the unchanged default grounding system prompt, and the prompt version (and so the answer cache) is untouched. Follow-ups append the untrusted-history rule to it, and the rewrite call uses its own system prompt that forbids following instructions found in the conversation.
- **Rewrite fallbacks.** If the kill switch is on, the budget cannot cover the quarter-unit, or the rewrite fails or returns nothing usable, retrieval uses the original question. In that case the `rewritten_query` field is omitted from the retrieval event.
- **Component questions.** Selecting an architecture node records its self-contained question in the About This System conversation but sends it without `history`, so it stays answer-cache eligible. Later typed follow-ups include it as context.
- **Rewritten query display.** The rewritten query is shown under the answer's sources while live. It is not persisted, matching the §5.5 storage shape.
- **Query log (§9.3).** Migration `0003_query_turn_columns` adds `turn_index` (`TINYINT UNSIGNED NOT NULL DEFAULT 0`) and `rewritten_query` (`VARCHAR(1000) NULL`) to `queries`. `question` stays the original wording. `turn_index` is the number of `user` messages in the history the client sent, before server trimming, so it keeps counting past the six-message window (0 = first question). `rewritten_query` holds the rewrite that retrieval actually used and is `NULL` for first questions and for every rewrite fallback. The other §9.3 changes (`mode` `stopped`, `ttft_ms`, `documents.metadata`) belong to other sections and are not part of this change.
- **Budget keys.** Whole answers are still counted one per answer in the original `budget:llm:{date}` key, and rewrites are counted in quarter-units in a separate `budget:llm:rw:{date}` key. One atomic Lua script checks `4 × answers + rewrites + requested` against `4 × cap`, then increments the matching key. Old and new API pods therefore share the answer counter during a rolling deploy, so there is no transition window for answers. Accepted residual: old pods compare only answers against the cap, so they do not see rewrite usage during the brief overlap.

### 5.4 Budget

A rewrite counts as 0.25 of a generated answer against the daily cap (DD1 7.2), since it is a much smaller call.

### 5.5 Persistence in localStorage

Conversations are saved in the browser's `localStorage`, so a refresh or a return visit restores the chat. Nothing about conversations is stored server-side beyond the anonymous query log (DD1 7.1).

**Storage shape** (one key per corpus):

```ts
// key: "glassbox:conv:v1:about_me" and "glassbox:conv:v1:about_system"
type StoredConversation = {
  version: 1;
  updatedAt: number;            // epoch ms
  messages: {
    id: string;
    role: "user" | "assistant";
    content: string;
    state?: "done" | "stopped" | "retrieval_only";  // assistant only
    sources?: { source_path: string; title: string; url?: string }[];
    createdAt: number;
  }[];
};
```

**Rules:**

- **Save on settle, not per token.** Write after a message reaches `done`, `stopped` or `retrieval_only`, and after each user message. Messages in `thinking`, `streaming` or `error` are never saved, so a refresh mid-answer never restores a half-written reply as if it were complete.
- **Display vs. send.** Keep up to the last 50 messages for display; only the last 6 are sent as `history` (5.1 limits still apply server-side).
- **Expiry.** On load, discard a conversation whose `updatedAt` is older than 7 days, so a returning visitor weeks later starts fresh.
- **Versioned key.** The `v1` in the key lets a future format change ignore old data instead of crashing on it.
- **Fail safe.** Every read and write is wrapped in `try/catch`. If storage is unavailable, full, blocked (some private browsing modes), or holds data that fails validation, the app falls back to in-memory only and works normally.
- **New chat.** A "New chat" button clears the current corpus's key and resets the view. Suggested-question chips reappear when a conversation is empty.
- **Multiple tabs.** Last write wins, and a change saved in another tab refreshes that conversation here through the `storage` event, unless this tab is streaming into it (`App.tsx`).
- **Privacy note.** Data stays in the visitor's own browser. On desktop a small line under the input ("Chats are saved in this browser. New chat clears it.") keeps that transparent, which matters on shared computers. Below 768px New chat sits in the footer instead (DD1 §4.2): its tooltip carries the note, and a line under the suggested questions repeats it.

**Restored-state behavior:** restored assistant messages render with their sources, but the architecture panel starts idle (traces are not persisted; they describe a past request, and replaying them would be misleading).

---

## 6. Feature 4: Live chat UX

### 6.1 Message states

Each assistant message moves through explicit states:

```
idle -> thinking -> streaming -> done
                 \-> stopped
                 \-> error
                 \-> retrieval_only
```

| State | Shown when | UI |
|---|---|---|
| `thinking` | Request sent, no tokens yet | Animated typing dots; diagram animating through stages |
| `streaming` | First `token` event arrived | Text grows; blinking caret at the end; Stop button visible |
| `done` | `done` event | Caret removed; sources and timing shown |
| `stopped` | Visitor pressed Stop | Partial text kept, muted "Stopped" label |
| `error` | `error` event or network failure | Inline message with Retry |
| `retrieval_only` | Daily budget reached or LLM kill switch on | A playful budget reply (§6.8) above the sources |

### 6.2 Stop button that stops server-side

The browser side is simple: an `AbortController` cancels the `fetch`. The important part is the server:

- The token relay loop checks for client disconnect (`await request.is_disconnected()`) between chunks.
- On disconnect, the API closes the Bedrock response stream, which stops generation and therefore token billing.
- The query is logged with `mode = 'stopped'` and the tokens actually generated.
- Stopped answers are **not** written to the semantic answer cache.

Acceptance: after pressing Stop, no further output tokens are billed beyond about 1 second of generation (verified by comparing logged `tokens_out` with the visible text).

### 6.3 Auto-scroll

- Follow new content while the visitor is at the bottom (within 80 px).
- If they scroll up, stop following and show a small "Jump to latest" pill.
- Clicking the pill, or sending a new question, resumes following.

### 6.4 Input behavior

- Enter sends; Shift+Enter adds a new line.
- The input stays enabled while streaming; sending a new question stops the current one first.
- Up arrow in an empty input recalls the previous question.

**Implementation notes (feature/chat-ux-leftovers).**

- **Typing while streaming.** The ask box is never disabled. While an answer streams, the button beside it is Stop when the box is empty and a Send arrow (accessible name "Stop answer and send question") once it holds text; Enter does the same. Sending stops the current answer through the Stop path of §6.6 (the partial answer is kept as `stopped`, saved, and sent as history), and the new question is asked once that stop has rendered, so its `history` contains the stopped answer exactly as it was saved. A component question queued behind the stopped answer (§5.3.1) is dropped; the visitor's newer question wins. The 400 ms Stop guard still applies to the Stop button. Suggested questions only show in an empty conversation, so they stay disabled while streaming.
- **Up-arrow recall.** Up in an empty ask box fills in the last question sent in the current topic's conversation (chats are per topic, §5.1), with the caret at the end. It does nothing when the box has text, when the caret is not on the first line (the box is single-line today; the check is there for a future textarea), with Shift/Alt/Ctrl/Meta held, or during IME composition (`isComposing`, or keyCode 229 in Safari), so candidate selection keeps working. Only the last question is recalled; there is no deeper history walk. Logic: `frontend/src/lib/askInput.ts`.
- **Retry (§6.1 `error`).** A Retry button (≥44px tap target below md, accessible name "Retry question") sits under the conversation's latest failure reply only; older failures, `stopped` answers and daily-budget replies (§6.8, recognized by their `budget` flag, not their text) get none. Retry **replaces** the failed attempt: the failure reply (and any partial answer marked `error`) is removed, the failed question stays in place, and the same question is asked again, so the saved conversation reads as if the first attempt had worked. Its `history` is the conversation before the failed question, which is exactly what the failed request sent: the question is never duplicated and no error text reaches the API. A failed first question retries with no history, so it stays answer-cache eligible, and a failed component question retries without history, as it was first sent. Retry is hidden while any request is in flight and a second click is ignored (one request per click). After a `rate_limited` error it stays disabled with a "Retry in Ns" countdown until the server's `retry_after_s` has passed; that deadline is live only and not persisted, so after a reload Retry is enabled. The Retry button disappears with the failure reply, so focus moves: on desktop to the ask box; on phones to the retried question, which is focusable for this (`tabIndex=-1`), so the on-screen keyboard does not pop up and keyboard and screen-reader users keep their place, with the new answer right below it. A reload while a retry is in flight comes back to the failed question with its failure reply and a working Retry: the saved conversation keeps the failure reply until the retried answer settles (done, stopped, or failed again), and only then is the new state saved. This matches a reload during a normal answer, which also restores only what had settled (the question, without its unfinished reply); the in-flight reply is never saved, and nothing is saved as an error that did not happen. Logic: `frontend/src/lib/chatRetry.ts` (`holdSaveDuringRetry`).
- **Screen-reader announcements.** The chat has one polite, off-screen live region (§6.5) that changes once per settled event, never per token: the settled answer or failure reply; "Answer stopped." after Stop (instead of re-reading the partial text, which was visible while it streamed); nothing for an answer interrupted by stop-and-send, since the new question starts at once (the region used to hold the stopped partial for one render); and "Retry is available now." when a rate-limit countdown on Retry ends (the button's accessible name also changes from "Retry question, available in N seconds" to "Retry question"). Logic: `frontend/src/lib/chatAnnouncement.ts`.

### 6.5 Accessibility

- Announcements happen once per settled event (an answer on `done`, a failure reply, Stop), not per token (per-token announcements are unusable with screen readers). Implemented as a separate off-screen polite live region rather than on the message list itself; see the §6.4 notes.
- The diagram respects `prefers-reduced-motion`: nodes change color without pulsing or edge animation.
- Stop, Retry and the corpus toggle are reachable and operable by keyboard.

### 6.6 Implementation notes (feature/chat-stream-resilience)

- **Stop (§6.2).** While an answer streams, Send becomes a Stop button (≥44px tap target). It aborts the `fetch`. The partial answer, including text that had arrived but was not yet revealed, is kept with state `stopped` and a muted "Stopped" label. It is saved and sent as history like any settled answer, and the chat is immediately free for a new question. A reply stopped before its first token is saved too, with empty text, so a reload still shows "Stopped". It is left out of `history`, since the server rejects empty turns. If the answer had already finished arriving and was only still being revealed, Stop shows the rest at once and settles it as `done`. A Stop within 400 ms of sending is ignored, so a double-click on Send does not stop its own answer.
- **Server side.** Starlette cancels the response when the client disconnects. The heartbeat wrapper (§7.6) then cancels and closes the event generator, which cancels or closes the provider stream (the Bedrock adapter closes its response stream in `finally`). As a fallback that does not depend on the Starlette version, the wrapper also polls `request.is_disconnected()` every second, whether or not tokens are flowing, which bounds post-Stop generation to about a second either way. A Stop during a cold Bedrock call, while `converse_stream` is still waiting for response headers in its worker thread, cannot interrupt that thread. The call is shielded, and the stream it returns after the cancellation is closed as soon as it arrives. The query is logged with `mode = 'stopped'` (migration `0004_query_mode_stopped`); a stop before the LLM step logs zero tokens.
- **Token counts.** `tokens_in`/`tokens_out` (query log and `done` event) are Bedrock's measured usage when the stream reaches its final `metadata` event, which is the case for every completed Bedrock answer. Otherwise they are whitespace-word estimates. That applies to the fake provider and to every stopped answer, because a stopped stream never reaches the usage event. The §6.2 acceptance check against logged `tokens_out` is therefore approximate for stopped answers; the billed figure is Bedrock's CloudWatch `OutputTokenCount`. The budget slot reserved for the answer is not refunded, since the prompt and any output were billed. Stopped answers never reach the answer-cache write. A client leaving after an error event is not logged as stopped.
- **Failures as chat replies (§6.1 `error`).** A failed request no longer shows an error banner. It adds an assistant message in the tab's conversation with a subtle dashed style. Generic failures pick one of 20 light-hearted replies (`frontend/src/lib/errorReplies.ts`), chosen at random and never the same twice in a row. These cover non-OK HTTP, network drops, the idle watchdog, and server `internal` errors. Failures the visitor needs details for keep them in a friendly form: a rate limit includes the retry-after seconds, and `budget_exhausted` gets one of the playful budget replies (§6.8), keeping any sources already found. A kill switch or budget stop arrives as `retrieval_only` with sources and gets a budget reply too. Error replies are saved with state `error`, a deliberate extension of the §5.5 shape, so a reload shows the same chat. They are never sent as `history` and never count as answers. The question they answer is left out of `history` too (a question goes into `history` only once its reply has settled as `done`, `stopped` or `retrieval_only`), so re-sending a failed question, by Retry or by Up-arrow and Enter, never puts it in twice. Partial text cut off by a failure stays visible and is also marked `error`. The technical message goes to the browser console, and the mobile inspector shows the same reply.
- **Auto-scroll (§6.3).** Implemented as specified: follow while within 80 px of the bottom, a "Jump to latest" pill otherwise, and sending a question or switching tabs resumes following.
- **Built later.** §6.4 (input enabled while streaming, send-stops-current, Up-arrow recall) and the Retry control in §6.1's `error` state: see the implementation notes in §6.4. `DoneEvent.mode` accepts `stopped` per §9.2, but in practice the stopped client never receives a `done` event.

### 6.7 Playful "I don't know" replies

When `done.abstained` is true and the streamed text, trimmed, is exactly the canonical sentence (a client-side guard), the client replaces the displayed text with one of 20 light-hearted replies from `frontend/src/lib/idkReplies.ts` (some playfully blame Basel, never the visitor; About This System draws from the lines that fit it plus a few about the docs), random and never the same as the last one shown or saved in that conversation. The swap happens at `done`, after the short canonical sentence has been revealed. The message is stored with its shown text plus `idk: true` (an optional field; older saves load unchanged). Conversation history sends the canonical sentence for such a turn rather than the joke, so the server and the §5.2 rewriter see what was actually said, and the user/assistant alternation stays intact (omitting the turn would leave two user turns in a row). The mobile architecture inspector shows the same stored text. The server, caching and query logging are unchanged.

### 6.8 Playful daily-budget replies

When the daily LLM budget is spent or the incident kill switch is on, the server ends with `done.mode = "retrieval_only"`: sources but no answer text. If no text arrived, the client shows one of 20 jokey replies from `frontend/src/lib/budgetReplies.ts` above the sources (some playfully blame Basel, never the visitor), random and never the same as the last one shown or the latest one saved in that conversation. The lines are written for the daily budget, the common cause, and a few joke about money even when the rare kill switch is the cause. None names an amount or a precise reset time, and none makes a firm promise of when answers return (at most "tomorrow-ish", "try me tomorrow" or "later"); every line points to the sources below. The reply is picked once at `done` and stored with the message as `budget: true` (an optional field), so it stays the same across re-renders and reloads. History sends the canonical sentence `CANONICAL_BUDGET_REPLY` (the fixed note the chat showed before this change) for such a turn, never the joke, which keeps the user/assistant alternation as in §6.7. A `budget_exhausted` error (in the §9.2 contract; the current server does not emit it) gets a budget reply in the same way, saved as `error` with `budget: true` and never sent as history. Retry is skipped for any message with `budget: true`; it no longer compares reply text. A save from before the flag that holds the old fixed budget failure text is marked `budget` on load, so Retry stays off it. The server, caching and query logging are unchanged.

---

## 7. Feature 5: Streaming delivery hardening

### 7.1 Transport decision (recorded)

**SSE over a single `POST /api/ask` response**, not WebSockets. The interaction is one question in, one streamed answer out; SSE handles it over plain HTTP, passes through Cloudflare and Traefik without special configuration, and needs no connection management between questions. WebSockets would be warranted only for continuous two-way traffic, which this product does not have.

### 7.2 Why streams break in production

Any layer that buffers or compresses the response delays tokens until the buffer flushes, so the visitor sees the whole answer arrive at once. Locally there are no such layers, so this bug typically appears only after deploy.

### 7.3 Required settings, layer by layer

| Layer | Setting |
|---|---|
| **Cloudflare** (`/api/*` route) | Cache Rule: bypass cache; Auto Minify and Rocket Loader disabled on this route |
| **Cloudflare origin timeout** | Free-tier edge timeout is 100 s; no single stage (including a cold Bedrock call) may exceed it |
| **Traefik** | No buffering middleware on the API route (the default) |
| **API response headers** | `Content-Type: text/event-stream`, `Cache-Control: no-cache`, `X-Accel-Buffering: no` |
| **API server** | Flush after every event; no response compression middleware on streaming routes |

### 7.4 Heartbeats

The API sends an SSE comment line (`: ping`) every 15 seconds while a stream is open. Browsers ignore comment lines, but they keep the connection active through proxies during slow stages, such as a cold Bedrock call.

### 7.5 Verification (not built yet)

- **Scripted check:** `curl -N` against `https://basel.engineering/api/ask` (through Cloudflare), recording the arrival time of each event. Tokens must arrive spread over time, not in one burst at the end.
- **Metric:** time to first token (TTFT), measured in the browser and logged. Target: production TTFT within 300 ms of local TTFT.
- Run the check in CI after every deploy (Phase 6) so a Cloudflare setting change can't silently break streaming.

### 7.6 Implementation notes (feature/chat-stream-resilience)

- **Heartbeat.** `services/glassbox/api/sse.py` `with_heartbeat()` wraps the `/api/ask` event generator. It awaits the next event in its own task and races it against a timer, sending `: ping` whenever nothing has been sent for 15 s. This covers the worker wait, rewrite/LLM cold starts, and slow tokens. Events are never reordered or dropped, and no ping is sent while events flow. Cleanup after a disconnect runs in a separate task so its own awaits (query-log write, lock release, Redis close) are not re-cancelled by the response's cancel scope.
- **Client idle watchdog.** `askQuestion` aborts after 45 s without a single received byte (pings count), including the wait for response headers, and reports "Connection lost — try again." A network failure (a `TypeError` from `fetch`/`read`) gets the same message. This releases the chat instead of leaving it locked on a half-open stream. The timer logic is a pure module (`frontend/src/lib/idleWatchdog.ts`) unit-tested with Node's built-in runner (`npm test`); no extra test dependency.

---

## 8. Network: S3 gateway endpoint (not built yet)

- Add `aws_vpc_endpoint` (type `Gateway`, service `com.amazonaws.us-east-1.s3`) associated with the public and private route tables.
- **Free**, and any S3 traffic from inside the VPC then stays on the AWS network.
- Honest note: the node currently makes little S3 traffic (the frontend is uploaded from GitHub Actions). It is included because it costs nothing and is a prerequisite for the private-subnet production path in DD1 section 20.

---

## 9. Changes to DD1 contracts and schema

### 9.1 API request

```ts
type AskRequest = {
  question: string;
  corpus: "about_me" | "about_system";
  history?: { role: "user" | "assistant"; content: string }[]; // new
};
```

### 9.2 Trace events

- New `NodeId`: `"rewrite"`.
- `DoneEvent.mode` gains `"stopped"`.
- New field on `DoneEvent`: `abstained: boolean`. True only when the whole answer, normalized for case, punctuation and "do not", equals the canonical "I don't know from what I have." (the model said exactly that, or no sources were found). It is stricter than the loose `is_abstention` check used for caching and logging, so an answer such as "I don't know from what I have, but he built X on k3s" is false and shown as written. False for real answers, cache hits and `retrieval_only`. The client uses it to show a playful reply instead (§6.7).
- New optional field on `RetrievalEvent`: `rewritten_query?: string`.
- Heartbeat comments (`: ping`) may appear anywhere in the stream; parsers must ignore them.

### 9.3 MySQL

Built: `mode` `stopped` (Alembic `0004`), `turn_index` and `rewritten_query` (`0003`). Not built yet: `documents.metadata` (§4.4) and `ttft_ms` (§7.5).

```sql
ALTER TABLE documents ADD COLUMN metadata JSON NULL;  -- not built yet

ALTER TABLE queries
  MODIFY COLUMN mode ENUM('full','retrieval_only','stopped') NOT NULL,
  ADD COLUMN turn_index      TINYINT UNSIGNED NOT NULL DEFAULT 0,  -- 0 = first question
  ADD COLUMN rewritten_query VARCHAR(1000) NULL,
  ADD COLUMN ttft_ms         INT NULL;  -- not built yet
```

### 9.4 Redis

- Not built yet: chunk hashes gain optional tag fields from front matter (`type`, `tags`) for filtered search.
- Rewrites count 0.25 of an answer: quarter-units in a separate `budget:llm:rw:{date}` counter (built; see §5.3.1).

### 9.5 Terraform

- Not built yet: in `modules/compute`, `aws_instance` replaced by a launch template + Auto Scaling group; new IAM statements (3.5); new SSM parameters for the k3s token and Flux deploy key.
- Not built yet: in `modules/network`, the S3 gateway endpoint.
- Built: the Cloudflare Cache Rule for `/api/*` (7.3) is managed by Terraform's `modules/edge`, next to the apex DNS record (DD1 §10.3).

---

## 10. Build plan integration

| DD1 phase | Adds from this doc |
|---|---|
| Phase 0: Scaffold | Corpus validation step in CI (4.5), not built yet |
| Phase 1: Backend locally | History in request, rewrite stage, follow-up cache rules, disconnect handling (5, 6.2): built |
| Phase 2: Real models + eval | Add follow-up question pairs to the eval set, scored after rewriting: the multi-turn cases are in `eval/golden.yaml`; no paid run has scored them yet |
| Phase 3: Frontend | Message states, Stop, auto-scroll, input behavior, accessibility (6); localStorage persistence and New chat (5.5): built |
| Phase 4: AWS + Kubernetes | Cloudflare Cache Rule bypassing `/api/*` (7.3): built. Launch template + ASG, boot script, health timer, S3 endpoint (3, 8): not built yet |
| Phase 5: Autoscaling demo | No change |
| Phase 6: CI/CD | Post-deploy streaming check (7.5), not built yet |
| Phase 7: Polish | Game day recovery test with measured time in README (3.7), not built yet |

---

## 11. Additional resume bullets

Fill in numbers only after measuring.

- Designed self-healing infrastructure for a single-node Kubernetes cluster (Auto Scaling group, application-level health checks, GitOps rebuild), restoring service in [X] minutes after instance termination with no data loss.
- Implemented multi-turn RAG with LLM-based query rewriting, raising follow-up question recall@5 from [X] to [Y].
- Built token-streaming chat over Server-Sent Events through Cloudflare with server-side cancellation, keeping time to first token at [X] ms.

---

## 12. Open questions

| Question | Options | Leaning |
|---|---|---|
| Rewrite model | Same model as answers vs. a smaller, cheaper one | Resolved: the answer model, Nova Lite; volume is tiny |
| History length | 3 exchanges vs. 5 | Resolved: 3 (6 messages); recruiters rarely go deeper |
| Game day cadence | Once or monthly | Open until self-healing is built (not built yet): once before sharing the site, again after major infra changes |
