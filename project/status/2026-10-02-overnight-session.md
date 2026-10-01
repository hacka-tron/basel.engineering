# Overnight session wrap-up (2026-10-01 → 10-02)

**Status:** 15 PRs open, all reviewed and approved, none merged. Merging is blocked for agents by the harness classifier ("Merge Without Review"), so every merge is the owner's.

## TL;DR

While the owner slept, Claude orchestrated parallel subagents in separate worktrees, and an Opus reviewer gated every PR (Codex was out of usage). The night covered:

- the two bugs the owner had filed;
- the RAG research and validation plan the owner asked for, plus its first three autonomous phases;
- the rest of the "Feature work" list (chat UX leftovers, the #78 minors, landscape phones);
- a security pass with follow-ups (exception-text leak, client-IP spoofing, salt fail-closed, stream connection cap, Actions pinned to SHAs, release-role pin);
- release-pipeline hardening.

Nothing touched production. No paid LLM or embedding calls were made, and no workflows were triggered.

## Merge plan

Merge in this order. Each PR has its own review summary in its description.

**Update 2026-10-02 (morning):**
- #88 is merged, and every open branch has `main` merged in.
- The UI group is chained (#89 → #90 → #93 → #100, each containing the one before), and so is the ops pair (#94 → #102). Merge each chain in order with no conflicts.
- **The RAG PRs (#92, #95, #97, #101) are parked:** the owner wants to add more documentation and resources before any RAG analysis.
- #96, #99, #91 and #98 stay separate and get re-synced right before each merge.

| # | PR | What | Needs from the owner |
|---|---|---|---|
| 1 | #88 | Footer drops the visible "· cached" | — |
| 2 | #89 | #78 review minors: desktop nav classes, focus handoff | — |
| 3 | #90 | Mobile Details bar locked until a component is picked | Note: answers to questions typed in Diagram view are now read in Chat only (a cue is in the backlog) |
| 4 | #93 | Chat UX: type while streaming (stop and send), Up-arrow recall, Retry | — |
| 5 | #100 | Landscape phone layout. **Merge last of the UI group**: it contains #88–#93 plus the cross-PR fixes | Design sign-off (decisions are marked *proposed* in MOBILE_DESIGN.md) |
| 6 | #92 | RAG research: `DESIGN-005` + an 11-phase plan | The decisions below |
| 7 | #95 | RAG phase 2: retrieval eval v2 (k=8, chunk-level, noise share) | — |
| 8 | #97 | RAG phase 1: 75-case golden set + free answer graders | Review the About Basel facts (flagged in `eval/golden.yaml`) |
| 9 | #101 | RAG phase 6 code: stale sweep (report mode) + `--clear` | Later: read one release's "would delete" log, then flip `GLASSBOX_INGEST_SWEEP=apply` |
| 10 | #94 | Ops `redact()` masks multiline secrets | Live on the runner at merge; on the node after a `terraform-prod` apply approval |
| 11 | #102 | Third-party Actions pinned to SHAs + Dependabot | When resolving the `release.yml` conflict with #91, keep the pinned `uses:` lines |
| 12 | #96 | Security fixes: no exception text to visitors, real client IP for rate limits, salt fail-closed | **Go-ahead** (prod configmap keys). After deploy: Ops · Diagnose (api ready, 0 restarts); 11 questions from laptop, then phone on mobile data still answers |
| 13 | #99 | Cluster stream: one shared watch per process, global and per-IP caps | **Go-ahead** (5 configmap keys, values = code defaults) |
| 14 | #91 | Release hardening: manual-run provenance, deploy-branch sync retry | **Go-ahead**, plus GitHub → Settings → Environments → `release` → Deployment branches → `main` only. Re-run its cancelled `plan` check |
| 15 | #98 | IAM: release role requires `ref = refs/heads/main` | **Go-ahead**; after merge, Actions → Bootstrap on `main`, expect `0 to add, 1 to change, 0 to destroy`, approve |

The `Terraform plan` checks on #91, #94 and #98 show as failed. All three were **cancelled** by the shared `terraform-prod-state` concurrency queue, so none of them actually failed. #98's Bootstrap plan passed. Re-run the cancelled plans before merging.

## Owner decisions collected

- **RAG (DESIGN-005 §9):**
  - Approve paid eval runs (about $0.03 each; paid runs need both `--paid` and `GLASSBOX_EVAL_ALLOW_PAID=1`).
  - Pick the judge model (Nova Pro, Claude Haiku, or offline only).
  - About 1–1.5 h of answer labelling for judge calibration (held-out test split).
  - Whether to drop `services/tests/` and plan files from the corpus (307 of 761 About This System chunks are tests).
  - Whether to ingest `frontend/src`.
  - Re-ingests for phases 6–8.
  - Whether to store answer text in `queries`.
  - An optional paid-eval CI role.
- **Corpus error:** `corpus/about-me/skills.md` says the site runs on RDS. MySQL runs in-cluster, so this needs a one-word fix. Golden case `me-site-stack` is a known failure until then.
- **Security High (from #96):** `terraform-plan` and `bootstrap-plan` run on same-repo PRs without approval. Revisit before anyone else gets write access.
- **UI:**
  - A Diagram-view "answer ready" cue (#90 follow-up).
  - Stop-and-send looks the same as Send to sighted users (#93 follow-up).

## How it was run

- Every PR had an Opus review round. Fixes went back to the implementing agent, with a second round where needed (round cap: after 2 rounds only Critical or reachable Important findings block).
- Each review verdict is recorded at the bottom of its PR description.
- Reviews caught real problems before merge, including:
  - a design doc that would have taught the chatbot that live features were "planned" (#92);
  - a token-limit change that would have failed every live answer (#92);
  - two secret-leak paths in `redact()` (#94);
  - test files counting as retrieval hits (#95);
  - a grader that passed "Yes, it's built" for planned features (#97);
  - threadpool and race bugs in the stream hub (#99);
  - a stale sweep that could delete a whole missing directory (#101);
  - semantic conflicts between parallel UI PRs (#100);
  - an outdated IAM claim, corrected against current AWS docs (#91 → #98).
- Generalizable lessons went to `~/Coding/template` (llm-apps, backend, ci-cd, agent-orchestration).

## Not done / next

- RAG phase 9 (free CI gate) needs #95 and #97 on `main`. Phases 3–5, 7, 8 and 10 need owner decisions or paid runs.
- Bring KEDA back (BACKLOG resume item 1) needs the owner's Ops approval clicks.
- Local Docker daemon was unresponsive overnight. Agents did not restart it (it would stop the shared stack). Please check it.
- After merging, update BACKLOG "> RESUME HERE". This report holds the resume point until then, because every open PR edits BACKLOG.
