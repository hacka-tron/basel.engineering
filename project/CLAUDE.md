# basel.engineering

Public production site: React/Vite and a FastAPI RAG chatbot on MySQL, Redis, Bedrock, k3s/Flux and AWS. `docs/` is chatbot corpus; `project/` is not.

## Repo layout

| Path | Purpose |
|---|---|
| `frontend/` | UI and phone preview. |
| `services/`, `eval/` | API, worker, tests and RAG evaluation. |
| `corpus/portfolio/` | Published portfolio entries. |
| `infra/`, `k8s/`, `.github/workflows/` | AWS/Terraform, Flux and CI. |
| `docs/` | Design and chatbot corpus. |
| `project/` | Instructions, guides, state, reports and history. |

## Commands and shared resources

- Python 3.12; `.venv/bin/python -m pytest services/tests -q -rs` with `GLASSBOX_TEST_MYSQL_PORT` and `GLASSBOX_TEST_REDIS_PORT` exported to **your own throwaway containers** for every pytest run. Tests use Redis DB 0 and 15. Lint: `.venv/bin/ruff check services eval`.
- `cd frontend && npm run lint && npm test && npm run build`; `npm run dev` serves the site; `npm run phone` starts phone preview. Backend: `docker compose up -d`. See `eval/README.md` for evaluation.
- Before a **full** suite, check none is running. Serialize full suites across agents; never dispatch an implementer whose suite will overlap yours. Do not rerun an identical tested tree.

## Project hard rules and owner approval

Every relevant dispatch copies the hard rules verbatim from `project/orchestration/README.md`. In particular:

- never read, open or copy any `terraform.tfstate`, `*.tfstate.backup`, `*.tfvars` or plan file;
- no `terraform apply`, no AWS/SSM/`kubectl` writes against the live system, no GitHub environment or secret changes;
- never run `git stash`;
- pytest only against your own throwaway MySQL/Redis containers, with `GLASSBOX_TEST_MYSQL_PORT` and `GLASSBOX_TEST_REDIS_PORT` exported for every run (including `-x`, `-k` and single-test runs): without them the tests default to 3306/6379, the owner's shared local compose stack (`services/tests/stack_ports.py`), and leave test rows in it.

Live changes happen only through the Terraform, Bootstrap and "Ops · ..." workflows after the owner's approval click; give the owner the click, never commands. Anything that changes the live cluster or AWS on merge beyond a normal release (a new Flux component or Kustomization, ConfigMap/infra applied by GitOps) or grants permissions (RBAC, IAM, trust policies) needs the owner's explicit go-ahead before merge. The owner never runs AWS or Terraform by hand.

RAG evaluations are approved as of 2026-10-03; `project/orchestration/rag-plan-brief.md` gives the approved small-run budget and the $1 per-run stop point. Do not start bulk paid runs without the owner's go-ahead. The About Basel files mirror the owner's resume: do not edit them to fix architecture facts; raise that with the owner. No public About Basel text may be added here.

## Review gate

Use the global `~/.claude/template/core/skills/review-gate/SKILL.md`. Reviewer primer: `project/orchestration/reviewer-primer.md`; dispatch specifics: `project/orchestration/reviewer-brief.md`. Record reviewer identity, round, verdict and validation in the PR body through GitHub REST (`gh api -X PATCH`; `gh pr edit` fails here), and in the feature's `project/status/` report. The process-doc-only exception **is enabled** only when the changed paths are exclusively `project/**` except `project/SNAPSHOT.md`, or repo-root `*.md`; moves from other directories and mixed changes do not qualify. These PRs skip test jobs through `.github/scripts/ci-code-changed.sh` and need no review round or security pass (state the exception when the merge gate asks). `docs/**` changes are chatbot corpus (external data): full review gate and security pass. `docs/**` is corpus and receives full CI.

## Merge

Variant: **PR + CI**. Required checks: `backend-tests` and `frontend-checks` on the updated PR head (the process-doc-only exception reports them skipped). For code, run the backend suite and Ruff; for frontend changes, also run frontend lint, tests and build on the merged tree if that exact tree has not passed. Update the branch with `gh pr update-branch`, wait for green CI, and merge with `gh pr merge <n> --merge` as its own command. Push rule: push feature branches and use a merge commit into `main`; do not push directly to `main` or rebase a pushed branch. Owner approval above precedes a live-effect merge.

## Status reports

One report per substantial feature in `project/status/`, named `YYYY-MM-DD-HHMM-<slug>.md` using Pacific time when first written; H1 ends `(YYYY-MM-DD HH:MM PT)`. Create at PR open, update at merge and deployment, and index it in `project/status/README.md`. Never put account IDs, IPs, tokens or private About Basel content in a report.

## Session memory

- `project/SNAPSHOT.md` — verified architecture and repository state.
- `project/BACKLOG.md` — `> RESUME HERE`, open decisions and next actions.
- `project/archive/` — older handoff checkpoints; there is no active `AGENT_HANDOFF.md`.

## Before you touch X, read Y

| Work | Project-specific guide |
|---|---|
| Architecture or a feature | Relevant section of `docs/DESIGN*.md`; update it when implementation diverges. |
| Any `frontend/` change | `project/MOBILE_DESIGN.md` (breakpoints, screenshot checklist and phone preview). |
| Portfolio content | `project/PORTFOLIO_PROJECT.md`; do not explore the portfolio code for a content request. |
| RAG plan or prompt | `project/orchestration/rag-plan-brief.md`; for prompt changes also `prompt-version-playbook.md`. |
| Private About Basel corpus resync | `project/orchestration/corpus-resync.md`. |
| Infra or operations | `project/orchestration/README.md`, `infra/CI.md`, and the relevant runbook. |

## Central instructions

Global layers load from `~/.claude/template`. This project overrides them with the PR + CI merge variant, the narrow process-doc-only review exception, the site-specific hard rules and approvals above, its status report format, and BACKLOG + SNAPSHOT handoffs without a separate handoff file. Claude owns implementation by default; Codex reviews only when it has usage, unless the owner explicitly hands it implementation.
