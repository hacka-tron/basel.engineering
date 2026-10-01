# Glassbox architecture deep dive

This document describes how the Glassbox system behind basel.engineering works today, based on the code and manifests in this repository. Each section stands on its own. Every section except the last one describes running, deployed behavior. The last section is the only place that lists work which does not exist yet.

## System overview: what Glassbox is and its components

Glassbox is the chatbot on the basel.engineering portfolio site. A visitor picks one of two corpora, "About Me" (Basel's work history, projects and skills) or "About This System" (this repository's code, Kubernetes manifests, Terraform and design docs), and asks a question. Glassbox answers with retrieval-augmented generation (RAG): it embeds the question, finds the most similar document chunks in a Redis vector index, and has a language model on Amazon Bedrock write a short answer grounded only in those chunks. The answer streams to the browser over Server-Sent Events (SSE), and a live architecture diagram next to the chat lights up each component as the request passes through it.

The whole Glassbox system runs on one small AWS EC2 instance (a `t4g.small` with 2 GiB of memory) running k3s, a lightweight Kubernetes distribution. Cloudflare sits in front of it for DNS, TLS and proxying.

Glassbox components, one line each:

- **Browser frontend**: React 19, TypeScript, Vite, Tailwind CSS and React Flow (`@xyflow/react`). It renders the chat, the architecture diagram and the stress-test button.
- **Edge**: Cloudflare (proxied DNS, TLS) forwards traffic to Traefik, the ingress controller bundled with k3s.
- **API** (`api` Deployment): Python 3.12 and FastAPI. It serves the built frontend and the `/api/*` endpoints, runs the question pipeline, and streams SSE.
- **Retrieval worker** (`retrieval-worker` Deployment): the same container image with a different entrypoint. It consumes jobs from a Redis Stream, runs vector search, and loads chunk text from MySQL.
- **Redis** (`redis/redis-stack-server` 7.2): vector indexes, three cache layers, the job queue (Redis Streams), trace fan-out (pub/sub), rate limits and budget counters.
- **MySQL 8.0**: the source of truth for documents, chunks, embeddings, ingestion runs and the query log.
- **Amazon Bedrock**: Amazon Titan Text Embeddings V2 (512-dimensional vectors) for embeddings and Amazon Nova Lite for answers and follow-up rewrites.
- **Ingest Job**: a Kubernetes Job that scans the corpus files baked into the image, chunks them, embeds changed files and writes MySQL rows and Redis vectors.
- **Migrate Job**: runs `alembic upgrade head` against MySQL for each release.
- **Answer warm-up CronJob** (`warm-answers`): every 2 hours, asks the suggested questions through the API so their answers stay in the semantic answer cache.
- **KEDA**: installed to scale the retrieval worker from 1 to 3 replicas based on Redis Streams backlog. It is currently suspended and scaled to 0 (since the 2026-09-30 memory incident), so no autoscaling happens right now; see "Stress test and KEDA autoscaling".
- **Flux**: GitOps controllers that detect new images in Amazon ECR and apply the Kubernetes manifests.
- **Terraform**: provisions the AWS network, EC2 node, IAM, ECR, SSM parameters, the node's compressed swap (zram) and Cloudflare DNS.
- **GitHub Actions**: runs tests, builds the ARM64 image and pushes it to ECR, and runs the push-button operations runbooks ("Ops · ..." workflows) and the bootstrap pipeline.

## Purpose: why Glassbox exists and why it is built this way

Glassbox exists for two reasons. First, it is Basel's portfolio: a landing page for his work that feels more like a conversation than a list of links. Visitors can ask about his experience, projects and skills, or turn the question around and ask how the site itself works. Every answer is grounded in retrieved sources, and the visitor can see what was retrieved and where the answer came from. The design document's summary puts it as "a RAG portfolio site that shows its work".

Second, Glassbox is a hands-on learning project. Basel built it to learn retrieval-augmented generation, embeddings, language models, and the infrastructure needed to run them, end to end, rather than through another small tutorial. The README says building the whole path from a question to an answer taught him more than a tutorial would have.

The idea that "the machinery is the design" explains why Glassbox looks over-engineered for a portfolio. Next to the chat, a live architecture diagram lights up as each request moves through edge, API, cache, queue, worker, vector search, database and LLM, with real timings and cache hit or miss status. A stress-test button floods the retrieval queue so visitors can watch Kubernetes scale worker pods. The interesting part of a project is often how it works: the decisions, the trade-offs, and the pieces that have to cooperate when someone uses it.

The design goals in `docs/DESIGN.md` make this explicit:

- **G1, first impression**: a recruiter understands the site and gets a good answer within about 10 seconds, on desktop or phone.
- **G2, verifiable depth**: a technical reviewer can confirm genuine use of RAG, MySQL, Redis, Kubernetes, Terraform and AWS, both in the UI and in the repository.
- **G3, no decorative tech**: every component has a job it would plausibly have in a real system, and where something is oversized for current traffic, the docs say so. For example, a job queue is more than this traffic needs; it exists to demonstrate backpressure and queue-driven autoscaling, and it is what makes the stress test real.
- **G4, bounded cost**: hard guardrails on LLM spend, and a tiny single-node AWS footprint.
- **G5, reproducible**: Terraform plus a GitOps sync brings the system up from an empty account.

Non-goals are just as deliberate: no high availability (a single node by design), no user accounts, and no real high traffic. The stress-test load is synthetic. Recruiters get a fast, cited demo; engineers get an inspectable system where every technology on the resume has a real job.

## Request lifecycle, part 1: from the browser to the answer cache

When a visitor asks a question, the browser sends `POST /api/ask` with a JSON body of `question` (1 to 1,000 characters), `corpus` (`about_me` or `about_system`) and an optional `history` of earlier chat turns. The API responds with a `text/event-stream` SSE stream and assigns the request a 26-character ULID `request_id`. The request passes through Cloudflare, then Traefik on the k3s node, then the `api` Service. Cloudflare has a cache rule that bypasses caching for `/api/*`, so the SSE stream is never buffered at the edge.

The API then runs these steps in order, emitting `stage` events so the diagram can follow along:

1. **API start.** The API emits `stage api start` at `t_ms` 0.
2. **Rate limit.** A Redis token bucket keyed by a salted HMAC hash of the client IP allows 10 questions per 10 minutes. A refused request gets an `error` event with code `rate_limited` and `retry_after_s`.
3. **Follow-up rewrite (follow-ups only).** If the request carries history, the kill switch is off, and the daily budget can cover one rewrite unit, the API asks Nova Lite on Bedrock to rewrite the follow-up into a standalone search query (at most 60 output tokens). This shows up as the `rewrite` stage. If the rewrite fails or is skipped, retrieval uses the original question.
4. **Embedding cache.** The API normalizes the retrieval query (collapsed whitespace, case-folded) and looks it up in the Redis embedding cache, keyed by the embedding model ID plus the normalized text. The `embed_cache` stage reports `hit` or `miss`.
5. **Embedding.** On a miss, the API calls Amazon Titan Text Embeddings V2 on Bedrock for a 512-dimensional normalized vector (the `embed` stage) and caches it for 7 days. The API, not the worker, computes the question embedding.
6. **Semantic answer cache (first questions only).** The API searches the Redis answer index for a previous question whose vector has cosine similarity of at least 0.95, scoped to the corpus, the corpus version and a model identity that includes the prompt version. On a hit, the API sends the cached sources as a `retrieval` event, the whole cached answer as one `token` event, and a `done` event with `answer_cache: "hit"`. No queue, worker or LLM work runs, and no daily budget is used. A short Redis lock (15 seconds) stops concurrent identical questions from all calling the LLM: a second request waits up to 3 seconds for the first one's answer before answering on its own.

On an answer-cache miss, the request continues to the retrieval queue, described in "Request lifecycle, part 2".

## Request lifecycle, part 2: queue, worker, vector search and LLM streaming

After a semantic answer-cache miss, the Glassbox API hands retrieval to a separate worker through Redis Streams:

1. **Subscribe, then enqueue.** The API subscribes to the Redis pub/sub channel `trace:{request_id}` first, then adds a job to the Redis Stream `retrieval:jobs` with `XADD` (the stream is trimmed to about 10,000 entries). The job carries the request ID, corpus, retrieval query, request start timestamp, embedding model ID and the question vector packed as 512 float32 values. The `queue` stage wraps this step.
2. **Worker picks up the job.** A `retrieval-worker` pod reads it with `XREADGROUP` in the consumer group `workers`, one message at a time, blocking up to 3 seconds per read. Each pod uses its hostname as its consumer name.
3. **Vector search.** The worker checks the retrieval cache, then runs a KNN query (top 8, cosine distance) on the RediSearch index `idx:chunks`, filtered to the requested corpus and to chunks embedded with the same embedding model. The `vector_search` stage reports cache `hit` or `miss`.
4. **MySQL chunk fetch.** The worker loads chunk text, line ranges, source path and title for the matched IDs, first from the Redis chunk cache and otherwise from MySQL (`chunks` joined to `documents`). This is the `mysql` stage.
5. **Publish results.** The worker publishes stage events and a final `retrieval` event with the ranked chunks to `trace:{request_id}`, then acknowledges the job with `XACK`. The worker acknowledges even when processing fails and publishes an `error` event instead, so a bad job is never retried forever.
6. **API forwards the trace.** The API relays the worker's stage events to the browser and sends a `retrieval` event with each chunk's title, path, score and a 180-character snippet (not the full text). If no worker answers within 30 seconds, the API sends an `error` event.
7. **Guards before the LLM.** If retrieval found no chunks, the answer is "I don't know from what I have." with no LLM call. If the Redis kill switch is on, or the daily budget is used up, the request ends in `retrieval_only` mode: sources, but no generated answer.
8. **LLM streaming.** The API builds a prompt from the numbered chunks and streams Amazon Nova Lite's answer through the Bedrock ConverseStream API (at most 400 output tokens, temperature 0.2). Each text delta becomes a `token` event, wrapped in the `llm` stage.
9. **Finish.** For first questions the answer is written to the semantic answer cache, unless ingestion changed the corpus version mid-request or the answer is an abstention or empty. The query is logged to MySQL's `queries` table, and a `done` event reports `total_ms`, `mode`, `answer_cache` and token counts.

## SSE event contract and trace sequencing

The `POST /api/ask` response is a Server-Sent Events stream with five event types:

- **`stage`**: `request_id`, `seq`, `node`, `status` (`start` or `end`), `t_ms`, and optionally `duration_ms` and `cache` (`hit` or `miss`). The `node` values match the diagram: `api`, `rewrite`, `embed_cache`, `embed`, `answer_cache`, `queue`, `vector_search`, `mysql` and `llm`. The diagram also shows `edge` and `worker` nodes; the worker's own work appears as the `vector_search` and `mysql` stages.
- **`retrieval`**: the ranked chunks (number, chunk ID, source path, title, similarity score, line range and snippet). For a rewritten follow-up it also carries `rewritten_query`.
- **`token`**: a piece of answer text. A semantic answer-cache hit sends the whole answer as one token.
- **`done`**: `total_ms`, `mode` (`full` or `retrieval_only`), `answer_cache` (`hit` or `miss`), `tokens_in` and `tokens_out`. Completed Bedrock answers report measured token usage from the stream's final metadata event. Otherwise (the fake provider, or a stopped answer) the counts are word-count estimates.
- **`error`**: `code` (`rate_limited` or `internal`), `message`, and `retry_after_s` for rate limits.

The API and the retrieval worker both produce stage events for one request, so they share a Redis sequencing convention. Each event's `seq` comes from `INCR seq:{request_id}`, and the key's TTL is refreshed to 300 seconds after every increment. `t_ms` is milliseconds since the request started: the API writes `request_start_ts` (epoch milliseconds) into the queue job, and the worker computes `t_ms` from it. Before enqueueing, the API reserves the sequence number for its own `queue end` event, so a fast worker can never get a smaller sequence number than the queue step that preceded it.

The worker never talks to the browser. It publishes JSON events to the Redis pub/sub channel `trace:{request_id}`, and the API validates each one (the request ID must match, and the event must parse against a strict schema) before forwarding it as an SSE frame. Responses set `Cache-Control: no-cache` and `X-Accel-Buffering: no` so proxies do not buffer the stream. Because `EventSource` cannot send a POST body, the frontend (`frontend/src/lib/sse.ts`) uses `fetch` with a `ReadableStream` reader and its own SSE parser. That parser keeps partial frames and partial UTF-8 characters between reads, and treats a stream that ends without `done` or `error` as a failure.

The `/api/ask` response is wrapped in a heartbeat relay (`with_heartbeat` in `services/glassbox/api/sse.py`). After 15 seconds with nothing sent, it emits an SSE comment line, `: ping`, so Cloudflare and Traefik do not close a quiet stream. The browser runs a 45-second idle watchdog, reset by any received bytes: if the stream goes silent that long, it aborts and shows a friendly error reply. When the visitor presses **Stop**, or closes the tab, the API notices the disconnect (Starlette cancels the response, and the relay also polls for a disconnect every second) and cancels generation, including closing the Bedrock stream, so no more tokens are paid for. A stopped answer keeps its daily-budget slot, is never cached, and is logged with `mode = "stopped"`.

## Live architecture diagram: how the frontend lights up nodes

The Glassbox architecture diagram is a React Flow graph defined as data in `frontend/src/architecture.ts`. It has 11 nodes (Edge, API, Rewrite, Answer Cache, Queue, Worker, Embed Cache, Embed, Vector Search, MySQL and LLM) and 11 edges that follow the request path. Each node carries a visitor-facing label, its concrete implementation (for example "Redis Streams" for Queue or "Amazon Titan Text Embeddings V2" for Embed), and a one-line description.

Trace events drive the diagram. When a `stage` event with `status: start` arrives, the frontend marks that node active, filling the whole tile with cyan, the one accent color reserved for "active". When the matching `end` event arrives, the node goes back to idle. Any `cache` field on a stage event is shown on that node as `hit` or `miss`, so a visitor can see, for example, an embedding-cache hit followed by an answer-cache miss. A list of retrieved chunks shows each source's title, path and similarity score. A stats bar shows real numbers for the last request: latency to the first token (or total time), the answer-cache status and output tokens. It does not show invented rolling averages.

The nodes are also interactive. Hovering or keyboard-focusing a node shows its description and implementation without making a model request. Selecting a node switches to the About This System corpus and asks a component-specific question, such as "How does the Queue component (Redis Streams) work in the current Glassbox system?" That question is sent without chat history so it stays eligible for the semantic answer cache. If an answer is still streaming, the component question waits until it finishes.

On phones (below the 768 px breakpoint) the chat takes the full width. The row above the ask box shows a compact strip of live stage dots and a Chat | Diagram switch. Choosing Diagram replaces the messages in place with the full diagram in a two-column portrait layout and a capped, collapsible details panel; browser Back or Escape returns to the chat. Until a request runs, the status text in that row reads "Select a component", and tapping a node asks about it just as on desktop. The phone header is a single row: the name, an envelope icon that copies Basel's email address ("Copy email"; it confirms "Email copied", or shows the address if copying fails), and the GitHub icon. The topic choice sits as a pair of chips ("Asking about" Basel or System) directly above the ask box, in the Chat view only; the Diagram view removes them to give the diagram more height, and a question typed there still goes to the current topic. Every tap on the stress-test control also switches to the Diagram view so the visitor sees the pods react. While the ask box has focus, the header and footer slide out of the way (focus mode) to leave room for the on-screen keyboard. The footer keeps the last request's latency as a bare millisecond value, a "+" New chat icon and the tiger or bunny capacity icon, each with a tooltip on long-press. The header shows the full name "Basel Abdel-Rahman" when it fits and the short form "Basel A-R" when it would crowd the icons (only on screens narrower than about 300 px). Chat messages and the ask box both use 13 px text; on iOS only, the page adds `maximum-scale=1` to the viewport so focusing the ask box does not zoom the page (pinch-zoom still works).

Only one React Flow instance is mounted at a time. The diagram declares fixed node dimensions and connection-handle positions so nodes and arrows stay visible during rapid trace updates, and it refits itself whenever its container is resized (a `ResizeObserver`), never on trace updates.

The worker node also shows live Kubernetes state during a stress test: one dot per `retrieval-worker` pod (filled when Ready) and a queue backlog counter. That data comes from a separate SSE endpoint, described in "Stress test and KEDA autoscaling".

## Caching layers: embedding, retrieval, chunk and semantic answer caches

Glassbox has four Redis caches. Every cache key includes whatever could make its value stale: the model identity for vectors and answers, and the corpus version for anything that depends on indexed content.

1. **Embedding cache** (`emb:{sha256}`): an exact-match cache of question vectors. The key hashes the embedding model ID together with the normalized question (whitespace collapsed, case-folded). Values are 512 packed float32 values with a 7-day TTL. A hit skips the Titan embedding call on Bedrock. For follow-ups, the key uses the rewritten standalone query.
2. **Semantic answer cache** (`idx:answers` over `ans:{corpus}:v{version}:{id}` hashes): a RediSearch HNSW vector index of previous question vectors, each stored with its answer and cited chunks for 24 hours. A lookup is a KNN-1 search filtered by corpus, corpus version and a hashed model tag, and it counts as a hit only at cosine similarity 0.95 or higher. The model tag hashes `embedding model | LLM model | prompt version`, so changing either model or the prompt wording (the prompt version) makes old answers unreachable. A hit skips the queue, the worker and the LLM entirely and uses no daily budget.
3. **Retrieval cache** (`ret:{corpus}:v{version}:{sha256}`): the ranked chunk IDs and scores for a given query vector, keyed by a hash of the embedding model ID and the packed vector, with a 1-hour TTL. The worker checks it before running KNN search.
4. **Chunk cache** (`chunktxt:{chunk_id}`): JSON with each chunk's text, source path, title and line range, with a 1-day TTL. The worker uses it only when every matched chunk is cached; otherwise it reads all of them from MySQL.

The order on a first question is: embedding cache, then (on a miss) Bedrock embedding, then the semantic answer cache, then the queue and worker with the retrieval and chunk caches. The embedding comes first because the semantic answer cache needs the question vector to search.

Invalidation uses versioned keys instead of scan-and-delete. The integer `corpus:ver:{corpus}` is part of every answer-cache and retrieval-cache key. The ingest Job increments it after it commits a changed document, so older entries stop being read and simply expire.

**Keeping suggested answers warm.** The suggested-question chips are the questions visitors ask most, so a Kubernetes CronJob, `warm-answers` (`k8s/base/warm-cronjob.yaml`), runs `python -m services.glassbox.warm` every 2 hours (at minute 17, UTC) and once more at the end of each release's ingest Job. It asks each suggested question through the `api` Service exactly like a visitor's first question. A cached answer comes back as a hit and costs nothing; only an answer that expired or was invalidated by a corpus change is regenerated, at most one per question per run and at most 10 generated answers per UTC day across all runs (`GLASSBOX_WARM_DAILY_LLM_CAP`, counted in Redis). A run stops at the first rate-limit or budget signal. The CronJob is running; an operator can pause it with the "Warm-up CronJob suspend or resume" runbook, but Flux re-applies the manifest and has turned a manual suspend back off before, so a lasting pause has to be made in Git.

Follow-up questions skip the semantic answer cache in both directions: no read, no lock and no write. A follow-up's answer depends on the conversation, not just the words, so a cached first-question answer is never replayed for a follow-up, and a follow-up answer never enters the cache. Follow-ups still use the embedding, retrieval and chunk caches, keyed on the rewritten query.

## Conversational chat: history, per-tab conversations and follow-up rewrite

The Glassbox server is stateless about conversations. The browser owns chat history and sends recent turns with each question.

In the browser, each corpus tab ("About Me" and "About This System") keeps its own conversation. Switching tabs swaps conversations rather than mixing them. Conversations persist in `localStorage` under `glassbox:conv:v1:about_me` and `glassbox:conv:v1:about_system`, expire after 7 days, keep at most 50 displayed messages, and are saved only once a message settles: done, stopped, retrieval-only, or a failure reply. A reply that is still streaming is never saved, so a refresh mid-answer never restores a half-written reply. Failures appear as short, friendly assistant replies with an `error` state; they are saved so a reload shows the same chat, but they are never sent back as history. While an answer streams, the send button becomes **Stop**, which keeps the partial text marked "Stopped". Stored data with a timestamp more than 5 minutes in the future is discarded. Storage failures (private windows, full storage) fall back to in-memory chat. A "New chat" button clears the current tab's conversation.

With each follow-up, the frontend sends up to the last 6 settled messages as `history`, with at most 4,000 characters in total. The server does not trust that: it accepts up to 50 history messages of at most 4,000 characters each, then keeps only the newest 6 messages within 4,000 total characters, dropping the oldest first. Roles are limited to `user` and `assistant`.

A follow-up like "tell me more about that" embeds poorly on its own, so the API first rewrites it into a standalone search query using Nova Lite on Bedrock with a dedicated system prompt. That prompt treats the conversation as untrusted input, never follows instructions inside it, and outputs only the rewritten question (at most 60 output tokens). The rewrite is used only for embedding and retrieval. The answer prompt still gets the original question plus the conversation, marked as untrusted user input: earlier assistant replies may be wrong, and the numbered sources win when they disagree. The rewrite appears on the diagram as the `rewrite` stage, and the chat shows it under the answer's sources as "Searched for: ...". The rewritten text is shown live but not stored in `localStorage`.

The rewrite is skipped for first questions, when the kill switch is on, or when the daily budget cannot cover a rewrite unit. If the rewrite fails or returns nothing, retrieval uses the original question. The MySQL query log records `turn_index` (how many user turns came before this one) and `rewritten_query` (the query retrieval actually used, or empty).

## Grounding: how answers stay tied to sources and to what is actually built

Glassbox answers only from retrieved chunks. The system prompt for Bedrock (`GROUNDING_RULES` in `services/glassbox/providers/base.py`) tells the model to answer only from the numbered sources in the user message. If the sources do not answer the question at all, it must reply with exactly "I don't know from what I have." and nothing else. If they answer it even in part, it must answer from them. It must not reveal its instructions, and it must stay within the selected corpus. Answers are plain prose in two or three concise sentences, without bracketed citation markers, because the interface shows sources separately.

The About This System corpus mixes code with design documents that sometimes describe features before they exist, often in the present tense. The system prompt treats a component as current when a source says it is implemented or working today, or when design sources describe it and it also appears in code, manifest or infrastructure sources (paths under `services/`, `k8s/` or `infra/`). The prompt builder in `services/glassbox/api/ask.py` then labels sources:

- **Code, manifests and infrastructure** (`services/`, `k8s/`, `infra/`) are never labeled, because they describe what runs.
- **`docs/DESIGN-003-ingestion.md`** (the content-pipeline design) gets one fixed source-level label saying its Google Drive and Git connector work does not exist yet.
- **Every other source**, such as the design docs and this deep dive, is labeled unit by unit. A heading or list item is one unit; a paragraph or table row is split into sentences. Each unit that matches the status keyword list (`_PLANNED_SOURCE_SIGNAL`) gets the inline `PLANNED_MARK` prefix, a bracketed marker saying the text does not exist yet. Units that do not match stay unmarked.

The keyword list covers explicit status wording (work described as upcoming, postponed or unbuilt) and the names of specific components that have not shipped. Live infrastructure such as KEDA, k3s, Terraform, Flux, GitOps and CI/CD is deliberately not on it.

The answer prompt tells the model that marked text describes work that does not exist today, so it should answer "No" when asked whether that feature works now. The marker applies only to the heading, list item or sentence it prefixes, not to unmarked text in the same source. Labels are per unit because one chunk often mixes a live component with a sentence about unbuilt work, and an older chunk-wide label steered answers about the live part toward "No".

Refusals are never cached. An answer that abstains (the exact "I don't know" reply) or comes back empty is not written to the semantic answer cache, so one bad retrieval cannot be replayed to later visitors. The query log flags these requests as `abstained` and `answer_cache_skipped` in its stage timings. The prompt version is part of the answer-cache key, so changing these rules makes every older cached answer unreachable.

This deep dive keeps all unbuilt work in its final section, so only that section's sentences carry the marker.

## Rate limiting, daily LLM budget and the kill switch

Glassbox is a public site that calls paid models, so every generated answer passes through cost controls implemented as atomic Redis Lua scripts (one `EVAL` each, so there is no read-then-write race between API requests).

**Per-IP rate limit.** Each visitor gets a token bucket of 10 questions that refills over 10 minutes, stored in `rl:{client_hash}`. The client hash is an HMAC-SHA256 of the client IP with a secret salt. The salt is generated by Terraform, stored as an SSM SecureString parameter, and mounted into the `api` pod as a Kubernetes Secret, so raw visitor IPs are never stored or logged. The API trusts the first `X-Forwarded-For` address only when the direct peer is inside the configured trusted proxy range (the k3s pod network where Traefik runs); otherwise it uses the TCP peer address. When the bucket is empty, the SSE stream sends an `error` event with code `rate_limited` and `retry_after_s`, and the frontend shows "Try again in N seconds."

**Daily LLM budget.** A UTC-day cap on generated answers defaults to 100 (`GLASSBOX_DAILY_LLM_CAP`). The budget is counted in quarter-units: a generated answer costs 4 units and a follow-up rewrite costs 1. Whole answers are counted one per answer in `budget:llm:{date}` and rewrites in `budget:llm:rw:{date}`. One script checks `4 × answers + rewrites + requested units` against `4 × cap` before incrementing, and both keys expire after 2 days. Semantic answer-cache hits use no budget. When the budget is exhausted, the request still runs retrieval and ends with `done.mode = "retrieval_only"`. The visitor sees the retrieved sources and the message "Sources retrieved — no generated answer for this request." A follow-up whose rewrite cannot be afforded falls back to retrieving with the original question.

**Kill switch.** Setting the Redis key `glassbox:kill:disable_llm` to `1` turns off all LLM calls immediately without a deploy: no rewrites and no answers. Every request becomes retrieval-only until the key is cleared. It exists for incidents such as runaway cost or bad model output.

**Other limits.** Questions are capped at 1,000 characters, history at 50 messages of 4,000 characters (6 messages and 4,000 characters actually used), answers at 400 output tokens and rewrites at 60. At startup the API validates provider configuration: an unknown provider mode, a non-positive daily cap or a malformed Bedrock model ID stops the app from starting rather than failing on the first request. The IAM instance role can invoke only the Titan embedding model and the specific Nova Lite and Claude Haiku inference profiles.

## Data stores: MySQL tables and what they hold

MySQL 8.0 is the Glassbox source of truth. It runs in the Kubernetes cluster as the `mysql` StatefulSet in the `data` namespace, with a 4 GiB persistent volume on the node's `local-path` storage. It is not Amazon RDS. Alembic migrations in `services/glassbox/db/migrations` define the schema (revisions `0001_initial_schema`, `0002_add_queries_table`, `0003_query_turn_columns` and `0004_query_mode_stopped`), and SQLAlchemy models live in `services/glassbox/db/models.py`.

MySQL tables:

- **`documents`**: one row per ingested file, unique on (`corpus`, `source_path`). It stores the corpus (`about_me` or `about_system`), the source path, a title taken from the first Markdown heading (or the file name), a SHA-256 `content_hash` of the file, and `updated_at`.
- **`chunks`**: one row per chunk, unique on (`document_id`, `ordinal`), and deleted with its document. It stores the chunk text, `start_line` and `end_line`, a word-based `token_count`, the embedding as a BLOB of packed float32 values, and `embedding_model`, the ID of the model that produced the vector. Storing the model ID per chunk is what lets ingestion re-embed when the model changes.
- **`ingestion_runs`**: one row per ingest run with start and finish times, `docs_changed`, `chunks_written` and a status of `running`, `succeeded` or `failed`.
- **`queries`**: the query log, one row per `/api/ask` request. It records the request ID, corpus, question, `cache_status` (`answer_hit` or `miss`), `mode` (`full`, `retrieval_only` or `stopped`), the retrieved chunk IDs, per-stage timings in milliseconds (JSON, including `abstained` and `answer_cache_skipped` flags), total milliseconds, tokens in and out (measured for completed Bedrock answers, estimated otherwise), `turn_index`, `rewritten_query` and `created_at`. It stores no IP addresses. Writing the query log is best-effort: a failed insert is logged and dropped, so it never turns a generated answer into an error.

Vector search does not happen in MySQL. MySQL keeps the embeddings so the data is complete and auditable, while similarity search runs in Redis. `documents` and `chunks` are a derived index of content checked into the Git repository, so losing the MySQL volume means re-running ingestion rather than losing data. The `queries` table is the only data that cannot be rebuilt, and it is statistics rather than core functionality.

MySQL is tuned for the 2 GiB node: `innodb_buffer_pool_size` of 96 MB, `performance_schema` off, `max_connections` 20, and a small table cache. The pod has a 350 MiB memory limit. The api, retrieval-worker, migrate and ingest pods get the database password from a Kubernetes Secret, which a bootstrap script fills from AWS SSM Parameter Store.

## Data stores: Redis keys, streams, channels and vector indexes

Redis in Glassbox is `redis/redis-stack-server` 7.2, which includes RediSearch vector search. It runs as the `redis` StatefulSet in the `data` namespace with a 1 GiB volume and a 150 MiB memory limit. One small in-memory store does many jobs on purpose, instead of running five separate services.

Redis structures and keys:

- **`idx:chunks` over `chunk:{id}` hashes**: the retrieval vector index (HNSW, cosine distance, 512 dimensions, float32). Each hash holds `corpus`, a hashed `model` tag, the `vector`, `source_path` and `document_id`. Searches filter on corpus and model tag, so vectors from different embedding models are never mixed.
- **`idx:answers` over `ans:{corpus}:v{version}:{id}` hashes**: the semantic answer cache index, also HNSW, cosine and 512 dimensions, with `corpus`, `version` and `model` tags and a JSON payload. 24-hour TTL.
- **`emb:{sha256}`**: the embedding cache. 7-day TTL.
- **`ret:{corpus}:v{version}:{sha256}`**: the retrieval cache. 1-hour TTL.
- **`chunktxt:{chunk_id}`**: the chunk text cache. 1-day TTL.
- **`corpus:ver:{corpus}`**: the corpus version counter used for cache invalidation.
- **`retrieval:jobs`**: the Redis Stream job queue, with consumer group `workers`, trimmed to about 10,000 entries. KEDA reads this group's lag to autoscale workers.
- **`trace:{request_id}`**: the pub/sub channel carrying worker trace events back to the API.
- **`seq:{request_id}`**: the shared trace sequence counter, refreshed to 5 minutes on each event.
- **`lock:answer:{sha256}`**: a 15-second lock that stops duplicate LLM calls for concurrent identical first questions.
- **`rl:{client_hash}`**: the per-IP token bucket.
- **`budget:llm:{date}` and `budget:llm:rw:{date}`**: daily answer and rewrite budget counters. 2-day TTL.
- **`glassbox:kill:disable_llm`**: the LLM kill switch.
- **`demo:load:lock`**: the global stress-test cooldown lock. 5-minute TTL.
- **`idx:chunks:model-tags-ready`**: a marker recording that existing chunk hashes carry model tags.

Most of Redis can be recreated: chunk vectors come from MySQL and ingestion, caches refill on demand, and locks are short-lived. The exception is the day's rate-limit and budget counters, which live only in Redis, so losing Redis resets the spent daily budget to zero.

## Ingestion pipeline: how content gets into the RAG index

The Glassbox ingestion pipeline (`services/glassbox/ingest/run.py`) turns files in the repository into searchable chunks. It runs as the Kubernetes Job `ingest` using the same container image as the app. The Dockerfile copies `corpus/`, `docs/`, `infra/`, `k8s/` and `services/` into the image, so each release ingests the content snapshot of that build, and the cluster needs no Git credentials.

**When ingestion runs.** Flux recreates the `ingest` Job for each new image tag, but only after the new `api` and `retrieval-worker` Deployments have finished rolling out and are Ready. A dedicated `app-ready` Flux Kustomization enforces that order, which keeps ingestion out of the rollout's memory peak on the 2 GiB node.

**What gets ingested.** The scanner (`services/glassbox/ingest/scanner.py`) builds two corpora:

- **`about_me`**: every Markdown file under `corpus/about-me/`. Any leading `---` front matter is stripped before chunking.
- **`about_system`**: files under `infra/`, `k8s/`, `services/` and `docs/` with the extensions `.md`, `.tf`, `.yml`, `.yaml`, `.py`, `.ts` or `.tsx`. Frontend code and GitHub workflow files are outside these directories and are not part of the corpus.

**Incremental re-embedding.** For each file, ingestion computes a SHA-256 content hash. It skips the file only when the stored `content_hash` matches and every existing chunk was embedded with the currently configured embedding model. A changed file, or a change of embedding model, triggers re-chunking and re-embedding. In one MySQL transaction, ingestion replaces the document's chunk rows. In one Redis transaction pipeline, it deletes the old `chunk:{id}` hashes and writes new ones tagged with corpus and model. It then increments `corpus:ver:{corpus}` so caches for that corpus stop being read. A typical release therefore embeds only the documents that changed.

**Run bookkeeping.** Each run writes an `ingestion_runs` row with its status and counts, and prints `docs_changed`, `chunks_written` and every skipped file with its reason. A file that fails chunking or embedding is skipped and reported, and the run continues. Files deleted from the repository are not pruned from the index by this job.

**Model tagging.** Every chunk hash in Redis carries a hashed embedding-model tag. If the index predates model tags, ingestion adds the tag field and backfills existing hashes from MySQL. Untagged vectors stay invisible to search in the meantime, so switching embedding models never mixes vector spaces.

## Ingestion safety and chunking: secret filtering and chunkers

The About This System corpus is the Glassbox repository itself, and its chunks are shown to the public, so the scanner filters files before anything is embedded.

**Path denylist.** Any path with a component named `secrets`, starting with `secrets.` or `.env`, ending in `.tfvars`, or containing `.tfstate` is refused, whatever its extension. The check is case-insensitive.

**Content secret heuristics.** Each file is scanned line by line and quarantined (skipped and reported, never embedded) if a line contains:

- an AWS access key ID pattern (`AKIA` or `ASIA` followed by 16 characters),
- a PEM private key header, or
- an assignment (`name = value` or `name: value`) whose value is 33 or more characters from a base64-like alphabet and has a Shannon entropy of at least 4 bits per character. The report reason is "possible high-entropy assigned value".

A quarantined file does not stop the run. Other files continue, and the skipped path and reason are printed. Symlinks and non-UTF-8 files are skipped as well. Secrets such as the MySQL password, the IP-hash salt and the Cloudflare API token never live in the repository: they sit in SSM Parameter Store, Kubernetes Secrets or GitHub environment secrets.

**Chunkers.** A chunker is picked by file extension. Every chunk keeps its source path and start and end line numbers.

- **Markdown** (`chunkers/markdown.py`): splits on headings (`#` through `######`). Sections under 300 words merge with the following sections as long as the total stays at or below 500 words. A section over 500 words is split into roughly 450-word windows that overlap by 50 words. Sizes are counted in whitespace-separated words, not model tokens.
- **Terraform** (`chunkers/terraform.py`): one chunk per top-level block (`resource`, `module`, `variable`, `data`, `output`, `provider`, `locals`, `terraform`), found by brace counting.
- **YAML** (`chunkers/yaml_doc.py`): one chunk per YAML document, split on `---`.
- **Python and TypeScript** (`chunkers/code.py`): a leading chunk for imports and module docstrings, then one chunk per top-level function, class, or exported function or arrow-function constant, with single-line decorators kept with their definition.

Embeddings come from the same provider interface the API uses. In production that is Titan Text Embeddings V2 through Bedrock (512 dimensions, normalized). Locally and in tests it is a deterministic fake provider that makes no network calls.

## Stress test and KEDA autoscaling of retrieval workers

The Glassbox stress-test button is built to show queue-driven autoscaling live. It floods the Redis Stream `retrieval:jobs` with synthetic work so KEDA scales the `retrieval-worker` Deployment from 1 to 3 pods and back.

**Current status: KEDA is suspended.** KEDA is installed, but since the 2026-09-30 memory incident (see "Operations: node memory, runbooks and the bootstrap pipeline") its Flux Kustomizations and HelmRelease are suspended and its Deployments in the `keda` namespace are scaled to 0, which frees roughly 150 MiB. While KEDA is off, nothing autoscales: the retrieval worker stays at its single replica, and the KEDA HelmRelease still shows the failed state left by the incident's install timeout. The capacity gate below still runs on every check. With the node's free memory under its 512 MiB threshold since the incident, the button has been playing the simulated burst rather than a real one. KEDA comes back through the runbooks (resume the HelmRelease and the KEDA Kustomizations, then switch KEDA on) once the node shows steady headroom.

**Capacity gate.** The node has only 2 GiB of memory, so a real burst runs only when the node has room. `GET /api/demo/capacity` uses the api ServiceAccount to list nodes and read live node memory usage from metrics-server (bundled with k3s). It approves a burst only when there is exactly one node, its `MemoryPressure` condition is `False`, and live free memory (allocatable minus current usage) is at least 512 MiB: two extra workers at their 128 MiB limit plus a 256 MiB safety margin. Anything it cannot confirm counts as a denial. The page shows a tiger icon when a real burst is possible and a bunny when it is not.

**Real burst.** `POST /api/demo/load` rechecks capacity, then takes the global Redis lock `demo:load:lock` with `SET NX EX 300`. That is a 5-minute cooldown shared by every visitor. The endpoint then enqueues 300 synthetic jobs. A synthetic job carries `synthetic=1` and a 200 ms delay. The worker just sleeps for the delay and acknowledges the job: no embedding, no Bedrock call, no MySQL query, and nothing published to any trace channel. Stress tests therefore cost nothing in model spend.

**Simulated burst.** If capacity is insufficient, the cooldown is active, or the cluster view is unavailable (for example in local development), a click plays a visual-only simulation instead: pod dots grow from 1 to 3 and shrink back while a backlog counter falls from 300. No jobs are queued. Every click also triggers a short screen-shake effect (skipped when the visitor prefers reduced motion), and the button has a 9-second client-side cooldown.

**KEDA scaling (when KEDA is on).** KEDA 2.21 is installed by a Flux HelmRelease. A `ScaledObject` named `retrieval-worker` uses the `redis-streams` trigger on stream `retrieval:jobs` and consumer group `workers`, with `lagCount: 10` (target backlog per replica), a 5-second polling interval, `minReplicaCount: 1` and `maxReplicaCount: 3`. KEDA drives a Horizontal Pod Autoscaler. Scale-down uses a 45-second stabilization window and may remove all extra replicas every 15 seconds, so workers return to 1 about a minute after the backlog drains. The worker Deployment deliberately has no fixed `replicas` field, so Flux and the HPA never fight over it.

**Live cluster view.** `GET /api/cluster/stream` is an SSE endpoint that lists and watches pods labeled `app=retrieval-worker` in the `app` namespace, and polls the consumer group's lag (the same metric KEDA uses) every 2 seconds. It forwards only pod name, phase and readiness, plus the backlog number. The browser subscribes with native `EventSource`. When there is no in-cluster Kubernetes access, the endpoint sends `cluster_unavailable` and the diagram falls back to the plain worker node.

## Kubernetes on k3s: namespaces, workloads and resource budget

Glassbox runs on k3s, a certified Kubernetes distribution, installed as a single-node server on the EC2 instance. The kubelet runs with `fail-swap-on=false` because the node uses swap: compressed RAM swap (zram) first, and a 1 GiB swap file on disk as overflow. Kubernetes manifests live in `k8s/base` (all long-running workloads plus the migrate Job) and `k8s/overlays/prod` (image tag pinning, Flux objects, KEDA and the ingest Job).

Namespaces and workloads:

- **`app`**: `api` (Deployment, 1 replica, container port 8000, requests 100m CPU and 120 MiB, limit 256 MiB), `retrieval-worker` (Deployment, 1 to 3 replicas managed by KEDA, requests 50m and 64 MiB, limit 128 MiB), `migrate` (Job running `alembic upgrade head`, limit 192 MiB), `ingest` (Job, limit 384 MiB), and `warm-answers` (CronJob every 2 hours, request 32 MiB, limit 48 MiB).
- **`data`**: `mysql` (StatefulSet, MySQL 8.0, 4 GiB volume, requests 200 MiB, limit 350 MiB) and `redis` (StatefulSet, redis-stack-server 7.2, 1 GiB volume, limit 150 MiB), each with a headless Service and a ClusterIP Service.
- **`keda`**: the KEDA operator, metrics server and admission webhooks, capped at 150 MiB, 100 MiB and 64 MiB so KEDA fits its share of the node. These Deployments are currently scaled to 0 (KEDA is suspended).
- **`flux-system`**: the Flux controllers, including the source, kustomize, helm, image-reflector and image-automation controllers.
- **`kube-system`**: k3s defaults, including Traefik, CoreDNS and metrics-server.

Traffic enters through two Traefik Ingress objects for host `basel.engineering`, one on the `web` (HTTP) entrypoint and one on `websecure` (HTTPS). Both route to the `api` Service on port 80, which targets the pod's port 8000. The HTTPS route uses Traefik's default self-signed certificate, so Cloudflare's SSL mode is "Full" rather than "Full (strict)". No cloud load balancer is used. The FastAPI app serves both the API and the static frontend (`frontend/dist`, mounted at `/`), so the site and API share one origin.

Configuration comes from the `glassbox-config` ConfigMap: MySQL host and database, the Redis URL, `GLASSBOX_PROVIDER=bedrock`, the AWS region, the Bedrock model IDs (Titan Text Embeddings V2 and the Nova Lite US inference profile), the daily LLM cap and the trusted proxy range. Secrets (`glassbox-mysql` and `glassbox-app`) are created on the node by `k8s/bootstrap-secrets.sh`, which reads SSM Parameter Store through the instance role. Images are pulled from private Amazon ECR with an image pull secret (`regcred`). A systemd timer on the node refreshes that secret every 6 hours, because ECR tokens expire after 12.

Memory is the binding constraint on the 2 GiB `t4g.small` (about 1.84 GiB allocatable to pods). The design budget is roughly 500 to 600 MiB for k3s itself, 100 MiB for Traefik, CoreDNS and metrics-server, about 150 MiB each for KEDA and Flux, 60 to 100 MiB for Redis, 200 to 350 MiB for MySQL, about 120 MiB for the API, and up to 384 MiB for three workers at peak. Measurements on the live node showed it already using swap before any burst. That is why rollouts never add extra pods, why ingestion waits until the rollout finishes, and why autoscaling stops at 3 workers. On 2026-09-30 the node ran out of headroom and thrashed, which led to compressed swap and to suspending KEDA; "Operations: node memory, runbooks and the bootstrap pipeline" covers the incident and the numbers afterwards.

## Kubernetes security: RBAC, NetworkPolicies and node access

**RBAC for the cluster view.** The `api` Deployment runs as the `api` ServiceAccount, which has two narrow grants. A namespaced Role, `pod-viewer`, allows `get`, `list` and `watch` on pods in the `app` namespace only; `GET /api/cluster/stream` uses it to watch retrieval-worker pods. A ClusterRole, `glassbox-node-reader`, allows only `list` on `nodes` and on `nodes` in the `metrics.k8s.io` API group; `GET /api/demo/capacity` uses it to read node allocatable memory, the `MemoryPressure` condition and live memory usage. Nothing else about pods or nodes leaves the cluster. The endpoints forward only pod name, phase, readiness and a capacity verdict. No workload can create, modify or delete Kubernetes objects.

**NetworkPolicies.** k3s ships an embedded NetworkPolicy controller, and Glassbox uses it for both namespaces:

- In `app`, a `default-deny-ingress` policy blocks all inbound traffic to every pod. A second policy, `api-from-traefik`, lets only Traefik pods from `kube-system` reach `api` on TCP 8000.
- In `data`, `mysql-from-app` accepts TCP 3306 only from `app` pods labeled `api`, `retrieval-worker`, `migrate` or `ingest`.
- In `data`, `redis-from-app` accepts TCP 6379 only from `app` pods labeled `api`, `retrieval-worker` or `ingest`, plus the `keda` namespace, because KEDA's redis-streams scaler polls Redis directly from its operator pod.

**Node access.** The EC2 node has no SSH port open and no public Kubernetes API. Administration happens only through AWS Systems Manager (SSM), which the instance role enables with the `AmazonSSMManagedInstanceCore` policy: routine operations run as fixed SSM documents from the approval-gated runbook workflows, and an interactive Session Manager session remains for anything those do not cover. The security group accepts only TCP 80 and 443, and only from Cloudflare's published IPv4 and IPv6 ranges, so the origin cannot be reached directly. Instance metadata requires IMDSv2 tokens with a hop limit of 2, so containerized Flux controllers can use the node's IAM role to read ECR.

**Deploy credentials.** Deployment is pull-based: Flux inside the cluster pulls from GitHub and ECR, so continuous integration never needs cluster credentials. GitHub Actions reaches AWS only through OIDC federation into narrowly scoped IAM roles, with no long-lived access keys. Workloads call Bedrock through the node's instance role, which allows invoking only the specific Titan embedding model and the Nova Lite and Claude Haiku inference profiles, and reading only SSM parameters under `/glassbox/`.

## Kubernetes rollouts: probes, migration gate and graceful shutdown

Glassbox releases are tuned for a node with no spare memory. The owner chose a few seconds of downtime per release over running old and new pods side by side.

**Rollout strategy.** Both `api` and `retrieval-worker` use `RollingUpdate` with `maxSurge: 0` and `maxUnavailable: 1`. A rollout never schedules an extra pod: the old pod stops before its replacement starts. With one api replica, the site is briefly unavailable during each release, for the time it takes the new pod to start and pass readiness (longer if a migration is running). When KEDA has scaled workers above one, replicas are replaced one at a time, so old-image and new-image workers briefly consume the same stream together.

**Migration gate.** Each release recreates the `migrate` Job (`alembic upgrade head`). Job pod templates are immutable, so the Job carries the Flux annotation `kustomize.toolkit.fluxcd.io/force: enabled` (a string enum, not a boolean), which makes Flux delete and recreate it when the image tag changes. The `api` and `retrieval-worker` pods each have a `wait-for-migrations` initContainer (`python -m services.glassbox.db.wait_for_migrations --timeout 300`). It polls the database's Alembic revision read-only every 5 seconds until it equals the head revision baked into the image. Each check has a hard 5-second cap on connecting and querying, so a stalled MySQL cannot stretch the wait. After 5 minutes it exits with an error pointing at the migrate Job's logs, and the pod shows `Init:Error` rather than hanging. The same gate refuses a rollback to an image older than the database schema.

**Probes.** The api has a startup probe on `/healthz` (every 5 seconds, up to 30 failures, so up to 150 seconds to boot), a readiness probe on `/readyz` (every 10 seconds), and a liveness probe on `/healthz` (every 20 seconds, restarting only after 6 consecutive failures). All use 5-second timeouts, because the default 1-second timeout caused spurious failures under swap pressure mid-rollout. MySQL has a TCP startup probe and a `mysqladmin ping` readiness probe; Redis has a `redis-cli ping` readiness probe.

**Graceful shutdown.** Uvicorn runs with `--timeout-graceful-shutdown 25` inside a 30-second `terminationGracePeriodSeconds`, so in-flight requests and SSE streams get up to 25 seconds to finish before the pod is killed.

**Ordering after rollout.** Ingestion runs only after both Deployments report their rollout complete, through the Flux `app-ready` health check described in "Deployment pipeline: Flux GitOps".

## Deployment pipeline: CI checks and release to Amazon ECR

Every Glassbox change reaches production through GitHub pull requests into `main`, which is branch-protected with required status checks.

**CI (`.github/workflows/ci.yml`).** Runs on every pull request and every push to `main`, with no path filters, so docs-only changes also get the required checks. Two jobs:

- `backend-tests`: Python 3.12 with MySQL 8.0 and redis-stack-server 7.2 as service containers. It applies the Alembic migrations, runs `ruff check`, then runs `pytest services/tests` with the fake provider, so CI never calls Bedrock or spends money.
- `frontend-checks`: Node 22, `npm ci`, lint (oxlint), and a production build (TypeScript plus Vite).

**Release (`.github/workflows/release.yml`).** On a push to `main` that touches any path the Dockerfile copies into the image (`services/`, `frontend/`, `corpus/`, `docs/`, `infra/`, `k8s/`) or another build input, a native ARM64 GitHub runner builds the image. A docs-only or corpus-only merge therefore still ships a release, and its ingest Job indexes the new content. Native ARM64 matches the Graviton EC2 node; an x86 runner emulating ARM took more than 20 minutes. The multi-stage Dockerfile builds the frontend with Node 22, then copies it into a Python 3.12 slim image with the API, worker, migrations and corpus content. The image runs as a non-root `glassbox` user. The workflow gets short-lived AWS credentials through GitHub OIDC (the `release` environment and a dedicated release role), logs in to Amazon ECR, and pushes three tags: the short Git SHA, `build-N` (the workflow run number) and `latest`. The release workflow never writes to Git.

**Why `build-N`.** Flux needs a tag it can sort to find the newest image. Short Git SHAs do not sort, and a bare numeric tag once collided with an all-digit short SHA that sorted higher than every real build and got deployed. The `build-` prefix makes that collision impossible.

**The deploy branch.** A third workflow, `sync-deploy-branch.yml`, merges `main` into an unprotected `deploy` branch on every push to `main`. Flux reads from `deploy` and commits image-tag bumps there. A protected branch with required checks would reject Flux's direct pushes, and a pull request opened with GitHub's default token would never trigger the checks needed to merge it. The `deploy` branch avoids both problems while always building on tested `main`.

**Amazon ECR.** Terraform manages the `glassbox` repository, which has scan-on-push enabled and a lifecycle rule that expires untagged images after 7 days (the leftovers from reassigning `latest`).

**Terraform workflows.** Changes under `infra/` run a separate workflow (`terraform.yml`): `terraform fmt` and `validate` on every pull request, a read-only `plan` through an OIDC plan role, and `apply` after a merge to `main` only once the owner approves the protected `terraform-prod` GitHub environment. The bootstrap root has its own workflow (`bootstrap.yml`), described in "Operations: node memory, runbooks and the bootstrap pipeline". Nobody runs Terraform from a laptop any more: every infrastructure change goes through a pull request and an approval click.

## Deployment pipeline: Flux GitOps, image automation and ordered Kustomizations

Flux runs inside the k3s cluster and turns a pushed image into a running release with no manual step. Merging to `main` deploys to production.

**Image automation.** An `ImageRepository` scans the ECR repository every minute. It sets `provider: aws`, so Flux's image-reflector-controller authenticates with the EC2 node's IAM role and needs no stored registry secret. An `ImagePolicy` keeps only tags matching `build-<number>` and picks the highest number. An `ImageUpdateAutomation` then rewrites the image tag at the `$imagepolicy` setter markers under `k8s/overlays/prod`, in both the root overlay's `kustomization.yaml` and the ingest overlay's, and commits the change to the `deploy` branch with the message "deploy: automated image tag update".

**Ordered Kustomizations.** Flux applies the manifests through several Kustomizations with explicit dependencies:

1. **`flux-system` (root)**: created by `flux bootstrap`. It applies `k8s/overlays/prod`: the base namespaces, StatefulSets, ConfigMap, RBAC, NetworkPolicies, Services, Ingresses, the `api` and `retrieval-worker` Deployments, the recreated `migrate` Job, the Flux image objects, and the child Kustomizations below. The `wait-for-migrations` initContainers hold the new pods until the migration finishes.
2. **`keda`**: applies the KEDA namespace, `HelmRepository` and `HelmRelease` with `wait: true`, so it is Ready only once the chart is installed and the `keda.sh` CRDs are registered.
3. **`keda-scaling`**: depends on `keda` and applies the `ScaledObject`. On a first install, this keeps the ScaledObject from being dry-run before its CRD exists, which would otherwise block the whole root Kustomization, app workloads included.
4. **`app-ready`**: depends on `flux-system`, applies nothing (an empty path), and health-checks the `api` and `retrieval-worker` Deployments with a 10-minute timeout. Flux treats a same-source dependency as ready only at the current Git revision, so `app-ready` cannot pass against the previous release's Deployments.
5. **`ingest`**: depends on `app-ready` and applies the `ingest` Job with `wait: true` and a 15-minute timeout. Ingestion therefore starts only after the new pods are Ready and never overlaps the rollout's memory peak.

The root Kustomization must not use `wait: true` or health checks on these objects, or it would wait on `ingest`, which waits on it. The default `flux bootstrap` root has neither. KEDA is installed through Flux rather than Terraform's Helm provider so there is one path to cluster state and Flux can correct drift.

To follow a release, `flux get kustomizations` should show `flux-system`, then `app-ready`, then `ingest` Ready at the same revision. If a rollout never becomes Ready, `app-ready` times out and ingestion does not run for that revision.

## AWS infrastructure with Terraform and the Cloudflare edge

All Glassbox cloud resources are defined in Terraform under `infra/` and live in `us-east-1`, chosen for Bedrock model availability. Every resource is tagged `project = glassbox`.

**Terraform layout.**

- `infra/bootstrap`: the root that creates the S3 state bucket (versioned, encrypted, public access blocked, TLS-only, protected from deletion by a bucket policy and `prevent_destroy`), the GitHub OIDC identity provider, and the IAM roles CI uses: the Terraform apply role, a read-only plan role, the image release role, two operations-runbook roles and two bootstrap-pipeline roles. Each role trusts only a specific GitHub environment of this repository. Its own state lives in that bucket under `bootstrap/terraform.tfstate`; the Terraform CI roles may use only the `envs/prod/*` keys, so CI cannot rewrite the state that defines its own permissions.
- `infra/envs/prod`: the production root. It uses an S3 backend with Terraform's native lockfile (no DynamoDB table) and wires six modules together.
- `modules/network`: one VPC, one public subnet in one availability zone, an internet gateway and a route table. There is no NAT gateway, which would cost more than everything else combined.
- `modules/compute`: the EC2 instance, security group, IAM instance role and profile, an Elastic IP, and the SSM document and State Manager association that configure compressed swap on the node (`zram.tf`, `zram-swap.sh`).
- `modules/ops`: the SSM Command documents behind the operations runbooks, one per action, built from the reviewed scripts in `infra/modules/ops/scripts`.
- `modules/registry`: the ECR repository and its lifecycle policy.
- `modules/secrets`: randomly generated MySQL password and IP-hash salt, stored as SSM Parameter Store SecureStrings.
- `modules/edge`: Cloudflare resources through the official Cloudflare provider.

**The EC2 node.** One `t4g.small` (ARM64 Graviton, 2 vCPUs, 2 GiB RAM) running Amazon Linux 2023, with a 20 GB encrypted gp3 root volume. CPU credits are set to `standard`, so sustained load is throttled instead of billed as unlimited burst. User data creates a 1 GiB swap file, installs k3s and the Flux CLI, and installs the systemd timer that refreshes the ECR pull secret. Later host settings, such as compressed swap, are applied through SSM State Manager rather than user data, because changing user data would force a stop and start of the instance. The AMI comes from AWS's "latest Amazon Linux 2023" SSM parameter, but the instance ignores AMI changes after launch (`ignore_changes = [ami]`). A newly published image therefore cannot force a replacement that would wipe k3s, MySQL and Redis data on the root volume. OS updates happen in place.

**Amazon Bedrock access.** The instance role can invoke Titan Text Embeddings V2 directly, plus the US cross-region inference profiles for Amazon Nova Lite and Claude Haiku. Nova Lite is the model in use. The underlying foundation models are callable only through those profiles.

**Cloudflare edge.** Cloudflare hosts DNS for basel.engineering. Terraform manages a proxied apex A record pointing at the node's Elastic IP, so the record follows the instance automatically. It also manages a Cache Rule that disables caching for `/api/*`, so SSE streams and API responses are never cached or buffered, while static assets use Cloudflare's default caching. Cloudflare terminates TLS for visitors and connects to the origin over HTTPS in "Full" mode against Traefik's self-signed certificate. The origin security group allows ports 80 and 443 only from Cloudflare's published IP ranges, and origin protection relies on that allowlist. Cloudflare API tokens are scoped to the zone and live only in GitHub environment secrets, separately for plan (read) and apply (edit).

## Operations: node memory, runbooks and the bootstrap pipeline

**The 2026-09-30 memory incident.** On 2026-09-30 the 2 GiB node ran out of memory headroom. Its only swap was the swap file on the EBS root volume, the same disk that holds k3s's SQLite datastore and MySQL, so heavy swapping stalled disk I/O. k3s's datastore timed out, KEDA's Helm upgrade failed and retried, Traefik stopped reporting ready, and Cloudflare returned error 521 to visitors. The site came back after a reboot of the node, a move to compressed swap, suspending KEDA (its Flux Kustomizations and HelmRelease suspended, its Deployments scaled to 0) and briefly pausing the `warm-answers` CronJob. Flux later re-applied the CronJob manifest, which turned the pause off again, so the warm-up runs as normal. After the fix the node showed about 3% memory pressure (the kernel's PSI "some" average over 300 seconds) and about 357 MiB of memory available. KEDA stays suspended until the node shows steady headroom under normal traffic.

**Compressed swap (zram).** Swap now goes to compressed RAM first: `/dev/zram0` with priority 100, the `lzo-rle` compressor (the only zram compressor this Amazon Linux 2023 kernel builds), and about 920 MB of uncompressed capacity (half of RAM, capped at 1 GiB). The disk swap file stays as overflow at priority -2, so the kernel fills zram before it writes to disk. Only the compressed pages use RAM, so a few hundred megabytes of swapped pages cost a fraction of that. Amazon Linux's own `zram-generator` creates the device on every boot; the setup script only overrides the packaged rule that disables zram on machines with more than 800 MiB of RAM, and then tunes `swappiness` to 150 and `page-cluster` to 0. Terraform delivers this as an SSM document plus a State Manager association (`infra/modules/compute/zram.tf`) that runs once when created, again whenever the script changes, and weekly to repair drift. The script holds a lock (`flock`) so two runs never overlap, and an exit trap rolls back the swap tuning if zram is not active at the end, because high swappiness with only disk swap would make thrashing worse.

**Push-button runbooks.** Incident actions are GitHub Actions workflows rather than hand-typed commands. Eight "Ops · ..." workflows (Diagnose, Reboot node, Restart deployment, Flux suspend or resume, Flux reconcile, KEDA on or off, Warm-up CronJob suspend or resume, and Apply zram) each show only their own inputs and call one reusable workflow, `.github/workflows/ops.yml`. Each action runs a fixed, Terraform-managed SSM Command document (`glassbox-ops-*`, nine of them, including a read-only `boot-id` check) with inputs limited to an allowed list, so nothing can run on the node that was not reviewed in this repository; the generic "run any shell script" document is never used. Diagnose is read-only, needs no approval, and redacts its output because workflow logs in this public repository are public: it reports memory, swap and zram, pressure (PSI), pods, Flux and KEDA status, warning events and k3s error counts, without printing Secrets or environment variables. Every other action waits for the owner to approve the protected `ops` GitHub environment, and runs a diagnose before and after. Reboot node is a plain EC2 API call, so it works even when the node is too sick to run commands, and it proves the reboot happened by waiting for the kernel's boot ID to change.

**GitHub environments and roles.** Each kind of access has its own GitHub environment and IAM role, trusted through GitHub OIDC with no stored AWS keys: `ops-read` (no approval, `main` only) for `glassbox-ops-read`, which can run only the diagnose document; `ops` (owner approval) for `glassbox-ops`, which can run the `glassbox-ops-*` documents and reboot the tagged instance; `bootstrap-plan` (no approval) for the read-only `glassbox-bootstrap-plan`; and `bootstrap` (owner approval, `main` only) for `glassbox-bootstrap`. The existing `terraform-plan`, `terraform-prod` and `release` environments cover the production Terraform root and image releases.

**The bootstrap pipeline.** `.github/workflows/bootstrap.yml` plans `infra/bootstrap` on every pull request that touches it and posts the result. An apply is started from `main`: the plan job prints the plan and a SHA-256 fingerprint of the change set, the owner approves the `bootstrap` environment, and the apply job re-plans and applies only if the new fingerprint matches the reviewed one. The plan file never leaves the runner. The bootstrap state moved from a file on the owner's laptop into the S3 state bucket, and the pipeline's first live apply delivered the IAM fixes the zram association needed. Two IAM details surfaced on the way: SSM `CreateAssociation` is authorized against both the instance and the document, and the `aws:RequestTag` condition is not populated for the document, so a tag-based condition on it denies the call.

## Observability and health checks

Glassbox keeps observability light because the 2 GiB node has little memory to spare. What exists today:

**Health endpoints.** The FastAPI app exposes `GET /healthz`, which returns `{"status": "ok"}` whenever the process is serving and backs the startup and liveness probes. It also exposes `GET /readyz`, which opens a fresh MySQL connection to run `SELECT 1` and pings Redis, each with a 3-second connect timeout. `/readyz` returns `{"mysql": ..., "redis": ..., "ready": ...}` and responds with HTTP 503 when either dependency is down, so Kubernetes stops routing traffic to an api pod that cannot reach its data stores. `/readyz` is reachable publicly through Cloudflare, which gives a quick external check that the whole path, from edge to Traefik to API to MySQL and Redis, is up.

**The trace as observability.** The live architecture diagram is the main observability feature. Every `/api/ask` request streams per-stage timings (`duration_ms`) and cache hit or miss results for each node, and the stats bar shows the last request's latency to first token, answer-cache status and output tokens.

**Query log.** Each request writes a row to MySQL's `queries` table with per-stage timings in milliseconds, total time, cache status, mode (`full`, `retrieval_only` or `stopped`), the retrieved chunk IDs, tokens in and out, the conversation turn index, and the rewritten follow-up query. This table supports offline analysis of latency, cache effectiveness and retrieval quality. It stores no IP addresses.

**Ingestion bookkeeping.** Each ingest run writes an `ingestion_runs` row (status, documents changed, chunks written) and prints every skipped file with its reason to the Job's logs.

**Logs.** The API, worker, migrate and ingest processes log through Python's standard `logging` module to stdout, read with `kubectl logs` over an SSM session. Failures include the request ID or Redis message ID. The `wait-for-migrations` initContainer logs the database's current and expected Alembic revisions while it waits. Flux status is read with `flux get kustomizations`, and KEDA's scaling decisions appear in the HPA it manages.

**Node diagnostics.** The "Ops · Diagnose" workflow is the first step in any incident. It needs no approval and prints a redacted, time-bounded snapshot of the node into the workflow log: uptime, memory, swap and zram, memory, I/O and CPU pressure (PSI), the largest processes, node conditions, `kubectl top`, every pod, Deployment, CronJob and Job, Flux Kustomizations and HelmReleases, KEDA, the newest warning events, recent k3s error counts and kernel out-of-memory kills.

**Retrieval evaluation.** `eval/run_eval.py` measures recall@5 and mean reciprocal rank over 30 public-safe questions in `eval/questions.yaml`, through the real Redis vector-search path. It runs manually. The recorded baseline with real Titan embeddings is recall@5 of about 0.87 and MRR of about 0.69.

**Cost guardrails.** Spend is bounded in the application by the daily LLM budget, the per-IP rate limit, the semantic answer cache and the kill switch, and on AWS by standard CPU credits and a monthly AWS Budgets alert.

## Local development and the fake provider

Glassbox runs locally without AWS. `docker-compose.yml` starts MySQL 8.0, `redis/redis-stack-server` 7.2 and the API. The retrieval worker runs as a separate process (`python -m services.glassbox.worker.main`), and ingestion runs with `python -m services.glassbox.ingest.run` after `alembic upgrade head`.

The provider layer (`services/glassbox/providers/`) is the seam that makes this work. `GLASSBOX_PROVIDER` selects `fake` (the default) or `bedrock`, and one factory returns both the embedding provider and the LLM provider, so embeddings always share one vector space. The fake embedding provider derives a deterministic 512-float vector from a SHA-256 hash of the text. The fake LLM streams a fixed placeholder reply and returns the follow-up unchanged as its rewrite. Neither makes network calls, so local development, the test suite and CI never spend money on Bedrock. The Bedrock adapters (`providers/bedrock.py`) use `boto3` with adaptive retries, call `InvokeModel` for Titan V2 embeddings (512 dimensions, normalized), and call `ConverseStream` for generation. Each provider exposes a `model_id` that is stored with every chunk and included in every cache key, so switching providers or models can never replay results from a different model.

The code is organized by service under `services/glassbox/`:

- `api/`: FastAPI routes: `ask.py` (the question pipeline and SSE), `demo.py` and `capacity.py` (stress test), `cluster.py` (pod watch stream), and the health checks in `main.py`.
- `worker/main.py`: the Redis Streams consumer and the synthetic-job handler.
- `retrieval/search.py`: the KNN query on `idx:chunks`.
- `cache/`: the embedding, retrieval, chunk and semantic answer caches.
- `limits.py` and `killswitch.py`: the rate limiter, daily budget and kill switch.
- `trace.py`: the shared sequence and timing helpers for trace events.
- `ingest/`: the scanner, chunkers, Redis index management and the ingestion run.
- `db/`: SQLAlchemy models, Alembic migrations and the migration wait gate.

Tests live in `services/tests/`. They cover the chunkers, caches, limits, kill switch, worker, SSE contract, conversation history, capacity gate, cluster stream, migrations gate and ingestion. Integration tests use real MySQL and Redis with the fake provider.

## Design trade-offs and why Glassbox made them

- **Single k3s node instead of EKS.** EKS charges for its control plane on top of the nodes. k3s is conformant Kubernetes, and the manifests use nothing k3s-specific beyond the `local-path` storage class, so they could move to EKS. The cost is no high availability: if the node fails, the site is down until it is rebuilt.
- **In-cluster MySQL instead of RDS.** RDS would be a real monthly cost with no free-tier offset for this account. `documents` and `chunks` are a rebuildable index of Git-tracked content, so losing managed backups is low-risk. Only the `queries` log cannot be rebuilt.
- **Redis for everything fast.** One redis-stack-server provides vector search, caches, the queue, pub/sub trace fan-out, rate limits and budget counters, instead of five separate services on a 2 GiB node.
- **A queue that traffic does not need.** Real traffic could be served synchronously. The Redis Streams queue exists to demonstrate backpressure and queue-driven autoscaling, and it is what makes the stress test real. The docs say so openly.
- **API embeds, worker retrieves.** The API computes the question embedding because the semantic answer cache needs the vector before deciding whether to enqueue. The worker receives a provider-neutral 512-float vector and never calls a model.
- **Acknowledge-always worker.** The worker acknowledges every job, even on failure, and reports the error on the trace channel. The API's 30-second timeout covers lost jobs. A visitor retries a question, so redelivery logic would add complexity without benefit.
- **Memory over zero downtime.** `maxSurge: 0` rollouts cost a few seconds of downtime per release but never need memory for two api pods at once.
- **Capacity-gated stress test.** A real burst runs only with 512 MiB of confirmed free memory, and a simulation covers every other case, so a visitor's click can never push the node into out-of-memory kills.
- **Flux pull-based GitOps instead of CI pushes.** The cluster pulls from GitHub and ECR, so the Kubernetes API is never exposed to CI, and Git always describes the running state. The unprotected `deploy` branch lets Flux commit tag bumps without weakening `main`'s protection.
- **Cloudflare instead of CloudFront and ACM.** Cloudflare already hosted the domain's DNS, proxies the apex natively and is free.
- **Nova Lite for generation.** Anthropic Claude Haiku streaming on Bedrock was blocked by the account's first-time-use form, so production uses Amazon Nova Lite. Switching models is a configuration change that also moves the answer cache to a new model identity.
- **Keyword-based grounding labels.** Labeling design-doc chunks that describe unbuilt work is cheap and effective, but not tense-aware, so the signal list is meant to cover only unbuilt work, and this deep dive keeps that work in one final section.
- **Content baked into the image.** Ingesting from the image snapshot keeps content and code in lockstep and avoids Git credentials in the cluster. The trade-off is that a content fix needs a release.

## Planned / not built yet

Everything in this section is planned or proposed design. None of it is implemented in the running Glassbox system today.

- **Google Drive and S3/SQS content pipeline (Milestone 4, `docs/DESIGN-003-ingestion.md`).** Not built. The design would author "About Me" content in Google Docs, sync it through a Drive connector and a Git connector into an S3 raw zone, send one SQS message per change, and process those messages with a KEDA ScaledJob ingestion worker. It would include a dead-letter queue, nightly reconciliation between stages, and blue-green re-embedding. Today, ingestion reads files baked into the container image, as described in the ingestion pipeline section.
- **Self-healing node recovery (Milestone 3, DD2).** Not built. The design replaces the standalone EC2 instance with a launch template and an Auto Scaling Group of exactly one instance across two public subnets, whose boot script re-attaches the Elastic IP. Today there is a single EC2 instance, and a node failure needs a manual rebuild.
- **Chat input polish and streaming checks (DD2).** Not built: typing the next question while an answer streams, Up-arrow recall, a Retry button on error replies, a scripted production check that heartbeats survive Cloudflare, and time-to-first-token logging. (Heartbeats, the Stop button and the idle watchdog are already live.)
- **Nightly ingestion CronJob.** Not built. Ingestion runs once per release, after the rollout.
- **Pruning deleted files from the index.** Not built. The ingest Job does not remove documents whose files were deleted from the repository.
- **Metrics and tracing.** Not built. There is no Prometheus `/metrics` endpoint, no Grafana Cloud export, no OpenTelemetry tracing, no CloudWatch status-check alarm and no external uptime ping. Logs are plain text rather than structured JSON.
- **Retrieval evaluation in CI.** Not built. The recall@5 and MRR harness runs manually and does not gate pull requests.
- **Citation deep links.** Not built. Sources show a title, path and score but do not link to the file and line range on GitHub at the deployed commit.
- **Landscape phone layout.** Not built. The mobile layout is tuned for portrait phones; a pass for phones rotated to landscape (short screens) is planned.
- **Polish (DD1 Phase 7).** Not started: recorded load-test numbers, footer statistics beyond the last request, and README screenshots.
- **Stronger edge and network posture.** Not built. This covers a trusted origin certificate for Cloudflare "Full (strict)" mode, a free S3 gateway VPC endpoint, private subnets with VPC endpoints for Bedrock and SSM, Cloudflare WAF rules, and the External Secrets Operator.
- **Stretch ideas.** An EKS variant to prove manifest portability, a multi-node or managed control plane, and a "live facts" tool that answers "what version is deployed right now?" from the cluster.
