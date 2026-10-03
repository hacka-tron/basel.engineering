# Chat stream resilience: heartbeats, a Stop button that stops the server, stall recovery, friendly errors (2026-09-30 15:49 PT)

**PR:** [#48](https://github.com/hacka-tron/basel.engineering/pull/48) · **Branch:** `feature/chat-stream-resilience` · **Spec:** `docs/DESIGN-002-followups.md` §6.1–6.3, §7.4, §9.2–9.3 (implementation notes in §6.6 and §7.6)
**Status:** In review (Codex round 1 findings fixed; owner's error-reply request added). Includes DB migration `0004`.

## TL;DR

- The chat can no longer get stuck. If an answer stream stalls (seen during the `build-19` rollout), the browser gives up after 45 s without data, says so in the chat, and frees the input.
- A **Stop** button replaces Send while an answer streams. Pressing it really stops generation on the server within about a second, so no more tokens are paid for. The partial answer stays, marked "Stopped".
- The server sends a `: ping` heartbeat after 15 s of quiet, so proxies (Cloudflare, Traefik) don't cut a slow answer and the browser's watchdog knows the connection is alive.
- Failures now appear as short, light-hearted assistant replies in the conversation instead of a bare "Request failed (…)" banner.
- Smart auto-scroll: new content is followed only while you're at the bottom, with a "Jump to latest" pill otherwise.

## What changed for a visitor

- While an answer streams, the send arrow becomes **Stop** (44 px+ tap target). Stop keeps what was written so far with a muted "Stopped" label, and you can ask the next question straight away. A reply stopped before any text still shows "Stopped" after a reload.
- If the connection dies or hangs, you get a friendly reply like "Oops — looks like something's wrong with the backend. Mind trying that again?" (one of 20, never the same twice in a row). A rate limit says how many seconds to wait. A spent daily budget says so.
- Scrolling up to reread no longer gets yanked down by new tokens. A "Jump to latest" pill takes you back.

## How it works

```mermaid
sequenceDiagram
    participant B as Browser (askQuestion)
    participant W as with_heartbeat (api)
    participant S as _stream generator
    participant L as LLM provider
    B->>W: POST /api/ask
    W->>S: next event (own task, raced vs timer)
    Note over W: 15 s quiet → ": ping"
    W-->>B: events + pings (reset 45 s idle watchdog)
    S->>L: generate (aclosing)
    B--xW: Stop / tab closed (fetch aborted)
    Note over W: Starlette cancels the response,<br/>or the 1 s is_disconnected() poll notices
    W->>S: cancel + close (separate cleanup task)
    S->>L: provider stream closed (Bedrock: close; stream arriving after cancel closed too)
    S->>S: log queries row mode='stopped', no answer-cache write
```

- **Heartbeat** (`services/glassbox/api/sse.py`): the next event is awaited in its own task and raced against a timer. Events are never reordered or dropped, and pings go out only during silence (worker wait, cold model, slow tokens).
- **Server-side stop:** Starlette 0.38 cancels the response on disconnect. As a version-proof fallback, the wrapper also polls `request.is_disconnected()` every second, even while tokens are flowing. Cleanup runs in a separate task so its own awaits (query log, lock release) aren't cancelled too. A Stop during a cold Bedrock call can't interrupt the worker thread, so the call is shielded and whatever stream it returns later is closed as soon as it arrives.
- **Client watchdog** (`frontend/src/lib/idleWatchdog.ts`): pure timer logic, reset by any received bytes. It aborts the fetch and also cancels the reader.
- **Error replies** (`frontend/src/lib/errorReplies.ts`): the failure becomes an assistant message with state `error`. It is saved with the chat but never sent back as `history` and never counted as an answer.

## Key design decisions & trade-offs

- **Wrapper, not per-branch pings.** One generic wrapper covers every slow stage without touching each code path, which keeps event order correct by construction.
- **Stopped answers keep their budget slot.** The prompt and any output were billed, so refunding would let Stop-spam exceed the daily cap.
- **Token counts are measured when possible, estimated otherwise.** Bedrock reports usage only in the stream's final event, which a stopped stream never reaches. So completed Bedrock answers log measured tokens, while stopped answers (and the fake provider) log a word-count estimate. This is documented as such, and billing truth is CloudWatch.
- **Migration 0004** adds `'stopped'` to `queries.mode` (§9.3). It only appends one ENUM value, which MySQL 8 applies in place. With #47's `wait-for-migrations` init container, the new API waits for it before serving.
- **Error replies are persisted as `error`.** This extends the §5.5 storage shape on purpose, so a reload shows the same conversation. They are excluded from history so the model never sees "Oops…" as a prior answer.

## What review caught

| Round | Finding | Resolution |
|---|---|---|
| R1 (Codex) | The disconnect fallback poll only ran when the stream was idle, so a fast token stream starved it | Time-based poll; test relays 5 ms events after a disconnect and asserts a stop within ~0.5 s (old code ran ~10 s) |
| R1 | A Stop during a cold Bedrock call left the late-arriving stream unowned | Shielded call, and the orphaned stream is closed on arrival; stub test where the response returns after the cancel |
| R1 | Stopped `tokens_out` was a word count presented like billed tokens | Measured Bedrock usage when available, estimates labelled |
| R1 | An early Stop lost its "Stopped" state on reload | Empty stopped reply persisted; excluded from history |
| CI | `…consumer_is_cancelled` flaked on the slow runner: depending on timing the source is closed at a yield rather than cancelled | Deterministic gated-source tests, explicit wait on cleanup tasks; 30/30 runs under full CPU load |

## Operational notes & risks

- **Deploy:** needs migration `0004` (handled by the `migrate` Job; API waits via `wait-for-migrations`). Rollback: revert; `0004` has a tested downgrade that maps `stopped` rows to `full` (lossy).
- **Not yet proven through Cloudflare/Traefik in production.** Pings are verified locally (arrived at 15.08 s and 30.08 s via `curl -N`). The §7.5 scripted production check is still a follow-up.
- The 45 s watchdog assumes the server pings at 15 s. Changing one means checking the other.

## How to see it / verify it

- **Locally (fake provider, slowed with a scratch launcher):** `curl -N` a slow answer and see `: ping` every 15 s. Press Stop in the UI and the server log shows generation ending at the visible token. Stop the worker and ask a question: after the 30 s retrieval timeout, a friendly error reply appears.
- **After deploy:** ask a question, press Stop mid-answer, then `SELECT mode, tokens_out FROM queries ORDER BY id DESC LIMIT 5` should show `stopped`. `curl -N` through the public URL should show pings during a slow answer.

## Open items / next steps

- §6.4 input behavior (typing while streaming, send-stops-current, Up-arrow recall) and a Retry button on error replies.
- §7.5 production streaming check through Cloudflare after deploys, and TTFT logging (`ttft_ms`).
