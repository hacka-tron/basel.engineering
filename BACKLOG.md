# BACKLOG

Bugs, stubs, future ideas, and the cross-session resume point. Update whenever a task completes or something new surfaces.

## > RESUME HERE

Next action: execute `docs/superpowers/plans/2026-09-28-phase0-repo-scaffold.md`, starting at Task 1 (repo guardrails: lint/format/secret-scan config).

Not blocked on anything — M1 local work is independent of the M0 AWS-account items below.

## Open decisions (owner-only, can't be delegated to an agent)

- AWS account: Free vs. Paid plan — decide before Milestone 2 (AWS deploy). Free plan auto-closes the account after 6 months or when credits run out.
- Cloudflare origin protection: Worker-injected secret header vs. IP-range-only — IP-range-only is the current plan; revisit only if abuse becomes a concern.
- Frontend delivery mechanism on the EC2 node (baked into the API image vs. a separate static-file container) — decide at Phase 6.
- Project name: "Glassbox" is a placeholder (DD1 §19).

## Future milestones (not started)

- **M3 (DD2):** self-healing (ASG + Elastic IP reassociation), conversational chat memory + follow-up rewriting, live chat UX (Stop button, auto-scroll, localStorage persistence), Cloudflare-flavored streaming hardening.
- **M4 (DD3):** Google Drive + Git connectors, S3 raw zone, SQS + DLQ, KEDA ScaledJob ingestion, nightly reconciliation, blue-green re-embedding.

## Bugs / stubs

_(none yet — no application code has been implemented)_

## Ideas

_(none yet)_
