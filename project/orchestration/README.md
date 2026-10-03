# Multi-model orchestration

How work is split between models on this project, how every change passes the review gate, and how PRs get merged. Claude (the orchestrator) fills in templates here instead of re-deriving boilerplate. Generic version of this guide: `~/Coding/template/agent-orchestration.md`.

- `reviewer-brief.md` — the review-gate dispatch template (Opus subagent by default, Codex optional) and the Codex CLI form.
- `rag-plan-brief.md` — shared context for every RAG-plan agent (owner decisions, environment, rules, gotchas); point dispatches at it instead of restating.
- `reviewer-primer.md` — what every reviewer reads first (system map, hard rules, known traps, per-area checklist).
- Legacy, kept for history: `project/archive/CODEX.md` (Codex as implementer) and `project/archive/gemini-reviewer.md` (Gemini via `agy`).

## Roles (as of 2026-10-02)

- **Claude Opus: orchestrator.** Owns the plan, the specs, judgment calls, reading reports, the merge sequence and talking to the owner. Only Claude (the orchestrator or its subagents) commits, pushes and merges. It chooses per task who does the hands-on work:
  - **Opus itself:** small or judgment-heavy changes where writing the spec would take longer than the change.
  - **Opus subagent** (`Agent`, `model: "opus"`): larger features with design or correctness risk (cache semantics, concurrency, security, cost controls), scoping that needs design judgment, and **reviews**.
  - **Sonnet subagent** (`model: "sonnet"`): well-specified implementation, dev-server and browser verification loops, test/lint/build legwork, surveys for a scoping brief, status-report writing.
  - **Haiku subagent** (`model: "haiku"`): mechanical low-risk edits and searches.
- **Reviewer (the gate):** an **Opus subagent** with `reviewer-brief.md` by default. **Codex** (`gpt-6-sol`) is optional, used only when it has usage, with the same brief. Gemini is legacy. Never skip review because a reviewer is out of quota; switch to the other one. Reviewers validate hands-on but never edit, commit, push, merge or touch live infrastructure.
- **The owner:** decisions only the owner can make (BACKLOG "Open decisions"), approval clicks on the Bootstrap, Terraform and "Ops · ..." workflows, and go-aheads for live-effect merges (below). The owner never runs AWS or Terraform by hand.

## Working rules

- **Parallelize by default.** Independent streams each get a branch in `.worktrees/<name>` and their own subagent, dispatched in the same turn. Give each stream distinct ports (API 8000/8001, Vite 5173/5174) and tell it not to stop or recreate the shared `docker compose` stack. About 3 concurrent reviews or heavy builds at most; agents delete the `node_modules`, `dist` and `.terraform` they create.
- **Delegate scoping and research to subagents** (Sonnet for surveys, Opus when it needs design judgment) and ask for a compact brief: file:line pointers, the spec or plan, owner questions, risks. Don't re-read what the brief summarized.
- **Keep a feature pipeline moving.** When current work waits on review or the owner, dispatch scoping for the next `> RESUME HERE` item.
- **Every dispatch restates the hard rules** (implementer and reviewer, every time; subagents don't inherit standing instructions):
  - never read, open or copy any `terraform.tfstate`, `*.tfstate.backup`, `*.tfvars` or plan file;
  - no `terraform apply`, no AWS/SSM/`kubectl` writes against the live system, no GitHub environment or secret changes;
  - never run `git stash`.
  Live changes happen only through the Terraform, Bootstrap and "Ops · ..." workflows after the owner's approval click; give the owner the click, never commands.
- **Unique scratch file names** (`pr<n>-body.md`, `pr<n>-review-r1-prompt.md`): parallel agents share the scratchpad.
- **Stacked and chained PRs.** Never rebase a pushed branch; merge `main` into it. Cut dependent work from the branch it builds on and open its PR against that branch. Chain PRs that touch the same files and state the merge order in each PR body. Trial-merge parallel approved PRs (`git merge-tree --write-tree <a> <b>`, or a scratch merge plus tests) and look for semantic conflicts git won't flag. Retarget a stacked PR (REST, below) before deleting its base branch; deleting the base closes the PR.

## What "checked in" means

1. Implementation and tests committed on a feature branch in its worktree.
2. The reviewer returns **APPROVED** (or CHANGES NEEDED, fixed, re-reviewed, APPROVED, within the round cap).
3. Real end-to-end verification by the orchestrator or a verification subagent (review-by-reading misses integration bugs).
4. PR open, with the reviewer, round and verdict recorded in the PR body, and for a substantial change a status report in `project/status/` (written when the PR opens, updated at merge and at deploy; format in `project/status/README.md`).
5. The merge sequence (below) completed.
6. Anything that changes the live cluster or AWS on merge beyond a normal release (a new Flux component or Kustomization, ConfigMap/infra applied by GitOps) or grants permissions (RBAC, IAM, trust policies) needs the owner's explicit go-ahead before merge.

**Round cap: 2.** Fix every Critical/Important finding and re-run the reviewer with the previous result file and the list of fixes. After round 2, only Critical findings, or Important findings with a reachable failure scenario, block the merge; everything else goes to `project/BACKLOG.md` (tell the owner). Disputed blocking findings go to the owner. Track each open PR's review state (round, verdict, fixes since, re-review pending) so nothing merges unreviewed and no fix sits unreviewed. Copy-only or one-class changes can be reviewed by the orchestrator from the diff plus a screenshot; say so in the PR.

## Merging

Agents merge **without asking** once the review is APPROVED and CI is green (owner, reconfirmed 2026-10-02), except step 6 changes.

1. **Record the review in the PR body** through REST: `gh api -X PATCH repos/hacka-tron/basel.engineering/pulls/<n> -F body=@<scratch>/pr<n>-body.md`. `gh pr edit` fails on this repo (the deprecated projectCards GraphQL field errors); use REST for retargets too (`-f base=main`).
2. **Re-sync:** `gh pr update-branch <n>`, pull the new head into the worktree, and run the **full** test suite on that merged tree (`.venv/bin/python -m pytest services/tests -q`, plus frontend lint/test/build when `frontend/` changed).
3. **Wait for CI:** `gh pr checks <n> --watch --fail-fast` in the background.
4. **Merge as its own command:** `gh pr merge <n> --merge` (this repo uses merge commits), not chained with other commands. Then update the status report and the PR's dependents.

## Coordinator handoff

`project/AGENT_HANDOFF.md` holds only the current coordinator, the current state, open PRs and where to look; older checkpoints are in `project/archive/`. On a coordinator switch (or a session ending low on tokens), the outgoing agent commits, pushes and rewrites the handoff: branch/worktree/commit, tests actually run, open PRs with review state, blockers and the next action.

## History

Until 2026-09-30 Codex implemented from exact specs and Gemini reviewed; from 2026-09-30 Claude implemented and Codex was the gate; since 2026-10-01 Codex has mostly been out of usage and an Opus subagent has been the gate, which is now the default. Pilot lessons are in `project/BACKLOG.md` "Multi-model pipeline notes".
