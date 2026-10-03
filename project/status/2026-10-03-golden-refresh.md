# Golden set refresh (RAG plan step 0)

Branch `eval/golden-refresh`. Merge order: after PR #153 (`docs/deep-dive-refresh`), which this branch merges so the system gold snippets match the updated deep dive.

## TL;DR

The private About Basel files were resynced from Drive (`corpus/drive-sync-2026-10-03`, PR not yet merged), so 12 gold snippets no longer matched. They are fixed, 14 new About Basel cases and 2 unanswerable cases were added (91 cases total, holdout unchanged at 15), and a free fake-provider run reports zero unreachable snippets. No paid calls.

## What changed

- `eval/golden.yaml`: re-quoted snippets in `me-current-role`, `me-assets`, `me-outages`, `me-release`, `me-fitbit-gate`, `me-background`, `me-languages`, `me-observability`, `sugg-me-distributed`, `mt-me-google-years`, `mt-me-education-years`. `me-site-stack` lost its `known_failure` (the RDS error is gone from `skills.md`) and now points at `projects.md` / `bio.md`.
- New cases (`needs_owner_review: true`): origin, favorite food, light vs dark mode, hobbies, complexity, team culture, favorite professional project, favorite languages (all `personal.md`); Azure regions, AI code review pilot (`microsoft.md`); Selenium reliability, YouTube backend test services (`google.md`); college (`bio.md`); retrieval stack (`projects.md`). Unanswerable: salary expectation, where Basel lives.
- `eval/schema.py`: `personal` added to `KNOWN_PRIVATE_SOURCES`. `services/tests/test_eval_golden.py`: case-count bound 95, abstain rate 7/12, known-failure test marks its own case.

## Open items

- `personal.md` and its facts are new to the corpus; the owner reviews the new cases.
- "Where does Basel live?" expects abstain, but the files name Redmond, WA (work location) and San Francisco; the answer may legitimately cite those.
- The fake baselines stay stale; the Titan baseline refresh is phase 3.
