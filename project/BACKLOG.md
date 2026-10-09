# BACKLOG

Bugs, stubs, future ideas, and the cross-session resume point. Update whenever a task completes or something new surfaces.

## > RESUME HERE

**2026-10-08 (owner):** answers no longer say the codename "Glassbox": a deterministic stream rewrite (`services/glassbox/codename.py`) turns "the Glassbox system" into "this site" (also on cached replays). The docs still use the codename; renaming it in `docs/**` is an option if answers still read oddly. Known rough grammar left as is (#205 review minors): "a Glassbox feature" -> "a this site feature", "Glassbox ingestion" -> "this site ingestion". The two streaming vs whole-text differences from the #205 re-check ("Glassbox system." glued to a word; capitalization after a long run of spaces) are fixed (2026-10-09, `fix/codename-stream-parity`): the stream holds "Glassbox system. " and keeps the whole sent text for capitalization.

**State (2026-10-05 PT, evening):** all merged and live. Owner feedback item 0 shipped as **prompt v19** (#189) plus **v20** (#191); **v21** (#195) put the public contact address in the examples so no `<email>` placeholder reaches an answer, with private docs PRs #12/#13 (owner sign-off round 3 complete). That covers:
- no model-written "Sources:" line (a deterministic filter);
- first-person chips and abstention lines;
- a playful route for flirty or off-topic questions (never cached);
- why-leaving vs why-looking, and show vs movie;
- **one topic per About Basel section and per approved answer** (owner rule: store one-topic answers and retrieve the right one), indexed one chunk per section (85 chunks), with no About Basel per-file cap;
- About This System answers may use up to 3 sentences;
- the db chip renamed "Is the database managed by Terraform?";
- topic-gated examples (Kubernetes, pay, "is it really you"); the production example goes last only for production-cue questions;
- golden checks recalibrated to the core fact (owner-approved).

Fresh index, recalibrated checks: about 94/110 golden and 42/54 overlay (v18 was about 91 and 38).

Also live:
- the **daily answer cap is 200** (#190);
- the **budget guardrail** (#193, applied): `glassbox-monthly-cost` is $30 for the whole account, email only; `glassbox-bedrock-answers` is $12.50 (Bedrock plus Marketplace, gross) with an automatic deny on the answer models (never Titan) at 100%; the app degrades to retrieval-only.
- Test-port hard rule (#192).

The account is on the AWS **Paid plan** (owner, 2026-10-05).

Production answer model: still **Nova Lite**. **GPT-6 Luna A/B is running** (branch `feat/answer-model-luna`; the owner accepted the Marketplace agreement 2026-10-05). Haiku is dropped (needs the Anthropic use-case form; owner chose Luna).

No PRs open. Live state: `project/SNAPSHOT.md`. Playbooks: `orchestration/prompt-version-playbook.md`, `orchestration/corpus-resync.md`, `orchestration/token-log.md`.

**Done 2026-10-04/05 (details in `project/status/`):**
- v17 (#176): persona, brevity, dual experience, factuality.
- Owner sign-off: three rounds.
- v18 tone routing; v19/v20 (above).
- Phase 8 hybrid search with dual-experience slots.
- Phase 10 answer log.
- Nova 2 Lite was tested and not adopted.

### Next up, in order

0. **Finish the GPT-6 Luna A/B** (in progress). If it wins, open a reviewed PR:
   - a `bedrock_profiles` entry (`us.openai.gpt-6-luna` / `openai.gpt-6-luna`), which the budget-stop deny then covers automatically;
   - the `project/default` InvokeModel statement if needed;
   - configmap `BEDROCK_LLM_MODEL_ID` and `BEDROCK_LLM_REASONING_EFFORT`;
   - per-model prompt selection if a Luna-tuned prompt wins.
   It needs the owner's Terraform apply click (owner go-ahead given 2026-10-05). Then confirm the first Luna charge lands in the Bedrock budget (billing entity AWS Marketplace).
0b. **Remaining Nova Lite misses** (if Luna isn't adopted): Python and React "at Microsoft" claims on personal-only tech (about 1–2 of 3 runs); the regex gates miss "for work", "to prod", "Are you a bot?" and "rate-limiting"; "How is the site deployed to production?" abstains.
0c. **Resume bullet figures:** re-run the file-level retrieval eval (recall@5, MRR) on the live v21 code and index. The committed baseline (2026-10-05, 82 cases) is recall@5 0.89, MRR 0.70.
0d. **Flaky tests:** `test_answer_cache.py::test_repeat_api_request_skips_retrieval_and_llm` and `test_ask_endpoint.py::test_full_stream_and_query_row_with_simulated_worker` fail on the first run against brand-new test containers and pass on a re-run. Probably a readiness race in DB setup.

1. **Watch the live answers for a few days**, using the answer log (#185). Hit rate: the SQL is in `eval/README.md`.
   - Check that "have you used X?" answers name both sides where the data has both. Phase 8 now retrieves both, but Nova Lite still drops a side for Redis and React sometimes. If it persists, try a two-sided approved example again, now that slots supply both chunks (v18 dropped it because it invented a work side).
   - Check the strict prompt's unsupported "favorite X" framing (golden cases are `known_failure`, see `status/2026-10-04-2144-rag-prompt-v18.md`).
2. **Real portfolio projects** (the owner adds them; `project/PORTFOLIO_PROJECT.md`). The planned-marker exemption ("Open items from the portfolio backend") is still open; projects need it once a write-up uses planned wording.
   - **GoalBuddy** (2026-10-07, `content/portfolio-goalbuddy`): first real project, code link only (the app is a work in progress). Decisions to roll back if unwanted: the body avoids every planned-marker word, so it shipped without the exemption; it claims only what is on GoalBuddy's public `main` (the web preview, connection-goals and authz work are local branches there, so it says Android only); screenshots come from GoalBuddy's web preview against its local dev DB (test accounts only); `portfolio.test.ts` no longer pins an empty corpus. New optional `cover` field (owner request: a thumbnail-style card picture kept out of the gallery); GoalBuddy uses its sign-in icon. Follow-up (minor, #200 review): `portfolio.py` lacks the frontend's image-extension and hidden-file rule for `src`/`cover` (the build still rejects them), and a missing cover is reported as "visual …". Live 2026-10-07 (#200): card, cover, details sheet and screenshots checked on basel.engineering, and "Tell me about GoalBuddy" answers from the write-up. Golden cases (prerequisite 2) are now unblocked. Share links (owner request, 2026-10-07): `?project=goalbuddy` and `?topic=basel|system|portfolio`; the address bar follows the view, replaced in place (owner, 2026-10-08); they never auto-ask the chatbot (decision: a shared link must not spend an answer per visit).
3. **CSP enforce:** after about 2026-10-08, the owner runs Ops · Diagnose on a few days. If the "CSP Report-Only violations" section is quiet, switch to enforcing ("Security" section).
4. **Small follow-ups:**
   - an Ops · Diagnose answer-cache hit-rate line (needs a Terraform apply);
   - `sample_live.py` weekly review of live answers (phase 10 follow-up);
   - "Do you know Taylor Swift?" fires the `swift` tech detector (routes strict, tone only);
   - the `team|teams` work veto makes the sports cue dead (harmless);
   - `inj-history` still prints the injected word (client-supplied history, uncached, the attacker's own screen only);
   - two cache KNN lookups per miss.
   - Prompt v19 review leftovers (2026-10-05): approved few-shots can bleed into About This System answers (once, on a 3-document mini index, the db chip answered with a degree fact, and it was cached); playful-cue false positives "Do you like me as a candidate?" and "Are you single-handedly…" (never cached); the private `rec-lead-style` check was loosened to one topic (working style) while the approved answer still names simplicity and documentation, so re-check it against the owner's one-topic rule; Nova Lite still invents a work side for React (v18 1/2, v19 2/2); BM25 statistics are index-wide, so the 85 one-topic About Basel chunks moved one About This System case (`planned-metrics`) out of the top 8.
5. **Server-side footer stats** (Phase 7 leftover). Low value.

**Decided, don't build:**
- Rewriting every question: keep the rewrite for follow-ups only, and look up the answer cache before any rewrite.
- Top-2 retrieval (too few chunks for dual-experience answers).
- Synthetic per-skill "cheat sheet" chunks, unless phases 7 and 8 still miss dual-experience answers. If built: generate them offline from the private docs, set `derived_from`, gate them with the factuality grader, and keep them in the private repo (never public About Basel text). The 2026-10-04 corpus resync added professional Kafka and gRPC usage, so this gap is closed.

**Owner to-dos:**
- Run **Ops · List snapshots** once.
- **Early January 2027:** buy a 1-year no-upfront Savings Plan/RI for the t4g.small; the EC2 trial ends 2026-12-31 (about 30–40% off the ~$12/month instance).
- Delete the old hand-made `Glassbox-Monthly` budget in the console (it counts credits, so it never fires; #193 replaced it).
- Decide whether to sync the About Me Google Doc with private `personal.md` (the portfolio self-reference, "very complete project", livelier fun facts), if Drive stays the source of truth.
- Private-repo edits need a Release run to go live (Actions → Release, or any push to main).

**Dropped (owner, 2026-10-03):** self-healing ASG (AWS alarms cover it), M4 / Google Drive pipeline, RAG phases 9 and 11, load-test numbers while KEDA is off, the About Basel → About This System fallback, and the chat bubble tightening.

**RAG evaluation state (2026-10-03):** evaluations are approved. Golden set is 92 cases (#154); judge code on Nova Pro (#155); phase 3 baseline (#160); prompt v15 (#164), v16 (#170). Same index, v14 → v15: fact_coverage 0.80 → 0.87, false-abstain 6.6% → 1.3%, pass 0.70 → 0.74, unsupported claims 3 → 5, median words 14 → 42. Run-to-run variance is high (65–70 of 92 pass). The judge labelling tools merged in #162; labelling itself is skipped for now in favour of an offline Claude faithfulness review.

**Possible later:** an optional sentence on the diagram's LLM node about personal-data masking.

**Earlier items, status:** the portfolio feature, Ops · Reindex (#136) and the Stream check (#143) are done (reports in `project/status/`). Alarms and snapshots (#124) are applied; the owner's confirmations are in the to-dos above. **KEDA stays off** (owner, 2026-10-02; re-propose only if asked or the node is upgraded). The private About Basel repo and personal-data guard are done; after private edits, run Release by hand.

**Portfolio status:** shipped and live (build-110); the topic stays hidden until a project is published.

**Open items** (not started):

- Minor (#133 review): the Dockerfile COPY test reads line by line, so a backslash-continued COPY would be missed.

**Open items from the portfolio backend (PR 2)** (not started):

- **Portfolio sources get the planned marker.** `_mark_planned` (`api/ask.py`) runs over every non-code source, so a project write-up mentioning "SQS", "ASG", "deferred" or "planned" would be labelled `[PLANNED, not built yet]` and answered as not built. Exempt `corpus/portfolio/` before real projects land (a prompt change: owner go-ahead, spec §6.3).
- **Portfolio eval and golden cases:** `eval/run_eval.py` and `eval/schema.py` know two corpora; add `portfolio` and golden cases for its suggested questions once the owner has added real projects (owner rule: not before).
- **Portfolio backend follow-ups (minor, #149 review).** (a) Mid-rollout skew: a new api can enqueue a `portfolio` job that an old retrieval worker picks up; it raises `unknown corpus`, the ask ends with `error internal` (no LLM spend, slot refunded), and that `warm-answers` CronJob run exits 1. Transient and harmless, but not covered by a test or the status report's skew note (which covers only new CronJob → old api, HTTP 422). (b) The `0007` downgrade's delete-then-narrow path is executed in CI only on empty tables (the round trip runs before the tests insert rows); its correctness rests on reading the code (only FK is chunks → documents with CASCADE; strict mode would fail the MODIFY loudly on a leftover row).

**Open items from the 2026-09-30/10-01 session** (not started unless noted):

- **About Basel corpus: RDS line is not a bug (owner, 2026-10-02).** `skills.md` describes the owner's skills, not this project's architecture, so its RDS mention stays as written. Possible later fix (low priority, no owner input needed): the golden case `me-site-stack` in `eval/golden.yaml` asserts `must_not_include` RDS on an About Basel answer, which tests the wrong thing; relax or retarget it to About This System the next time the eval set is touched (evaluations themselves stay parked). Done in the v17 PR (relaxed).
- **Decide: warm-answers persistently suspended?** Owner decision. Currently running and cheap (at most 10 LLM answers/day). A manual suspend does not stick, because Flux re-applied the CronJob with `suspend: false` after the incident; a lasting suspend means `spec.suspend: true` in `k8s/base/warm-cronjob.yaml` via a PR.
- **Real-phone checks:** the Contact clipboard-failure bubble (#78), iOS focus zoom (#77's iOS-only `maximum-scale=1`), and the portrait-only screen (#119) on iPhone and Android.
- **First change-type Ops runbook approval prompt:** not yet observed live since the per-action split (#68); confirm the `ops` approval prompt appears on the first non-diagnose run.
- **Leftover branches for the owner to decide on (delete or keep):** `debug/oidc-token-claims` (local only), `docs/claude-session-handoff`, `fix/ghcr-pat-auth`, `docs/orchestration-codex-review` (local and on origin). Stale merged branches on origin can also go.

**Roadmap after that** (owner's earlier list; details in the sections below):

- Security: confirm the security-pass fixes in prod, Terraform preview credential audit, enforce CSP after a week of clean Report-Only data.
- Self-healing node phases 2–4 (deferred; alarms + snapshots chosen instead).
- M4 content pipeline (DESIGN-003; Google Drive dropped for About Basel in favour of the private repo).

**Standing notes still true:** the grounding keyword list (`_PLANNED_SOURCE_SIGNAL` in `services/glassbox/api/ask.py`) is keyword-based, not tense-aware; keep live infrastructure off it and keep unbuilt work in the deep dive's last section. The daily budget spends a slot when generation starts, even if it fails (accepted). Anthropic Haiku streaming stays blocked by the account's first-time-use form: do not submit it on the owner's behalf. Switching embedding models means re-ingesting both corpora.

## Bugs

- **Minors from 2026-10-03 (not started).** (3) #164 review minors: deep dive step 9 doesn't mention the content-filter cache skip; DESIGN-005 §6 cost estimate is stale; the path / "as described in" graders can false-positive; old component questions saved in browsers show as waiting. (5) A full Titan ingest takes about 45 minutes (sequential embedding).

- **Phone details sheet follow-ups (minor, #148 review).** (b) On desktop, Back clears the selection (`diagramNav.ts` popstate to Chat) after a narrow-to-wide resize; gate `onReturnToChat` on `!isDesktopRef.current`. (c) The animated pan dips zoom to about 0.996 mid-flight; cosmetic. Spec §5.5 should also gain "focus then goes to the view toggle" and "tapping the strip while the sheet is open closes it; nodes switch only after closing" (lands via a #146 follow-up).

- **Ops · Reindex follow-ups (minor, #136 round-2 review).** (1) A failed `job/ingest` (e.g. Bedrock down during embedding) makes the runbook refuse ("has not completed") until the next release, because Flux doesn't recreate a failed Job; `--reindex` doesn't need Bedrock, so accept Failed as well as Complete when the image matches. (2) SIGTERM edge cases in `run.py` `reindex`: a SIGTERM during the lock's `SET NX` round trip, or during a blocking MySQL call past the 30 s grace, leaves `ingest:lock` held for its TTL; `test_reindex_releases_the_lock_on_sigterm` sends a real SIGTERM to pytest, which would kill the run where the handler can't install.

- **Open to work popover follow-ups (minor, #147 review rounds 1–2).** (1) `RotateScreen` stops Escape with `stopPropagation` on window capture, so the popover's window listener still sees Escape behind the rotate screen; harmless today, `stopImmediatePropagation` would make it airtight. (2) At 300–320px the open popover covers the ask input, so the first tap only closes the popover and focus mode needs a second tap. (3) `closesOnFocusOut` ignores the copy-fallback textarea, so if focus went from that textarea straight to an element outside the item the popover would stay open; unreachable today because `legacyCopy` adds, copies, removes and restores focus synchronously (Safari edge: Clipboard API rejected with the ask box focused leaves the popover open with focus in the ask box; an outside tap closes it).

## Feature work (priority)

Owner wants this worked first, ahead of the security/infra/data-pipeline groups below. Done and removed on 2026-10-01: conversational memory and per-topic chats (#41), Stop/auto-scroll/persistence (#48), suggested-question chips hidden once a conversation starts, hover-to-reveal node technology on the diagram (#33).

- **Portfolio frontend follow-ups (PR 3b):** (1) the lightbox is a native modal `<dialog>`: Tab never reaches the page behind it, but once per cycle it passes through the browser's own UI (Chrome's normal behaviour), not a strict in-page wrap; (2) the warm-up asks 10 suggested questions, exactly the per-IP limit of 10 per 10 minutes, so any new suggestion needs that revisited; (3) project bodies ship in the JS bundle (fine for a handful of projects; move to a fetched JSON if it grows); (4) no Portfolio golden cases until real projects land; (5) from #150 review round 1: a project selection survives Portfolio → Diagram → Portfolio (the sheet reopens; Back clears it), owner call; the empty state still shows a locked bar with nothing to select (unreachable while the topic is hidden); the JS bundle crossed Vite's 500 kB warning (501 kB); front-matter parity edge cases that fail loudly in one check only (explicit YAML tags, bare `=`, merge-only `<<:`, base-60 with a leading zero or a fraction, lone-CR line endings) are documented in `vite-plugins/portfolio.ts`, not aligned. (6) from #150 review round 2: after stepping through the lightbox with the arrow keys, the gallery row's "N / M" and scroll position don't follow it (cosmetic; `PortfolioVisuals.tsx`); a file starting with two BOMs passes the frontend build but fails the Python check (fails loudly in CI).
- **RAG quality and validation** (was "Answer thinness"). Plan: `docs/superpowers/plans/2026-10-01-rag-quality.md` (11 small phases, harness first; phase 5 is the thinness fix). Design and owner decisions: `docs/DESIGN-005-rag-quality.md`. Status report: `project/status/2026-09-30-2254-rag-research.md`. **Evaluations approved 2026-10-03** (owner added documents); phases 1-6 and 5 (prompt v15) merged; remaining: 7, 8, 10 (9 and 11 dropped), after v17 and the model A/B. See RESUME HERE.
- **Chat UX follow-ups left from the #93 review** (the reload-mid-retry, announcement and phone-focus items were fixed in the UI review follow-ups PR, `project/status/2026-10-01-0621-ui-review-followups.md`): (1) component and project questions are recognised by wording only (`SELECTION_QUESTIONS` in `frontend/src/App.tsx`, `lib/selection.ts`); (2) no tests for the App wiring (queued-ask effect, `isCurrent` guard, retry wiring, the save hold during a retry): the frontend has no React test renderer (`npm test` is Node's runner over `lib/`), so a hook extraction would still need a component-test setup (e.g. a DOM shim plus a renderer) to be tested. The new logic is in tested pure helpers (`lib/chatAnnouncement.ts` and `holdSaveDuringRetry` in `lib/chatRetry.ts`).

## Security

Code-level pass done 2026-10-01 (`project/status/2026-09-30-2306-security-pass.md`): no live secret in git history; worker error text, spoofable/shared rate-limit identity and the fail-open salt are fixed in code. Still open:

- **Confirm the security-pass fixes in prod** (owner clicks, listed in the status report's "Owner must confirm in prod"): `api` healthy in **Ops · Diagnose** after deploy (salt check), and the two-network rate-limit test (laptop vs phone on mobile data).
- **Terraform preview credentials (decide before granting anyone else write access).** Any account that can push a branch runs its own code in `terraform-plan` with no approval: edit `terraform.yml`, or add a `data "external"`/`data "http"` that runs at plan time. It can read the plan Cloudflare token, use `glassbox-ci-plan` to read `envs/prod/terraform.tfstate` (holds the MySQL password and IP-hash salt), and decrypt `/glassbox/*` SSM parameters. `bootstrap-plan` is the same pattern with less to read (IAM, bootstrap state). The fork guard works. Options: restore a required reviewer on `terraform-plan`/`bootstrap-plan`, or only plan after a maintainer approves. Today only the owner has write access, so this is not reachable.
- **Security headers: merged (PR #112, status report `project/status/2026-10-01-0609-security-leftovers.md`).** The API now sets HSTS (180 days, apex only), `nosniff`, `Referrer-Policy`, `X-Frame-Options: DENY` + CSP `frame-ancestors 'none'`, and a `Permissions-Policy`; live on the release after merge. After deploy: `curl -sI https://basel.engineering/` shows them (Cloudflare passes origin headers through).
- **Script/style CSP, Report-Only: merged (PR #121, status report `project/status/2026-10-01-1603-csp-report-only.md`).** `Content-Security-Policy-Report-Only` with same-origin scripts, styles, images, fonts and connections, plus `/api/csp-report` (rate-limited, 8 KiB cap, logs directive, blocked origin and path only, no DB writes). The iOS zoom script moved to `frontend/public/ios-zoom.js`. Zero violations in a headless Chrome audit of the build. Live on the release after merge; the **Ops · Diagnose** section needs the Terraform apply of the `ops` module (owner approval in the Terraform workflow).
- **Enforce CSP after a week of clean reports.** Once the Report-Only header has been live for 7 days: run **Actions → Ops · Diagnose** and read the section "CSP Report-Only violations, api log last 24 hours". It prints the number of violation lines, oversize reports dropped (a non-zero count means a Chromium batch over 8 KiB was lost, so look harder at the manual check), log-cap summary lines, and a count per directive and blocked origin. It only sees the running api pod's log since its last restart, so run it on several days of the week, not once. Clean means: no steady stream of lines on any directive, and nothing that the manual check below reproduces. Browser extensions also inject inline scripts and styles, so a few scattered `inline` lines are expected; a count that grows with traffic is not. Ignore lines whose blocked origin is a browser extension scheme (`chrome-extension:`, `moz-extension:`, `safari-web-extension:`) or obviously foreign hosts injected by extensions or ISPs; those are visitors' browsers, not the site. Also check by hand once: desktop Safari and Firefox plus an iPhone, devtools console free of "[Report Only]" messages, and in Chrome devtools Application → Reporting API that reports reach `/api/csp-report` (the `report-to` path was not observable in the local audit). If clean: rename the header to `Content-Security-Policy` in `services/glassbox/api/security_headers.py`, merge it into the existing enforced value (keep `frame-ancestors 'none'`, keep `report-uri`/`report-to` so blocks are still reported), update the tests and `docs/DESIGN.md` §11. If `style-src-attr` shows up from our own code, use `style-src-attr 'unsafe-inline'` with `style-src-elem 'self'` instead of loosening `style-src`. The owner never runs kubectl; Ops · Diagnose is the only log view needed. Also consider `includeSubDomains` once every name in the zone is known to serve HTTPS (Cloudflare "Always Use HTTPS" on the zone would cover it), and a longer max-age after a quiet period.
- **Optional diagnose section** printing whether the `glassbox-app` salt is set (length class only) and the Traefik proxy setup (`externalTrafficPolicy`, `forwardedHeaders` args, pod network) plus the number of live `rl:*` buckets. Owner decision: it reads a production secret's length on the node.
- **Alarms, uptime probe, daily snapshots, restore runbook: PR #124 merged 2026-10-02, waiting on the owner's Bootstrap + Terraform clicks (`infra/alarms-snapshots`, status report `project/status/2026-10-01-1938-alarms-snapshots.md`).** After merge, in order: (1) **Bootstrap** run + approve (expect only in-place updates to the `glassbox-ci`, `glassbox-ci-plan`, `glassbox-ops-read`, `glassbox-ops` policies); (2) approve the **Terraform** apply (topic, subscription, 2 alarms, `glassbox-dlm` role + attachment, DLM policy); (3) owner clicks the AWS SNS confirmation email; (4) **Ops · List snapshots** (alarms `OK`) and **Ops · Diagnose**; (5) the next day, **Ops · List snapshots** shows the first snapshot. Follow-ups: a cleanup path for root volumes kept by `restore-snapshot` (`old_volume=keep`; the ops roles can't delete volumes); a restore rehearsal (only possible on the live node today, so only on the owner's go-ahead); confirm the first uptime-probe failure email actually reaches the owner; re-enable `uptime.yml` if GitHub ever disables it after 60 quiet days.
- **`glassbox-ops-read` trust narrowed to the `ops-read` environment (merged with #112).** Nothing used the plain `ref:refs/heads/main` subject (only `ops.yml`'s diagnose job assumes the role, always in `ops-read`). Needs the owner's **Bootstrap** workflow apply after merge, then one **Ops · Diagnose** run to confirm.
- Origin protection is still IP-range-only (see Open decisions). The origin IP is in git history, so anyone can route to it through their own Cloudflare account; Authenticated Origin Pulls would close that.

## Infrastructure & reliability

- **Revisit ubuntu-24.04 runner pin after Ubuntu 26 images are proven.** All workflows pin `ubuntu-24.04` (release keeps `ubuntu-24.04-arm`) so the 2026-10-19 `ubuntu-latest` move to Ubuntu 26 does not silently change runners; Python 3.12 and Node 22 toolcache availability on 26.04 is unproven.
- **Bootstrap plan shows a no-op `aws_s3_bucket_policy.state` update** (seen on the 2026-10-01 #98 apply: planned "2 to change", applied 1). Most likely policy-JSON normalization; it reappears in every Bootstrap plan. Make the Terraform policy document match AWS's canonical form (e.g. statement order, `jsonencode` vs heredoc), so a plan's change count means something.
- **KEDA (on hold).** **On hold by owner decision (2026-10-02).** Ops · Diagnose on 2026-10-02 04:04 UTC showed only ~274 MiB available, active swap-in, about 4% memory PSI and 304 MiB in swap, below the 512 MiB burst gate. The owner chose to keep KEDA off. Re-propose only if the owner asks or the node is upgraded (e.g. t4g.medium, ~+$12/month). The stress test keeps working in simulated mode.
- **Release pipeline hardening (merged, PR #91, status report `project/status/2026-09-30-2253-release-hardening.md`):** a manual `workflow_dispatch` release run is now refused unless it is on `main` and builds `main`'s current head, so it can no longer mint a higher `build-N` for a stale commit (for refs that contain the check; old refs are gated by the `release` environment policy (owner setting), restricted to `main`); `sync-deploy-branch.yml` now re-fetches, re-merges and retries a rejected push (5 attempts, never forced). The `release_trust` ref pin to `refs/heads/main` is in place (applied through Bootstrap with #98; `infra/CI.md` "Release role trust"). Remaining, not blocking: Flux's own push has no retry beyond its next 1-minute reconcile (acceptable, it recomputes from the new tip); a sync that exhausts its attempts or hits a merge conflict fails the job visibly but nothing re-runs it automatically until the next `main` push. (The release path filter was fixed in #51; migrate-before-api ordering in #47; ingest-after-app in #49.)
- **Release-time memory pressure and stalled chats.** Mitigated by #47 (probe timeouts, `maxSurge: 0`, graceful drain), #49 (ingest after rollout), #48 (client watchdog and Stop) and zram (#55). Still worth a check on the first releases after KEDA returns: probe timeouts, swap in/out, and whether an in-flight SSE request survives a pod replacement.
- **M3 (DD2) remainder (dropped 2026-10-03: AWS alarms cover it):** self-healing node (ASG + Elastic IP reassociation). Scoped in `docs/superpowers/plans/2026-10-01-self-healing-node.md`. On 2026-10-01 the owner chose alarms + daily drive snapshots + a restore runbook instead (PR `infra/alarms-snapshots`, status report `project/status/2026-10-01-1938-alarms-snapshots.md`); phases 2–4 (MySQL dumps to S3, boot script, ASG cutover) are deferred. (Post-deploy streaming check through Cloudflare, DESIGN-002 §7.5/§7.8, done 2026-10-02: `stream-check.yml`, `project/status/2026-10-01-2239-stream-check.md`. Not covered: token-level spacing of a real `/api/ask` answer, skipped because every question spends LLM budget.) (TTFT logging, `queries.ttft_ms`, done 2026-10-02: DESIGN-002 §7.7, `project/status/2026-10-01-2159-ttft-logging.md`.)

## Data pipeline (M4 + deferred ingestion items)

- **Answer cache follow-ups (from the source-validation PR #117):**
  - **Embedding-model rollback (A → B → A):** each switch re-embeds every chunk under new ids, so answers cached under model A miss after a rollback even when the text is unchanged. That is a cold cache, not stale answers. `content_sha` doesn't encode the model, so if a future reindex ever keeps chunk ids across a model switch, only the model tag in the answer key separates the two. Keep the embedding model in `answer_model_id`.
  - **New-document lag:** a new document that would improve an answer it wasn't built from is picked up only when that answer's 24h TTL ends. If that matters, shorten the TTL or add a per-corpus counter that only new documents bump.
  - **Reindex vs. release ingest (minor, from #136 review):** Ops · Reindex refuses until this release's `ingest` Job has completed, but a release that starts while a reindex runs still has its ingest locked out (exit 0) until the next release; a SIGKILLed reindex (past the 30 s grace) leaves `ingest:lock` for its 30-minute TTL. Fix if it ever bites: have the ingest Job wait for the lock instead of exiting.

- **M4 (DD3, dropped 2026-10-03):** Google Drive + Git connectors, S3 raw zone, SQS + DLQ, KEDA ScaledJob ingestion, nightly reconciliation, blue-green re-embedding.

Phase 1a's ingestion script review items (Redis I/O inside the MySQL transaction, the N+1 hash check and per-chunk flush, the unguarded run-failure status write) are fixed in `fix/ingest-robustness` (`project/status/2026-10-01-2157-ingest-robustness.md`). Left from it:

- A MySQL commit failure after a changed document's old Redis keys were deleted leaves that document unsearchable until the next ingest run re-ingests it (its old hash is still in MySQL, so it isn't skipped). The run fails loudly, and the ingest Job's `backoffLimit: 2` retry is usually that next run; acceptable at one ingest per release. If it ever matters, restore the old keys from MySQL in the failure path.

Documented as known limitations directly in code (docstrings) — not yet fixed, not urgent:

- `services/glassbox/ingest/chunkers/terraform.py`: brace-depth counting doesn't strip string literals/comments first, so an unbalanced `{`/`}` inside a Terraform string or comment would throw off block boundaries. No real `.tf` files exist yet (Milestone 2).
- `services/glassbox/ingest/chunkers/code.py`: decorator grouping only handles single-line decorators (a multi-line `@app.get(\n  "/x",\n)` would get orphaned); TypeScript matching doesn't cover typed arrow functions (`const f: Handler = (...) =>`) or generic type parameters. No real `.ts` files exist yet (frontend is a later phase).
- `services/glassbox/ingest/scanner.py`'s secret heuristic now recognizes GitHub, Slack, Anthropic, OpenAI-style, Google API, Stripe live, JWT and `Bearer` tokens (tests: `services/tests/test_scanner_tokens.py`). Still not covered: other providers' formats (Cloudflare API tokens have no distinguishing prefix; only the generic high-entropy assignment rule can catch them).
- Scanner regex performance: the `_ASSIGNMENT` regex was quadratic on long word-character runs (58 s at 80k chars) and the JWT pattern on repeated `eyJ-`; both now start only after a non-word character (tests in `test_scanner_tokens.py` bound 200k-char lines; corpus quarantine set verified identical). Detection is unchanged, including names that start with a digit (`2fa_secret: ...`, checked in the #132 round-2 review). Possible follow-up: timing tests aimed at the lookbehind edge cases (`"a"` or `a: ` repeats; 37 such 1M-char lines were timed by hand at under 0.12 s).

## Open decisions (owner-only, can't be delegated to an agent)

- Whether `warm-answers` should be persistently suspended (see "Open items from the 2026-09-30/10-01 session").
- Judge labelling: tools merged (#162), labelling skipped for now; offline Claude faithfulness review is the option.
- Cloudflare origin protection: Worker-injected secret header vs. IP-range-only — IP-range-only is the current plan; revisit only if abuse becomes a concern.
- Project name: "Glassbox" is a placeholder (DD1 §19).
- Follow-up (budget stop): an "Ops · ..." button to reverse the Bedrock stop, if ever wanted (needs `budgets:ExecuteBudgetAction` and PassRole on the ops role); after the first GPT-6 Luna charge, confirm it bills with billing entity "AWS Marketplace" so `glassbox-bedrock-answers` counts it.
