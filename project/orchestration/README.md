# Multi-model orchestration

Templates for dispatching implementation to Codex and review to Gemini, so Claude (the orchestrator) fills in a template instead of re-deriving the boilerplate each time. Background and full findings from the pilot that produced these: `project/BACKLOG.md`.

- `codex-implementer.md` — dispatch template + known CLI constraints for Codex.
- `gemini-reviewer.md` — dispatch template + known CLI constraints for Gemini (via `agy`).

## Roles

- **Claude (orchestrator):** writes the blueprint (exact spec, no placeholders), dispatches to Codex, dispatches the diff to Gemini for review, and — critically — always runs real end-to-end verification itself (tests, `docker compose up`, actual requests) before considering anything done. A review approval is not a substitute for this.
- **Codex (`gpt-6-sol`):** implementation from an exact spec.
- **Gemini (`gemini-3.8-flash-medium` via `agy`):** spec-compliance + code-quality review, combined in one pass.

## Division of labor for git

In a worktree, only the orchestrator commits (Codex's sandbox can't write worktree git metadata). Elsewhere, Codex can commit if explicitly instructed to.
