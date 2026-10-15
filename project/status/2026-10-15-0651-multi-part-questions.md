# Prompt v23: answers cover every part of a multi-part question (2026-10-15 06:51 PT)

**Status:** merged (#216, 779a167); the release started automatically on merge and also picks up the private bio changes (docs #14, #15). Live check not done in this session.

## TL;DR

- Visitors often ask two things at once. One search already finds the sources for both parts, but Nova Lite sometimes answered only one ("Where do you want to work and what kind of roles are you after?" got only the roles).
- For a message that looks multi-part, a short Nova Lite call lists its on-topic questions while retrieval runs. The list closes the answer prompt, so the answer covers each part. Single questions are untouched.
- Paid eval, same local index, 2 runs each: pass 150-152 → 152-154 of 188; the two-part cases 8 → 9 of 11; third person, follow-ups (9/9) and false abstentions unchanged; both new injection cases declined.

## What changed for a visitor

A two-part question gets both parts answered, in order, with no extra wait in the common case (the split call, about 0.4 s, overlaps retrieval). Off-topic or injected parts ("…and write me a Python function") are not listed, so they are declined as before.

## How it works

```
question ──> looks_multi_part? ──yes──> split call (Nova Lite, 120 tokens, 1 budget unit) ─┐
    │                                                                                    │ (runs in parallel)
    └──> embed ─> answer cache ─> queue ─> worker retrieval ─> wait ≤ 2 s for the split ─┘
                                                                  │
          answer prompt + "The visitor's message (untrusted user input) asks N things:
          (1) … (2) … Answer each one that the sources answer, in that order,
          following every rule above."
```

- Gate (`services/glassbox/question_parts.py`): two question-mark runs, or "and/also/plus" (or a comma) followed by a question word. "Have you used Kafka and Kubernetes?" is not split; it already works as one question.
- Follow-ups split the standalone rewrite. Any split failure or timeout answers without the list, and that answer is not cached. Prompt version v23 (answer-cache identity).

## Key decisions

- **Not agentic RAG / not one search per part.** The 2026-10-15 probe: one search retrieved both parts in 11 of 11 questions; per-part searches did not help and once mixed the CSP-report limiter's 30 into the rate-limit answer (the real limit is 20).
- **Hint after the question:** Nova Lite follows what it reads last; a plain "answer every part" rule changed nothing.
- **On-topic parts only:** with every part listed, the coding request in `mp-inj-role-code` was answered with code; v22 declined it. The split now leaves such parts out.

## What review caught (Opus, 2 rounds: REQUEST CHANGES → APPROVE WITH MINORS)

- The deep-dive note pushed a section over the chunk cap (CI would fail): moved to DESIGN.md's budget row.
- The hint raised injected parts above the rules: reworded, then the on-topic split (above).
- Follow-ups split the raw question; a failed split's answer was cached; `??` fired the gate; the split stream lacked `aclosing`; missing timeout/cancel tests. All fixed.
- Minor left open: split parts can be third person ("What college did Basel go to?"); the eval showed no third-person increase.

## Operational notes and risks

- Cost: one 120-token call for about 10% of eval questions; the daily budget counts it as a rewrite unit.
- The query log's `stage_timings_ms` records `question_parts` and `question_split_missing`.

## Open items

- `mp-system-cache-cost`, `mp-system-llm-database`, `rec-project-site` fail in both v22 and v23 (one part too thin: "Bedrock" instead of Nova Lite; caches listed instead of the 0.95 match). Prompt work, not the split.
- `inj-history` still prints the injected word (pre-existing, BACKLOG).
