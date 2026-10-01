# Agent entry point

**Codex's current role (since 2026-09-30): reviewer and check-in gate, not implementer.** Claude orchestrates and implements. When dispatched, Codex reviews a change against its requirements and validates it hands-on — full permissions, but no file edits, commits, pushes, or live AWS/Kubernetes access. The dispatch prompt follows `project/orchestration/codex-reviewer.md`; follow its rules and report format.

Before reviewing, read `project/orchestration/reviewer-primer.md` first (system map, hard rules, known traps, per-area checklist). Do not scan all of `project/SNAPSHOT.md`; go deeper only as the change requires: the relevant `docs/DESIGN*.md` section, `project/SNAPSHOT.md` or `AGENT_HANDOFF.md` for history, and for frontend changes `project/MOBILE_DESIGN.md`.

`project/CODEX.md` is Codex's guide for the fallback case where the owner explicitly hands implementation back to Codex. `project/AGENT_HANDOFF.md` records the current coordinator.
