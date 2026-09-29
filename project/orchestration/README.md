# Multi-model orchestration

Templates for dispatching implementation to Codex (backend) or a Sonnet subagent (frontend), and review to Gemini, so Claude (the orchestrator) fills in a template instead of re-deriving the boilerplate each time. Background and full findings from the pilot that produced these: `project/BACKLOG.md`.

- `codex-implementer.md` — dispatch template + known CLI constraints for Codex.
- `gemini-reviewer.md` — dispatch template + known CLI constraints for Gemini (via `agy`).

## Roles

- **Claude Opus (orchestrator):** writes the blueprint (exact spec, no placeholders), dispatches implementation, dispatches the diff for review, and directs real end-to-end verification before considering anything done. **Does not do hands-on implementation, debugging, or verification legwork itself** — that's what the roles below are for. Opus's job is architecture, judgment calls, and reading reports, not running dev servers or chasing down CSS bugs by hand.
- **Backend implementer (Codex, `gpt-6-sol`):** implementation from an exact spec. Sandboxed (`workspace-write`), no network access — any step needing network (package installs, binding ports, real DB/service connections) has to be done by whoever is verifying, not Codex itself.
- **Frontend — two-step, both delegated away from Opus:**
  1. **Codex** still writes the initial component/CSS code where it can (no network needed for that part) — same as backend.
  2. **A Claude Sonnet subagent** (`Agent` tool, `model: "sonnet"` — confirmed 2026-09-30 to genuinely route to Sonnet, self-identified as `claude-sonnet-5`) does everything Codex's sandbox can't: `npm install`/scaffold setup, running the dev server, real browser verification via `claude-in-chrome` (screenshots, live DOM inspection), and fixing whatever it finds. This is the delegation Opus was skipping — e.g. the Task 1 token-collision bug (see `project/BACKLOG.md`) was root-caused and fixed by Opus directly the first time, which is exactly the pattern to avoid going forward: hand that loop to Sonnet instead.
- **Gemini (`gemini-3.8-flash-medium` via `agy`):** spec-compliance + code-quality review, combined in one pass. **Fallback:** if Gemini's quota/tokens run out, run the same review via a Claude subagent instead (`superpowers:code-reviewer` or a general-purpose Agent dispatch with the same review brief) rather than blocking — don't skip review.
  - **Status as of 2026-09-30, ~2:33am:** Gemini's individual quota hit `RESOURCE_EXHAUSTED` (429), resets in ~7 days (~165h). **Reviews from here forward use a Claude subagent** with the same review-brief format/rigor until Gemini's quota resets — revert back to `agy` once it does (cheaper/separate quota from Claude's own usage).

## Division of labor for git

In a worktree, only the orchestrator commits (Codex's sandbox can't write worktree git metadata). Elsewhere, Codex can commit if explicitly instructed to.
