# basel.engineering orchestration inputs

Generic dispatch, review and merge procedures load from `~/Coding/template/core/CLAUDE.md` and its `review-gate`, `merge`, `status-report` and `handoff-checkpoint` skills. This file supplies the facts and hard rules those procedures need for this production site. Use `reviewer-brief.md` for a review and `reviewer-primer.md` for the system map and known traps. RAG work also uses `rag-plan-brief.md`.

## Hard rules for every dispatch

Copy these rules into **every** relevant implementer and reviewer brief; agents do not inherit standing instructions:

- never read, open or copy any `terraform.tfstate`, `*.tfstate.backup`, `*.tfvars` or plan file;
- no `terraform apply`, no AWS/SSM/`kubectl` writes against the live system, no GitHub environment or secret changes;
- never run `git stash`;
- pytest only against your own throwaway MySQL/Redis containers, with `GLASSBOX_TEST_MYSQL_PORT` and `GLASSBOX_TEST_REDIS_PORT` exported for every run (including `-x`, `-k` and single-test runs): without them the tests default to 3306/6379, the owner's shared local compose stack (`services/tests/stack_ports.py`), and leave test rows in it.
  Live changes happen only through the Terraform, Bootstrap and "Ops · ..." workflows after the owner's approval click; give the owner the click, never commands.

Before starting a full test suite, check that no other agent's full suite is running. Serialize full suites across agents; never dispatch an implementer whose suite will overlap yours.

## Shared local resources

| Resource | Owner/shared use | Agent use |
|---|---|---|
| API and Vite | 8000 and 5173 | 8001 and 5174, or another assigned free port. Do not stop or recreate the shared `docker compose` stack. |
| MySQL | Compose port 3306 | Own throwaway container and assigned host port; export `GLASSBOX_TEST_MYSQL_PORT` on every pytest command. |
| Redis | Compose port 6379, DB 0 | Own throwaway container and assigned host port; export `GLASSBOX_TEST_REDIS_PORT`. Tests use DB 0 and endpoint tests also use DB 15, so a separate DB on the shared port is insufficient. |

## CI and operations

- CI: `.github/workflows/ci.yml` reports required `backend-tests` and `frontend-checks`; `.github/scripts/ci-code-changed.sh` skips the test jobs only for the exact process-doc-only paths in `project/CLAUDE.md`.
- Release: `release.yml` publishes `build-N`; `sync-deploy-branch.yml` and Flux advance `deploy`. A normal release follows the approved merge path.
- Live infrastructure: `terraform.yml` uses the `terraform-prod` approval environment; `bootstrap.yml` handles bootstrap; `ops.yml` contains the named "Ops · ..." workflows. See `infra/CI.md`. The owner gives approval clicks, not shell commands. The owner must give explicit go-ahead before merging live AWS/cluster changes beyond a normal release or new permissions, as specified in `project/CLAUDE.md`.
- PR body edits and base retargets use GitHub REST (`gh api -X PATCH`); `gh pr edit` fails in this repository. Merge method is `--merge`. The global `merge` skill handles the sequence.

There is no current `AGENT_HANDOFF.md`. `project/BACKLOG.md` (`> RESUME HERE`) and `project/SNAPSHOT.md` hold the current checkpoint; older handoffs are in `project/archive/`. Keep project-specific expensive-loop observations in `token-log.md`.
