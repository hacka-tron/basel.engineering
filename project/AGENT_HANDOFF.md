# Agent handoff

Short and current: who is coordinating, what state things are in, what is open, and where to look. Rewrite it (don't append) at each session end or coordinator switch. Older checkpoints: `project/archive/AGENT_HANDOFF-2026-09-29-to-10-01.md`.

## Current — Claude, 2026-10-03 (RAG quality day; end of session)

**Active coordinator:** none. **Branch/worktree:** this wrap-up is `docs/wrapup-2026-10-03` (`.worktrees/wrapup-1003`); main was at 707756b (#164) when it started. **Review gate:** an Opus subagent with `project/orchestration/reviewer-brief.md`. **Open PRs:** #162 judge labelling tools (label page + importer), unmerged on purpose: the owner chose to skip judge labelling for now (option: an offline Claude faithfulness review). Leave it open.

**Done (all merged):** #153 deep-dive refresh; private `basel.engineering-docs` #1 (About Basel synced from the Drive bullet bank + About Me, new `personal.md`) and #2 (portfolio self-reference, livelier fun facts); #154 golden set (92 cases); #155 judge code (Nova Pro); #156 RAG plan brief; #157 corpus scope + `kind` tag (tests and plans excluded); #158 stale sweep apply (owner merged on the local dry-run evidence, about 49 test/plan docs are deleted on the next release); #159 `PORTFOLIO_PROJECT.md`; #160 phase 3 baseline; #161 status reports renamed `YYYY-MM-DD-HHMM`; #163 daily LLM cap 500; #165 per-answer sources list removed in all topics and About Basel chunk paths replaced by section labels server-side; #164 prompt v15 (fuller answers, meeting-appropriate tone, no file/source references, content-filter stop becomes an uncached abstention, max_tokens 600). Reports: `project/status/README.md` rows dated 2026-10-03.

**Results (paid runs, same index):** v14 → v15 fact_coverage 0.80 → 0.87, false-abstain 6.6% → 1.3%, pass 0.70 → 0.74, unsupported claims 3 → 5, median words 14 → 42; run-to-run variance is high (65-70 of 92). Tone rules and the portfolio self-reference did not take on Nova Lite via the prompt; the self-reference moved into `personal.md`. Not verified here: this wrap-up only edited process docs (SNAPSHOT test run noted in the PR).

**Owner decisions:** no judge labelling for now (Nova Pro was the chosen judge). Remaining roadmap: RAG phase 7 (section-aware chunks), phase 8 (hybrid BM25 + vector; the diagram's Vector Search node updates in that PR), phase 10 (answer logging), real portfolio projects (the first needs the planned-marker exemption, see `PORTFOLIO_PROJECT.md`), server-side footer stats. Dropped: self-healing ASG, M4 / Google Drive, phases 9 and 11, load-test numbers, the About Basel fallback and bubble tightening.

**Owner one-time to-dos:** confirm the SNS alarm email; check AWS Free vs Paid plan (Free auto-closes after 6 months); run Ops · List snapshots once; consider an AWS Budgets alert (T4g trial ends 2026-12-31, about $5-6 → $17-18/month); CSP enforce optional. Private-repo edits need a Release run to go live. The About Me Google Doc lacks the `personal.md` tweaks from docs #2.

**Next action:** RAG phase 7 (read `orchestration/rag-plan-brief.md` first). Later option: Claude Haiku on Bedrock for answers (needs Anthropic's first-use form; worst case about $75/month at 500/day vs about $4.50 on Nova Lite). New minor backlog items are under BACKLOG "Bugs".

**Environment:** Docker Desktop works; the shared compose MySQL may lag Alembic head, so agents use their own containers for DB tests (rag-plan-brief Gotchas).

**Where to look**
- Next actions: `project/BACKLOG.md` "> RESUME HERE"; owner-only decisions: its "Open decisions".
- Architecture and live state: `project/SNAPSHOT.md`. Per-change reports: `project/status/README.md`.
- Standing rules: `project/CLAUDE.md` "Working style". UI decisions: `project/MOBILE_DESIGN.md` "Owner decisions".

## Checkpoint format (when switching coordinator)

Replace "Current" above with: active coordinator; branch/worktree/commit; done; verification (exact commands and results); not verified; review (reviewer, round, verdict); open PRs (round, verdict, fixes since, merge order, waiting on whom); paid calls and live changes; next action; blockers. The incoming agent reads this file, `SNAPSHOT.md` and `BACKLOG.md`, checks `git status`, `git worktree list` and open PRs, and treats unknown uncommitted work as another agent's until understood. Never run two coordinators on one branch.
