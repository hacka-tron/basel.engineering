# RAG phase 3: paid baseline of the current system (prompt v14)

## TL;DR
The "before" numbers for the RAG quality plan, measured on 2026-10-03 against `main` `ce1b410` with Titan v2 embeddings and Nova Lite answers. Cost about $0.04 (limit was $1). The stress-test case fails `fact_coverage`, as the acceptance asks. Nothing in production changed.

## What changed
- `eval/baselines/amazon.titan-embed-text-v2_0.json`: refreshed to the v2 format (fingerprints, chunk recall, noise).
- `eval/baselines/answers-nova-lite-v14.json`: new; summary, per-corpus summary and per-case results (about_system: question, retrieved paths, answer, grades; about_me: redacted to ids, grades, metrics, retrieved paths).
- `docs/DESIGN-005-rag-quality.md` §2.2 and §2.3: measured numbers.

## How it was run
Own MySQL 8.0 and Redis Stack containers on ports 23306 and 26379 (the shared stack is dirty), `alembic upgrade head`, full Titan ingest (170 documents, 1,351 chunks; about 45 minutes because embedding is sequential), `run_eval --write-baseline`, `run_answers --paid` without the judge. The private About Basel corpus was cloned at main.

## Results
Retrieval, k=8, 67 cases: recall@5 0.851, MRR@5 0.706, recall@8 0.910, chunk recall@8 0.731, chunk MRR@8 0.553, noise@8 0.127. about_me 0.909/0.864/0.970/0.970/noise 0; about_system 0.794/0.553/0.853/0.500/noise 0.25.

Answers, 90 cases: 63 passed (0.70), fact_coverage 0.789, abstain on unanswerable 11/11, false abstain 0.068, planned/live status 11/14, median 13.5 words. about_me 0.83 pass; about_system 0.57. Planned cases pass only 2 of 8; live 5 of 6.

Note: this baseline was taken before #157 (corpus scope) merged, so noise@8 will drop afterwards; that is expected, not a regression. The committed answers file has `about_me` free text redacted (private corpus).

## Findings
- Tests and plan files crowd the top 8 for about_system questions (rate limit: six test chunks).
- Planned cases retrieve the right file but rarely the section that says "planned" (chunk recall 0.125); three wrong abstentions come from that.
- About Basel has 15 chunks, not 6, so the top 8 does not cover the corpus; `me-education` misses its chunk and abstains.
- Answers are very short (median 13.5 words), which fails many `must_include` facts.

## Operational notes
- Production uses Nova Lite through `BEDROCK_LLM_MODEL_ID=us.amazon.nova-lite-v1:0` (the k8s configmap). The code default is Haiku 4.5, which this AWS account cannot call yet (Anthropic use-case form not submitted); set the variable for any local paid answer run.
- Re-running the baseline needs the same containers and a Titan re-ingest.

## Open items
Phases 5 to 8 compare against these numbers. The judge was deliberately not run.
