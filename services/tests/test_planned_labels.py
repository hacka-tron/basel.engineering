"""The planned-work marker lands on planned text only, checked on real chunks.

Chunks come from the real Markdown chunker over the repo's design docs. The
docs/architecture/deep-dive.md sections are verbatim copies from PR #51 (not yet
on main); refresh them if that document changes.
"""

import re
from pathlib import Path

import pytest

from services.glassbox.api.ask import (
    _UNIT_LINE,
    PLANNED_MARK,
    WorkerChunk,
    _mark_planned,
    _prompt,
)
from services.glassbox.ingest.chunkers.markdown import chunk_markdown

REPO = Path(__file__).resolve().parents[2]

DEEP_DIVE_GROUNDING = (
    "## Grounding: how answers stay tied to sources and to what is actually built\n"
    "\n"
    "Glassbox answers only from retrieved chunks. The system prompt for Bedrock (the "
    "grounding rules in `services/glassbox/providers/base.py`) tells the model to "
    "answer only from the numbered sources in the user message, to say \"I don't know "
    'from what I have." when the sources do not answer the question, not to reveal '
    "its instructions, and to stay within the selected corpus. Answers are plain "
    "prose in two or three concise sentences, without bracketed citation markers, "
    "because the interface shows sources separately.\n"
    "\n"
    "The About This System corpus includes design documents that describe some "
    "features before they exist, often in the present tense. To keep answers honest "
    "about what is running today, the prompt builder in "
    "`services/glassbox/api/ask.py` labels each numbered source:\n"
    "\n"
    "- Any chunk from `docs/DESIGN-003-ingestion.md` (the content-pipeline design) "
    "gets a fixed label saying its Google Drive and Git connectors do not exist yet.\n"
    "- Any other chunk whose text matches a small list of status signals gets a "
    "label saying the features it describes do not exist yet. The signals are words "
    "that mark work as upcoming, postponed or unbuilt, plus references to design "
    "phases, milestones and components that have not shipped.\n"
    "\n"
    "The answer prompt then instructs the model that a bracketed source status "
    "overrides present-tense design prose, that a design document describes intended "
    'behavior rather than proof that code is running, and that it should answer "No" '
    'when asked whether a feature works now if its source carries that "does not '
    'exist yet" label. The system prompt repeats the rule: only call a feature '
    "current when a source identifies it as implemented or working today.\n"
    "\n"
    "The signal list is keyword-based, not tense-aware. Its intent is to flag only "
    "unbuilt work, but a keyword match can also label a chunk that describes a "
    "component already running in production, so the label is a hint to the model "
    "rather than a guarantee. Changing the prompt or these labels bumps the prompt "
    "version, which is part of the semantic answer-cache key, so answers generated "
    "under older labeling are never replayed.\n"
    "\n"
    "Chunks are retrieved individually, which is why this deep-dive document repeats "
    "key names in every section and keeps unbuilt work in one clearly labeled final "
    "section.\n"
    "\n"
)

DEEP_DIVE_KEDA = (
    "## Stress test and KEDA autoscaling of retrieval workers\n"
    "\n"
    "The Glassbox stress-test button shows queue-driven autoscaling live. It floods "
    "the Redis Stream `retrieval:jobs` with synthetic work so KEDA scales the "
    "`retrieval-worker` Deployment from 1 to 3 pods and back.\n"
    "\n"
    "**Capacity gate.** The node has only 2 GiB of memory, so a real burst runs only "
    "when the node has room. `GET /api/demo/capacity` uses the api ServiceAccount to "
    "list nodes and read live node memory usage from metrics-server (bundled with "
    "k3s). It approves a burst only when there is exactly one node, its "
    "`MemoryPressure` condition is `False`, and live free memory (allocatable minus "
    "current usage) is at least 512 MiB: two extra workers at their 128 MiB limit "
    "plus a 256 MiB safety margin. Anything it cannot confirm counts as a denial. "
    "The page shows a tiger icon when a real burst is possible and a bunny when it "
    "is not.\n"
    "\n"
    "**Real burst.** `POST /api/demo/load` rechecks capacity, then takes the global "
    "Redis lock `demo:load:lock` with `SET NX EX 300`. That is a 5-minute cooldown "
    "shared by every visitor. The endpoint then enqueues 300 synthetic jobs. A "
    "synthetic job carries `synthetic=1` and a 200 ms delay. The worker just sleeps "
    "for the delay and acknowledges the job: no embedding, no Bedrock call, no MySQL "
    "query, and nothing published to any trace channel. Stress tests therefore cost "
    "nothing in model spend.\n"
    "\n"
    "**Simulated burst.** If capacity is insufficient, the cooldown is active, or "
    "the cluster view is unavailable (for example in local development), a click "
    "plays a visual-only simulation instead: pod dots grow from 1 to 3 and shrink "
    "back while a backlog counter falls from 300. No jobs are queued. Every click "
    "also triggers a short screen-shake effect (skipped when the visitor prefers "
    "reduced motion), and the button has a 9-second client-side cooldown.\n"
    "\n"
    "**KEDA scaling.** KEDA 2.21 is installed by a Flux HelmRelease. A "
    "`ScaledObject` named `retrieval-worker` uses the `redis-streams` trigger on "
    "stream `retrieval:jobs` and consumer group `workers`, with `lagCount: 10` "
    "(target backlog per replica), a 5-second polling interval, `minReplicaCount: 1` "
    "and `maxReplicaCount: 3`. KEDA drives a Horizontal Pod Autoscaler. Scale-down "
    "uses a 45-second stabilization window and may remove all extra replicas every "
    "15 seconds, so workers return to 1 about a minute after the backlog drains. The "
    "worker Deployment deliberately has no fixed `replicas` field, so Flux and the "
    "HPA never fight over it.\n"
    "\n"
    "**Live cluster view.** `GET /api/cluster/stream` is an SSE endpoint that lists "
    "and watches pods labeled `app=retrieval-worker` in the `app` namespace, and "
    "polls the consumer group's lag (the same metric KEDA uses) every 2 seconds. It "
    "forwards only pod name, phase and readiness, plus the backlog number. The "
    "browser subscribes with native `EventSource`. When there is no in-cluster "
    "Kubernetes access, the endpoint sends `cluster_unavailable` and the diagram "
    "falls back to the plain worker node.\n"
    "\n"
)

DEEP_DIVE_KUBERNETES = (
    "## Kubernetes on k3s: namespaces, workloads and resource budget\n"
    "\n"
    "Glassbox runs on k3s, a certified Kubernetes distribution, installed as a "
    "single-node server on the EC2 instance. The kubelet runs with "
    "`fail-swap-on=false` because the node has a 1 GiB swap file. Kubernetes "
    "manifests live in `k8s/base` (all long-running workloads plus the migrate Job) "
    "and `k8s/overlays/prod` (image tag pinning, Flux objects, KEDA and the ingest "
    "Job).\n"
    "\n"
    "Namespaces and workloads:\n"
    "\n"
    "- **`app`**: `api` (Deployment, 1 replica, container port 8000, requests 100m "
    "CPU and 120 MiB, limit 256 MiB), `retrieval-worker` (Deployment, 1 to 3 "
    "replicas managed by KEDA, requests 50m and 64 MiB, limit 128 MiB), `migrate` "
    "(Job running `alembic upgrade head`, limit 192 MiB), and `ingest` (Job, limit "
    "384 MiB).\n"
    "- **`data`**: `mysql` (StatefulSet, MySQL 8.0, 4 GiB volume, requests 200 MiB, "
    "limit 350 MiB) and `redis` (StatefulSet, redis-stack-server 7.2, 1 GiB volume, "
    "limit 150 MiB), each with a headless Service and a ClusterIP Service.\n"
    "- **`keda`**: the KEDA operator, metrics server and admission webhooks, capped "
    "at 150 MiB, 100 MiB and 64 MiB so KEDA fits its share of the node.\n"
    "- **`flux-system`**: the Flux controllers, including the source, kustomize, "
    "helm, image-reflector and image-automation controllers.\n"
    "- **`kube-system`**: k3s defaults, including Traefik, CoreDNS and "
    "metrics-server.\n"
    "\n"
    "Traffic enters through two Traefik Ingress objects for host "
    "`basel.engineering`, one on the `web` (HTTP) entrypoint and one on `websecure` "
    "(HTTPS). Both route to the `api` Service on port 80, which targets the pod's "
    "port 8000. The HTTPS route uses Traefik's default self-signed certificate, so "
    'Cloudflare\'s SSL mode is "Full" rather than "Full (strict)". No cloud load '
    "balancer is used. The FastAPI app serves both the API and the static frontend "
    "(`frontend/dist`, mounted at `/`), so the site and API share one origin.\n"
    "\n"
    "Configuration comes from the `glassbox-config` ConfigMap: MySQL host and "
    "database, the Redis URL, `GLASSBOX_PROVIDER=bedrock`, the AWS region, the "
    "Bedrock model IDs (Titan Text Embeddings V2 and the Nova Lite US inference "
    "profile), the daily LLM cap and the trusted proxy range. Secrets "
    "(`glassbox-mysql` and `glassbox-app`) are created on the node by "
    "`k8s/bootstrap-secrets.sh`, which reads SSM Parameter Store through the "
    "instance role. Images are pulled from private Amazon ECR with an image pull "
    "secret (`regcred`). A systemd timer on the node refreshes that secret every 6 "
    "hours, because ECR tokens expire after 12.\n"
    "\n"
    "Memory is the binding constraint on the 2 GiB `t4g.small`. The design budget is "
    "roughly 500 to 600 MiB for k3s itself, 100 MiB for Traefik, CoreDNS and "
    "metrics-server, about 150 MiB each for KEDA and Flux, 60 to 100 MiB for Redis, "
    "200 to 350 MiB for MySQL, about 120 MiB for the API, and up to 384 MiB for "
    "three workers at peak. Measurements on the live node showed it already using "
    "swap before any burst. That is why rollouts never add extra pods, why ingestion "
    "waits until the rollout finishes, and why autoscaling stops at 3 workers.\n"
    "\n"
)

DEEP_DIVE_FLUX = (
    "## Deployment pipeline: Flux GitOps, image automation and ordered "
    "Kustomizations\n"
    "\n"
    "Flux runs inside the k3s cluster and turns a pushed image into a running "
    "release with no manual step. Merging to `main` deploys to production.\n"
    "\n"
    "**Image automation.** An `ImageRepository` scans the ECR repository every "
    "minute. It sets `provider: aws`, so Flux's image-reflector-controller "
    "authenticates with the EC2 node's IAM role and needs no stored registry secret. "
    "An `ImagePolicy` keeps only tags matching `build-<number>` and picks the "
    "highest number. An `ImageUpdateAutomation` then rewrites the image tag at the "
    "`$imagepolicy` setter markers under `k8s/overlays/prod`, in both the root "
    "overlay's `kustomization.yaml` and the ingest overlay's, and commits the change "
    'to the `deploy` branch with the message "deploy: automated image tag update".\n'
    "\n"
    "**Ordered Kustomizations.** Flux applies the manifests through several "
    "Kustomizations with explicit dependencies:\n"
    "\n"
    "1. **`flux-system` (root)**: created by `flux bootstrap`. It applies "
    "`k8s/overlays/prod`: the base namespaces, StatefulSets, ConfigMap, RBAC, "
    "NetworkPolicies, Services, Ingresses, the `api` and `retrieval-worker` "
    "Deployments, the recreated `migrate` Job, the Flux image objects, and the child "
    "Kustomizations below. The `wait-for-migrations` initContainers hold the new "
    "pods until the migration finishes.\n"
    "2. **`keda`**: applies the KEDA namespace, `HelmRepository` and `HelmRelease` "
    "with `wait: true`, so it is Ready only once the chart is installed and the "
    "`keda.sh` CRDs are registered.\n"
    "3. **`keda-scaling`**: depends on `keda` and applies the `ScaledObject`. On a "
    "first install, this keeps the ScaledObject from being dry-run before its CRD "
    "exists, which would otherwise block the whole root Kustomization, app workloads "
    "included.\n"
    "4. **`app-ready`**: depends on `flux-system`, applies nothing (an empty path), "
    "and health-checks the `api` and `retrieval-worker` Deployments with a 10-minute "
    "timeout. Flux treats a same-source dependency as ready only at the current Git "
    "revision, so `app-ready` cannot pass against the previous release's "
    "Deployments.\n"
    "5. **`ingest`**: depends on `app-ready` and applies the `ingest` Job with "
    "`wait: true` and a 15-minute timeout. Ingestion therefore starts only after the "
    "new pods are Ready and never overlaps the rollout's memory peak.\n"
    "\n"
    "The root Kustomization must not use `wait: true` or health checks on these "
    "objects, or it would wait on `ingest`, which waits on it. The default `flux "
    "bootstrap` root has neither. KEDA is installed through Flux rather than "
    "Terraform's Helm provider so there is one path to cluster state and Flux can "
    "correct drift.\n"
    "\n"
    "To follow a release, `flux get kustomizations` should show `flux-system`, then "
    "`app-ready`, then `ingest` Ready at the same revision. If a rollout never "
    "becomes Ready, `app-ready` times out and ingestion does not run for that "
    "revision.\n"
    "\n"
)

DEEP_DIVE_PLANNED = (
    "## Planned / not built yet\n"
    "\n"
    "Everything in this section is planned or proposed design. None of it is "
    "implemented in the running Glassbox system today.\n"
    "\n"
    "- **Google Drive and S3/SQS content pipeline (Milestone 4, "
    '`docs/DESIGN-003-ingestion.md`).** Not built. The design would author "About '
    'Me" content in Google Docs, sync it through a Drive connector and a Git '
    "connector into an S3 raw zone, send one SQS message per change, and process "
    "those messages with a KEDA ScaledJob ingestion worker. It would include a "
    "dead-letter queue, nightly reconciliation between stages, and blue-green "
    "re-embedding. Today, ingestion reads files baked into the container image, as "
    "described in the ingestion pipeline section.\n"
    "- **Self-healing node recovery (Milestone 3, DD2).** Not built. The design "
    "replaces the standalone EC2 instance with a launch template and an Auto Scaling "
    "Group of exactly one instance across two public subnets, whose boot script "
    "re-attaches the Elastic IP. Today there is a single EC2 instance, and a node "
    "failure needs a manual rebuild.\n"
    "- **Streaming hardening and server-side stop (DD2).** Not built on main. This "
    "covers SSE heartbeats, a Stop button that cancels generation on the server, and "
    "a client stall watchdog.\n"
    "- **Nightly ingestion CronJob.** Not built. Ingestion runs once per release, "
    "after the rollout.\n"
    "- **Pruning deleted files from the index.** Not built. The ingest Job does not "
    "remove documents whose files were deleted from the repository.\n"
    "- **Metrics and tracing.** Not built. There is no Prometheus `/metrics` "
    "endpoint, no Grafana Cloud export, no OpenTelemetry tracing, no CloudWatch "
    "status-check alarm and no external uptime ping. Logs are plain text rather than "
    "structured JSON.\n"
    "- **Retrieval evaluation in CI.** Not built. The recall@5 and MRR harness runs "
    "manually and does not gate pull requests.\n"
    "- **Citation deep links.** Not built. Sources show a title, path and score but "
    "do not link to the file and line range on GitHub at the deployed commit.\n"
    "- **Polish (DD1 Phase 7).** Not started: recorded load-test numbers, footer "
    "statistics beyond the last request, and README screenshots.\n"
    "- **Stronger edge and network posture.** Not built. This covers a trusted "
    'origin certificate for Cloudflare "Full (strict)" mode, a free S3 gateway VPC '
    "endpoint, private subnets with VPC endpoints for Bedrock and SSM, Cloudflare "
    "WAF rules, and the External Secrets Operator.\n"
    "- **Stretch ideas.** An EKS variant to prove manifest portability, a multi-node "
    'or managed control plane, and a "live facts" tool that answers "what version is '
    'deployed right now?" from the cluster.\n'
    "\n"
)


def _is_marked(marked_text: str, needle: str) -> bool:
    """True when the heading, list item, or sentence containing needle carries the mark."""
    line = next(line for line in marked_text.split("\n") if needle in line)
    stripped = line.lstrip()
    if stripped.startswith(PLANNED_MARK) and _UNIT_LINE.match(stripped[len(PLANNED_MARK) + 1 :]):
        return True
    before = line[: line.index(needle)]
    mark = before.rfind(PLANNED_MARK)
    return mark >= 0 and not re.search(r"[.!?]\s", before[mark + len(PLANNED_MARK) :])


def _chunk_containing(path: str, needle: str) -> str:
    source = REPO / path
    if not source.exists():
        pytest.skip(f"{path} not present")
    for chunk in chunk_markdown(source.read_text(), path):
        if needle in chunk.text:
            return chunk.text
    pytest.skip(f"{path} no longer contains {needle!r}; update this case")


# (path, text inside a unit that describes something live today)
LIVE_UNITS = [
    ("docs/DESIGN.md", "- MySQL 8.x, `StatefulSet` + PVC on the node's `local-path` storage class"),
    ("docs/DESIGN.md", "**Embeddings:** Amazon Titan Text Embeddings V2"),
    ("docs/DESIGN.md", "**System prompt rules:**"),
    ("docs/DESIGN.md", "### 9.3 Queue-driven autoscaling (KEDA)"),
    ("docs/DESIGN.md", "| KEDA | Scales workers on Redis Stream backlog"),
    ("docs/DESIGN.md", "| Kubernetes (k3s) | Runs API, workers, Redis, ingestion"),
    ("docs/DESIGN-004-action-plan.md", "## 6. Milestone 2: Live on AWS"),
    ("docs/DESIGN-004-action-plan.md", "| 5 (done) | KEDA, synthetic load endpoint"),
    ("docs/DESIGN-004-action-plan.md", "| M2 |"),
    # DESIGN-005: the current-state pipeline map must stay unmarked.
    ("docs/DESIGN-005-rag-quality.md", "| Retrieval | KNN **top 8**"),
    ("docs/DESIGN-005-rag-quality.md", "| Prompt | Numbered sources"),
    ("docs/DESIGN-005-rag-quality.md", "Section 2 describes the system as it runs today."),
    # DESIGN-005 §3.4/§3.5 headings are neutral: their already-built facts stay unmarked.
    ("docs/DESIGN-005-rag-quality.md", "Already filters by corpus and model."),
    ("docs/DESIGN-005-rag-quality.md", "### 3.4 Query rewriting"),
    ("docs/DESIGN-005-rag-quality.md", "| Index migration | `ensure_index` checks only"),
    ("docs/DESIGN-004-action-plan.md", "| M3 (shipped part) | DD2 | Conversational memory"),
    (
        "docs/DESIGN-004-action-plan.md",
        "Status: the baseline is live, and DD2's conversational chat",
    ),
    ("docs/DESIGN-004-action-plan.md", "**Conversational chat + live chat UX (DD2 §5, §6)**"),
    ("docs/DESIGN-002-followups.md", "## 5. Feature 3: Conversational chat"),
    ("project/SNAPSHOT.md", "Phases 4–6 done and live (Terraform, k3s, CI/CD"),
    # 2026-10-01 drift pass: live infra and shipped features stay unmarked, including
    # sentences that say KEDA is suspended (installed, just off) and zram is on.
    ("docs/DESIGN.md", "// event: reconnect"),
    ("docs/DESIGN.md", "KEDA is installed but suspended since the 2026-09-30 memory incident"),
    ("docs/DESIGN.md", "Live status: KEDA 2.21 is installed through Flux"),
    ("docs/DESIGN.md", "| `keda` | KEDA operator + metrics server |"),
    ("docs/DESIGN.md", "#### Swap: zram first, `/swapfile` as overflow"),
    ("docs/DESIGN.md", "- **Retrieval eval (run by hand):**"),
    ("docs/DESIGN.md", "- On every pull request and push to `main` (`ci.yml`"),
    ("docs/DESIGN.md", "**Operations runbooks.** Node operations are push-button too"),
    ("docs/DESIGN.md", "- **Logs:** plain-text application logs"),
    ("docs/DESIGN.md", "- **Alerts:** AWS Budgets (cost)."),
    ("docs/DESIGN.md", "- AWS Budgets: `Glassbox-Monthly`"),
    ("docs/DESIGN.md", "Phases 0 to 6 are done and live."),
    ("docs/DESIGN.md", "Built: the answer is plain prose without citation markers"),
    ("docs/DESIGN-002-followups.md", "Heartbeats and server-side Stop are live."),
    ("docs/DESIGN-002-followups.md", "- **Up-arrow recall.**"),
    ("docs/DESIGN-002-followups.md", "### 6.8 Playful daily-budget replies"),
    ("docs/DESIGN-002-followups.md", "- Built: the Cloudflare Cache Rule for `/api/*`"),
    ("docs/DESIGN-003-ingestion.md", "## 1.1 What runs today: the current ingest Job"),
    ("docs/DESIGN-003-ingestion.md", "- **On by default (report only):**"),
    ("docs/DESIGN-003-ingestion.md", "## 1.2 Private About Basel repo"),
    ("docs/DESIGN-003-ingestion.md", "- **The public copies during the switch.**"),
    ("docs/DESIGN-003-ingestion.md", "- **No GitHub Actions cache for private builds.**"),
    ("docs/DESIGN-004-action-plan.md", "Milestones 0 to 2 are done and live"),
    ("docs/DESIGN-004-action-plan.md", "| 6 (done) | GitHub Actions building images to Amazon ECR"),
    ("docs/DESIGN-005-rag-quality.md", "| Stale files | After a full scan"),
    ("docs/DESIGN-005-rag-quality.md", "| Eval | Retrieval (`run_eval.py`, PR #95)"),
    ("docs/DESIGN-005-rag-quality.md", "### 5.1 Golden dataset v2 (`eval/golden.yaml`), built"),
    ("docs/DESIGN-005-rag-quality.md", "| `live` | ~5 |"),
    ("docs/architecture/deep-dive.md", "A **Retry** button under the latest failure reply"),
    ("docs/architecture/deep-dive.md", "## KEDA status: suspended since the memory incident"),
    # Review round 1 of #120: live facts split out of planned rows and sections.
    ("docs/DESIGN.md", "Idempotent. It ends with the answer warm-up."),
    ("docs/DESIGN-002-followups.md", "Note for section 3, as of 2026-10-01"),
    ("docs/DESIGN-002-followups.md", "Built: files may start with a small YAML block"),
    ("docs/DESIGN-002-followups.md", "- **Multiple tabs.**"),
    ("docs/DESIGN-005-rag-quality.md", "| planned-category cases |"),
    ("docs/DESIGN-005-rag-quality.md", "Built: unit tests for the graders"),
    ("docs/DESIGN-005-rag-quality.md", "**Ingested:** this document is part of About This System"),
    ("docs/architecture/deep-dive.md", "Phones are portrait-only"),
    ("docs/architecture/deep-dive.md", "## Compressed swap (zram) on the node"),
    ("docs/architecture/deep-dive.md", "## Operations: push-button runbooks"),
    ("docs/architecture/deep-dive.md", "## Stale documents: report-only sweep"),
    # The self-healing plan's "what is lost today" section states live facts.
    (
        "docs/superpowers/plans/2026-10-01-self-healing-node.md",
        "There is no second volume, no snapshot policy and no backup",
    ),
    # DD2 §3a: the node as it runs today, outside the planned Feature 1 headings.
    ("docs/DESIGN-002-followups.md", "## 3a. Where the node's state lives today"),
    (
        "docs/DESIGN-002-followups.md",
        "MySQL runs in-cluster as a StatefulSet on a `local-path` volume",
    ),
    ("docs/DESIGN-002-followups.md", "- Flux's Git credential, which exists only as a Secret"),
    (
        "docs/superpowers/plans/2026-10-01-self-healing-node.md",
        "DD1 §6.5 describes a `reindex` Job",
    ),
]

# (path, text inside a unit that describes work not built yet)
PLANNED_UNITS = [
    ("docs/DESIGN.md", "## 20. Stretch ideas"),
    ("docs/DESIGN-004-action-plan.md", "| M3 (not built yet) | DD2 | Self-healing (ASG)"),
    ("docs/DESIGN-004-action-plan.md", "DD2's self-healing node and the DD3 Google Drive"),
    ("docs/DESIGN-004-action-plan.md", "**Streaming hardening formalized (DD2 §7)**"),
    ("docs/DESIGN-004-action-plan.md", "**Corpus authoring guide + validation (DD2 §4)**"),
    # Heading marks cover their section: bullets under DESIGN.md's stretch ideas.
    ("docs/DESIGN.md", "- **EKS for an afternoon:**"),
    ("docs/DESIGN.md", "- **Live facts tool:**"),
    ("docs/DESIGN.md", "- **Hybrid search:**"),
    # DESIGN-005 and its plan describe unbuilt RAG work (hybrid retrieval, etc.).
    ("docs/DESIGN-005-rag-quality.md", "### 3.2 Hybrid search (planned choice, not built yet)"),
    ("docs/DESIGN-005-rag-quality.md", "4. **Hybrid retrieval** in `retrieval/search.py`"),
    ("docs/DESIGN-005-rag-quality.md", "6. **Answer log**"),
    ("docs/DESIGN-005-rag-quality.md", "plus a per-document cap (at most 2 or 3 chunks"),
    ("docs/DESIGN-005-rag-quality.md", "6. **Answer logging:**"),
    (
        "docs/superpowers/plans/2026-10-01-rag-quality.md",
        "**Files:** `ingest/redis_index.py` (`text` TEXT field",
    ),
    # DD2's self-healing proposal, including a chunk that starts mid-feature.
    ("docs/DESIGN-002-followups.md", "### 3.3 Boot sequence (user data)"),
    ("docs/DESIGN-002-followups.md", "### 3.4 Health detection"),
    ("docs/DESIGN-002-followups.md", "| `ec2:AssociateAddress` |"),
    ("docs/DESIGN-002-followups.md", "2. Terminate the instance from the console."),
    ("docs/DESIGN-004-action-plan.md", "**Self-healing (DD2 §3)**"),
    # The revised self-healing plan (DD2 §3.9 and its plan document).
    ("docs/DESIGN-002-followups.md", "### 3.9 Revised plan: backups first"),
    ("docs/DESIGN-002-followups.md", "- **Phase 2:** nightly `mysqldump`"),
    ("docs/DESIGN-002-followups.md", "- **Phase 4:** a launch template"),
    (
        "docs/superpowers/plans/2026-10-01-self-healing-node.md",
        "### Phase 4a (planned): launch templates",
    ),
    ("docs/superpowers/plans/2026-10-01-self-healing-node.md", "**F1, no write key on the node.**"),
    (
        "docs/superpowers/plans/2026-10-01-self-healing-node.md",
        "1. **Owner go-ahead** for a quiet window.",
    ),
    ("docs/superpowers/plans/2026-10-01-self-healing-node.md", "## 2. Design options"),
    (
        "docs/superpowers/plans/2026-10-01-self-healing-node.md",
        "### Phase 1 (planned): recovery alarms",
    ),
    (
        "docs/superpowers/plans/2026-10-01-self-healing-node.md",
        "- `infra/modules/backup/` (new module",
    ),
    (
        "docs/superpowers/plans/2026-10-01-self-healing-node.md",
        "### Phase 4b (planned): cutover",
    ),
    ("docs/DESIGN-004-action-plan.md", "## 8. Milestone 4: DD3 production ingestion pipeline"),
    ("docs/DESIGN-004-action-plan.md", "| M4 | DD3 |"),
    ("project/SNAPSHOT.md", "| M4 — DD3: production ingestion pipeline"),
    ("project/SNAPSHOT.md", "Phase 7 polish (load-test numbers, README screenshots) not started."),
    # 2026-10-01 drift pass: unbuilt parts of the design docs carry their own marker.
    ("docs/DESIGN.md", "Not built yet: citation chips that open a popover"),
    ("docs/DESIGN.md", "A static fallback card with resume and GitHub links"),
    ("docs/DESIGN.md", "- Not built yet: `GET /api/stats`"),
    ("docs/DESIGN.md", "- Not built yet: a light rerank"),
    ("docs/DESIGN.md", "- Not built yet: a nightly ingest CronJob."),
    ("docs/DESIGN.md", "A nightly CronJob is not built yet"),
    ("docs/DESIGN.md", "- **Metrics (not built yet):**"),
    ("docs/DESIGN.md", "- Not built yet: a CloudWatch alarm"),
    ("docs/DESIGN.md", "- Not built yet: a free, lexical-only variant"),
    ("docs/DESIGN.md", "Not built yet: `tflint` and a misconfiguration scanner."),
    ("docs/DESIGN.md", "**Phase 7: Polish (not started)**"),
    ("docs/DESIGN.md", "- Not started: README with screenshots/GIF"),
    ("docs/DESIGN.md", "Phase 7 is not started."),
    ("docs/DESIGN-002-followups.md", "Plus one small network change"),
    ("docs/DESIGN-002-followups.md", "Not built yet: reading its values for filtering"),
    ("docs/DESIGN.md", "A Cloudflare Worker injecting a secret header"),
    ("docs/DESIGN.md", "External Secrets Operator to sync automatically"),
    ("docs/DESIGN-005-rag-quality.md", "Planned: a **lexical-only retrieval eval**"),
    ("docs/DESIGN-005-rag-quality.md", "| **Online (weekly, manual; planned)** |"),
    ("docs/DESIGN-002-followups.md", "- **Scripted check:**"),
    ("docs/DESIGN-002-followups.md", "## 8. Network: S3 gateway endpoint (not built yet)"),
    ("docs/DESIGN-002-followups.md", "ALTER TABLE documents ADD COLUMN metadata JSON NULL;"),
    ("docs/DESIGN-002-followups.md", "ADD COLUMN ttft_ms"),
    ("docs/DESIGN-002-followups.md", "The scripted post-deploy streaming check"),
    ("docs/DESIGN-003-ingestion.md", "### 4.2 Why a raw zone at all (planned)"),
    ("docs/DESIGN-003-ingestion.md", "### 8.2 Processing one message (planned)"),
    ("docs/DESIGN-003-ingestion.md", "## 5. Google Drive connector (dropped"),
    ("docs/DESIGN-004-action-plan.md", "| 7 (not started) |"),
    ("docs/DESIGN-004-action-plan.md", "post-deploy Cloudflare streaming check is not built"),
    ("docs/DESIGN-005-rag-quality.md", "| Retrieval, lexical leg (planned with hybrid search) |"),
    ("docs/DESIGN-005-rag-quality.md", "| Answer: faithfulness (planned) |"),
    ("docs/DESIGN-005-rag-quality.md", "### 5.3 Judge design (planned)"),
    ("docs/architecture/deep-dive.md", "**Streaming checks (DD2).**"),
    ("docs/architecture/deep-dive.md", "**Deleting stale documents automatically.**"),
]


@pytest.mark.parametrize(("path", "needle"), LIVE_UNITS)
def test_live_units_in_real_chunks_are_not_marked(path, needle):
    marked = _mark_planned(_chunk_containing(path, needle))
    assert not _is_marked(marked, needle)


@pytest.mark.parametrize(("path", "needle"), PLANNED_UNITS)
def test_planned_units_in_real_chunks_are_marked(path, needle):
    marked = _mark_planned(_chunk_containing(path, needle))
    assert _is_marked(marked, needle)


def test_a_chunk_mixing_live_and_planned_marks_only_the_planned_parts():
    # DESIGN-004's overview chunk has both the live M2 row and the planned M3/M4 rows.
    marked = _mark_planned(_chunk_containing("docs/DESIGN-004-action-plan.md", "| M4 | DD3 |"))
    assert _is_marked(marked, "| M4 | DD3 |")
    assert not _is_marked(marked, "| M2 |")


def _real_chunks(path: str) -> list[str]:
    source = REPO / path
    if not source.exists():
        pytest.skip(f"{path} not present")
    return [chunk.text for chunk in chunk_markdown(source.read_text(), path)]


def _rendered_source(path: str, text: str) -> str:
    prompt = _prompt(
        "Is this built now?",
        [WorkerChunk(n=1, chunk_id=1, text=text, source_path=path, title="t", score=0.8)],
    )
    line_start = prompt.index(f"[1] {path}")
    return prompt[line_start : prompt.index("\n\nQuestion:", line_start)]


def test_every_design_003_chunk_is_marked_in_the_prompt_except_what_runs_today():
    # DD3 is unbuilt M4 design except sections 1.1 (the running ingest Job and
    # stale sweep) and 1.2/1.2.1 (the private About Basel repo, live since the owner
    # added the deploy key). Since prompt v14 DD3 has no fixed whole-document label:
    # every other heading carries the planned wording, so each real chunk is marked
    # in the rendered prompt (including chunks that start at a ### heading), while
    # no line of 1.1, 1.2 or 1.2.1 is.
    path = "docs/DESIGN-003-ingestion.md"
    source = (REPO / path).read_text()
    live = source[source.index("## 1.1 What runs today") : source.index("## 2. Goals")]
    # "---" separators also close planned sections, so they say nothing here.
    live_lines = {line for line in live.split("\n") if line.strip() and line != "---"}
    chunks = _real_chunks(path)
    assert len(chunks) == 14
    for text in chunks:
        rendered = _rendered_source(path, text)
        assert "PLANNED M4 DESIGN" not in rendered
        for line in rendered.split("\n"):
            if line.startswith(PLANNED_MARK):
                assert line.removeprefix(PLANNED_MARK + " ") not in live_lines, line[:80]
        if not text.startswith(("## 1.1 What runs today", "## 1.2 Private About Basel")):
            assert PLANNED_MARK in rendered, text[:80]


def test_real_deep_dive_marks_only_its_planned_section():
    # Live infra (KEDA installed but suspended, zram, k3s, Flux, ops runbooks) must
    # never carry the marker; the one planned section must be marked throughout.
    chunks = _real_chunks("docs/architecture/deep-dive.md")
    planned = [text for text in chunks if text.startswith("## Planned / not built yet")]
    assert len(planned) == 1
    for text in chunks:
        marked = _mark_planned(text)
        if text in planned:
            lines = [line for line in marked.split("\n") if line.strip()]
            assert all(line.startswith(PLANNED_MARK) for line in lines)
        else:
            assert PLANNED_MARK not in marked, text[:80]


@pytest.mark.parametrize(
    "shipped",
    ["Up-arrow", "Retry", "Landscape phone layout", "Pruning deleted files"],
)
def test_deep_dive_planned_section_lists_no_shipped_feature(shipped):
    # Settled on 2026-10-01: stop-and-send, Up-arrow and Retry (#93), landscape phones
    # (#100, then superseded by portrait-only #119) and the report-only stale sweep
    # (#101). Listing them as planned made the bot deny live features.
    (planned,) = (
        text
        for text in _real_chunks("docs/architecture/deep-dive.md")
        if text.startswith("## Planned / not built yet")
    )
    assert shipped not in planned


def _deep_dive_chunks(section: str) -> list[str]:
    return [chunk.text for chunk in chunk_markdown(section, "docs/architecture/deep-dive.md")]


@pytest.mark.parametrize(
    "section", [DEEP_DIVE_GROUNDING, DEEP_DIVE_KEDA, DEEP_DIVE_KUBERNETES, DEEP_DIVE_FLUX]
)
def test_deep_dive_live_sections_are_not_marked(section):
    for chunk in _deep_dive_chunks(section):
        assert PLANNED_MARK not in _mark_planned(chunk)


@pytest.mark.parametrize(
    "needle",
    [
        "## Planned / not built yet",
        "Everything in this section is planned or proposed design.",
        "**Google Drive and S3/SQS content pipeline (Milestone 4,",
        "**Self-healing node recovery (Milestone 3, DD2).**",
        "**Nightly ingestion CronJob.**",
        "**Stretch ideas.**",
    ],
)
def test_deep_dive_planned_section_is_marked(needle):
    (chunk,) = _deep_dive_chunks(DEEP_DIVE_PLANNED)
    assert _is_marked(_mark_planned(chunk), needle)


def test_deep_dive_keda_sentence_in_prompt_is_unmarked():
    prompt = _prompt(
        "Does KEDA scale the workers today?",
        [
            WorkerChunk(
                n=1,
                chunk_id=1,
                text=DEEP_DIVE_KEDA,
                source_path="docs/architecture/deep-dive.md",
                title="Deep dive",
                score=0.9,
            )
        ],
    )
    assert f"[1] docs/architecture/deep-dive.md: {DEEP_DIVE_KEDA}" in prompt


@pytest.mark.parametrize(
    "text",
    [
        "The site runs on k3s on a single EC2 node provisioned by Terraform.",
        "Flux GitOps image automation deploys each build; CI/CD runs in GitHub Actions.",
        "**Phase 5: Autoscaling demo**\nKEDA scales workers.",
        "The prompt distinguishes current vs. planned/future functionality.",
        "A future format change can use a versioned cache key.",
    ],
)
def test_live_or_meta_text_is_not_marked(text):
    assert PLANNED_MARK not in _mark_planned(text)


@pytest.mark.parametrize(
    ("text", "needle"),
    [
        ("Self-healing replaces the instance with an Auto Scaling Group of one.", "Self"),
        ("A launch template and a size-1 ASG rebuild the node.", "A launch"),
        ("- Google Drive connector (M4, not built).", "Google Drive"),
        ("An S3 raw zone feeds SQS events.", "An S3"),
        ("## Future milestones (not started)", "Future"),
    ],
)
def test_planned_text_is_marked(text, needle):
    assert _is_marked(_mark_planned(text), needle)


def test_paragraph_marks_only_the_planned_sentence():
    text = (
        "KEDA scales the retrieval-worker from 1 to 3 on queue lag. "
        "Self-healing with an Auto Scaling Group is planned for Milestone 3."
    )
    marked = _mark_planned(text)
    assert not _is_marked(marked, "KEDA scales")
    assert _is_marked(marked, "Self-healing")


@pytest.mark.parametrize(
    "source_path",
    [
        "services/glassbox/api/ask.py",
        "k8s/overlays/prod/flux/kustomization-keda.yaml",
        "infra/modules/compute/main.tf",
    ],
)
def test_code_manifests_and_infra_are_never_marked(source_path):
    text = "# Deferred: an Auto Scaling Group is planned; KEDA and Flux run now."
    prompt = _prompt(
        "Is this built now?",
        [WorkerChunk(n=1, chunk_id=1, text=text, source_path=source_path, title="c", score=0.8)],
    )
    assert f"[1] {source_path}: {text}" in prompt


def test_design_003_is_marked_unit_by_unit_without_a_document_label():
    text = "## 5. Google Drive connector (planned)\nDrive sync runs every 15 minutes."
    prompt = _prompt(
        "Does Drive ingestion work now?",
        [
            WorkerChunk(
                n=1,
                chunk_id=1,
                text=text,
                source_path="docs/DESIGN-003-ingestion.md",
                title="Design",
                score=0.8,
            )
        ],
    )
    assert "PLANNED M4 DESIGN" not in prompt
    assert f"{PLANNED_MARK} Drive sync runs every 15 minutes." in prompt
    assert f"Text prefixed {PLANNED_MARK} describes work that does not exist today" in prompt
    assert "also appears in code, manifest, or infrastructure sources" in prompt
    assert 'reply with exactly "I don\'t know from what I have." and nothing else' in prompt
    assert "Do not include bracketed citation markers" in prompt


def test_marked_heading_covers_its_section_until_a_sibling_heading():
    text = (
        "## Planned work\n"
        "Workers restart on their own.\n"
        "- Hybrid search\n"
        "### Detail\n"
        "More detail.\n"
        "## Live today\n"
        "KEDA scales the workers.\n"
    )
    marked = _mark_planned(text)
    for needle in ("## Planned work", "Workers restart", "- Hybrid search", "### Detail", "More"):
        assert _is_marked(marked, needle), needle
    assert not _is_marked(marked, "## Live today")
    assert not _is_marked(marked, "KEDA scales")
