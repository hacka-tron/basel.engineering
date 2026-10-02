"""The planned-work marker lands on planned text only, checked on real chunks.

Chunks come from the real Markdown chunker over the repo's design docs,
including docs/architecture/deep-dive.md (read from the real file, so the tests
follow its current text).
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
    # DD2 TTFT logging is live (migration 0005); only the scripted check is planned.
    ("docs/DESIGN-002-followups.md", "ADD COLUMN ttft_ms"),
    ("docs/DESIGN-002-followups.md", "- **Metric:** time to first token (TTFT)"),
    ("docs/DESIGN-002-followups.md", "### 7.7 Implementation notes (TTFT logging)"),
    ("docs/DESIGN-002-followups.md", "**Answer-cache hits are measured too.**"),
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
    ("docs/DESIGN.md", "- **Alerts:** AWS Budgets (cost); two CloudWatch status-check alarms"),
    ("docs/DESIGN.md", "- **Backups:** daily snapshots of the node's root volume"),
    ("docs/DESIGN.md", "- AWS Budgets: `Glassbox-Monthly`"),
    ("docs/DESIGN.md", "Phases 0 to 6 are done and live."),
    ("docs/DESIGN.md", "Built: the answer is plain prose without citation markers"),
    (
        "docs/DESIGN-002-followups.md",
        "Heartbeats, server-side Stop and server-side time-to-first-token logging",
    ),
    ("docs/DESIGN-002-followups.md", "- **Up-arrow recall.**"),
    ("docs/DESIGN-002-followups.md", "### 6.8 Playful daily-budget replies"),
    ("docs/DESIGN-002-followups.md", "- Built: the Cloudflare Cache Rule for `/api/*`"),
    ("docs/DESIGN-003-ingestion.md", "## 1.1 What runs today: the current ingest Job"),
    ("docs/DESIGN-003-ingestion.md", "- **On by default (report only):**"),
    ("docs/DESIGN-003-ingestion.md", "## 1.2 Private About Basel repo"),
    ("docs/DESIGN-003-ingestion.md", "- **The public copies are gone.**"),
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
        "There is no second volume. When this plan was scoped there was no snapshot",
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
    # 2026-10-01 owner decision: alarms, daily snapshots and the restore runbook are
    # merged (live once applied), so they must not read as planned work.
    ("docs/DESIGN-002-followups.md", "## 3b. Alarms, uptime probe and daily snapshots"),
    ("docs/DESIGN-002-followups.md", "- **Daily snapshots of the whole disk.**"),
    ("docs/DESIGN-002-followups.md", "- **One-click restore.**"),
    ("docs/DESIGN-002-followups.md", "Section 3b adds alarms, daily snapshots"),
    (
        "docs/superpowers/plans/2026-10-01-self-healing-node.md",
        "- **Daily drive snapshots.** A Data Lifecycle Manager policy",
    ),
    (
        "docs/superpowers/plans/2026-10-01-self-healing-node.md",
        '- **One-click restore.** "Ops · Restore from snapshot"',
    ),
    ("docs/architecture/deep-dive.md", "## Alarms, uptime probe and daily snapshots"),
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
        "**Phases 2, 3, 4 and 5 below are deferred**",
    ),
    (
        "docs/superpowers/plans/2026-10-01-self-healing-node.md",
        "### Phase 4a (deferred 2026-10-01, planned): launch templates",
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
        "### Phase 4b (deferred 2026-10-01, planned): cutover",
    ),
    ("docs/DESIGN-004-action-plan.md", "## 8. Milestone 4: DD3 production ingestion pipeline"),
    ("docs/DESIGN-004-action-plan.md", "| M4 | DD3 |"),
    ("project/SNAPSHOT.md", "| M4 — DD3: production ingestion pipeline"),
    (
        "project/SNAPSHOT.md",
        "footer stats and load-test numbers not started.",
    ),
    # 2026-10-01 drift pass: unbuilt parts of the design docs carry their own marker.
    ("docs/DESIGN.md", "Not built yet: citation chips that open a popover"),
    ("docs/DESIGN.md", "A static fallback card with resume and GitHub links"),
    ("docs/DESIGN.md", "- Not built yet: `GET /api/stats`"),
    ("docs/DESIGN.md", "- Not built yet: a light rerank"),
    ("docs/DESIGN.md", "- Not built yet: a nightly ingest CronJob."),
    ("docs/DESIGN.md", "A nightly CronJob is not built yet"),
    ("docs/DESIGN.md", "- **Metrics (not built yet):**"),
    ("docs/DESIGN.md", "- Not built yet: a free, lexical-only variant"),
    ("docs/DESIGN.md", "Not built yet: `tflint` and a misconfiguration scanner."),
    ("docs/DESIGN.md", "Not started: server-side footer stats"),
    ("docs/DESIGN.md", "footer stats and load test numbers are not started."),
    ("docs/DESIGN-002-followups.md", "Plus one small network change"),
    ("docs/DESIGN-002-followups.md", "Not built yet: reading its values for filtering"),
    ("docs/DESIGN.md", "A Cloudflare Worker injecting a secret header"),
    ("docs/DESIGN.md", "External Secrets Operator to sync automatically"),
    ("docs/DESIGN-005-rag-quality.md", "Planned: a **lexical-only retrieval eval**"),
    ("docs/DESIGN-005-rag-quality.md", "| **Online (weekly, manual; planned)** |"),
    ("docs/DESIGN-002-followups.md", "- **Scripted check (not built yet):**"),
    ("docs/DESIGN-002-followups.md", "- Running the scripted check in CI after every deploy"),
    ("docs/DESIGN-002-followups.md", "## 8. Network: S3 gateway endpoint (not built yet)"),
    ("docs/DESIGN-002-followups.md", "ALTER TABLE documents ADD COLUMN metadata JSON NULL;"),
    ("docs/DESIGN-002-followups.md", "The scripted post-deploy streaming check"),
    ("docs/DESIGN-003-ingestion.md", "### 4.2 Why a raw zone at all (planned)"),
    ("docs/DESIGN-003-ingestion.md", "### 8.2 Processing one message (planned)"),
    ("docs/DESIGN-003-ingestion.md", "## 5. Google Drive connector (dropped"),
    ("docs/DESIGN-004-action-plan.md", "load test numbers recorded are not started"),
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


def test_deep_dive_keda_sentence_in_prompt_is_unmarked():
    # The real KEDA section of the deep dive, rendered through the prompt builder.
    keda = _chunk_containing(
        "docs/architecture/deep-dive.md", "## Stress test and KEDA autoscaling"
    )
    prompt = _prompt(
        "Does KEDA scale the workers today?",
        [
            WorkerChunk(
                n=1,
                chunk_id=1,
                text=keda,
                source_path="docs/architecture/deep-dive.md",
                title="Deep dive",
                score=0.9,
            )
        ],
    )
    assert f"[1] docs/architecture/deep-dive.md: {keda}" in prompt


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
