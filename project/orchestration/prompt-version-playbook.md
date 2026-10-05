# Prompt-version playbook (answer prompt v_N → v_N+1)

Read this, then `rag-plan-brief.md` (environment, Gotchas), before any answer-prompt change. It front-loads what v15–v17 cost to learn: v17 took about 400k implementer tokens over three rounds, mostly exploration, repeated full runs and prompt ablations.

## Spec and rules
- The spec is `BACKLOG.md` "> RESUME HERE". The owner's standing answer rules:
  - first person as Basel (the uploaded-consciousness persona);
  - 1–2 sentences;
  - warm, friendly tone;
  - dual experience for "have you used X?", without volunteering a missing side;
  - a production question with personal-only use gets "No, but I used it extensively in my personal project X";
  - absent tech gets "I don't have X in my memory" (an abstention, never cached);
  - no billing-plan details;
  - strict factuality.
- **Public repo:** no About Basel text in code, examples, gold snippets or status reports. Few-shot examples in code use placeholders (`<Company>`, `<Tech>`, `<Project>`). The owner's approved real examples live in the private corpus repo.
- Nova Lite copies few-shot examples and follows rules placed **after** the question; it mostly ignores rule lists. Tone comes from examples, not from adjectives.

## Measure cheaply, in this order
1. **Smoke:** `python -m eval.run_answers --cases <ids>` (under $0.002 each). Standard probe set: `me-current-role`, `system-budget`, `system-cost`, `me-site-stack`, `me-fav-languages`, `sugg-system-db-terraform` (the db chip, fragile), `inj-history`, plus a Kafka, a gRPC, a Go (absent) and a "Kubernetes in production?" question. Run length rules x3 on `me-current-role` and `system-budget`.
2. **Same-source comparison:** `run_answers --replay <stored run>` reuses stored retrieval, so two prompts see identical sources with no ingest (about $0.03 per 95 cases on Nova Lite). Good for A/B on wording; not proof for what ships.
3. **Fresh index before merge:** clone the private corpus (https + gh credential helper) into `corpus/about-me-private`, start throwaway `mysql:8.0` + `redis/redis-stack-server` on spare ports, `alembic upgrade head`, and ingest in the background (a full Titan ingest takes about 30–45 min; don't poll). Then 2 live runs each of old vs new. State the corpus commit measured.
4. **Re-grade, don't re-run:** use `grade_case` on stored rows after a grader change.

Report a table: pass, fact_coverage, false abstain, third person, median/p90 words, unsupported claims (offline review: read answers against their sources and count unsupported claims), measured cost. **Acceptance:** unsupported claims must not go up; third person about 0; the db chip and the suggested-question chips still answer.

## When a change breaks a case
Ablate. Remove one prompt part at a time on the failing case (smoke runs) until it passes; that finds the cause in a few cents. In v17 this found that the first-person and factuality bullets placed after the question made the db chip abstain. Don't iterate wording blind; two blind fixes in a row means ablate.

## Known traps
- Pointer stripping must remove only pointer-only text, never whole parentheticals that carry facts (v17 review round 1). There are regression tests in the suite.
- Unlabelled About Basel chunks let facts bleed between projects ("MEAN stack" landed on the site). Sources are labelled "About Basel (<topic> · <section>)".
- Temperature 0 on Nova Lite is still not deterministic (about 11 of 95 answers differ between runs). Compare 2+ runs.
- Nova 2 Lite (`us.amazon.nova-2-lite-v1:0`) was tested on v17: more fact coverage, but 2.7x the words, about 4x slower to the first token, about 5.6x the cost, an injection miss and an invented claim. Not adopted (2026-10-04). Haiku 4.5 needs the owner's Anthropic form.

## Dispatch and review
- One implementer (Opus for prompt judgment) in `.worktrees/prompt-vN`. Resume the same agent for fix rounds. Budget the paid runs ($1 cap; report spend). Review with an Opus reviewer per `reviewer-brief.md`, then resume the same reviewer for round 2.
- The review focus that paid off: whether pointer or label processing loses facts; whether the cache treats a denial as an answer; whether the measurement matches what ships (fresh index); whether About Basel text leaked into the repo.
