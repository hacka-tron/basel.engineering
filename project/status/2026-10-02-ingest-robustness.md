# Ingest write path: commit first, batch queries, honest failures

**Status:** PR [#138](https://github.com/hacka-tron/basel.engineering/pull/138) open (branch `fix/ingest-robustness`). Review: pending (orchestrator gate).

## TL;DR

Three deferred items from the Phase 1a ingestion review, all in `services/glassbox/ingest/run.py`:

1. **No Redis I/O inside a MySQL transaction any more.** A changed document's old Redis keys are deleted first, then MySQL commits the new chunk rows in one short transaction, then the new keys are written. A crash at any point leaves MySQL rows without Redis keys (which the end-of-run reconcile from #122 repairs), never a Redis key pointing at a deleted row (which the worker treats as an error).
2. **N+1 gone.** One query per run loads every indexed document's hash, chunk ids and embedding models (it used to be one `SELECT` per scanned file, about 200). Chunk rows go in with one bulk insert plus one `SELECT` for their ids, instead of a flush per chunk.
3. **A failed run reports the real error.** Marking the run `failed` now uses its own session, and if that write fails too (MySQL gone), it is logged and swallowed, and the original error is what the Job prints.

Nothing visitor-visible changes.

## How it works

```mermaid
sequenceDiagram
    participant I as ingest (one changed file)
    participant R as Redis
    participant M as MySQL
    I->>R: DEL old chunk:{id} keys, INCR corpus:ver
    I->>M: BEGIN; update document hash; replace chunk rows (bulk insert); COMMIT
    I->>R: MULTI: DEL old ids, HSET new chunk:{id}; INCR corpus:ver
    Note over I,R: crash here = rows without keys:<br/>next run skips the file (hash matches),<br/>its reconcile writes the keys from MySQL
```

This is the same order the stale sweep and `--clear` already use (`sweep.py`).

## Design decisions

- **Delete old keys before the commit, not after.** Committing first and then replacing keys (the obvious fix) has a crash window where Redis still holds the old document's keys but MySQL has deleted those rows. The worker raises on a KNN match with no MySQL row, and the reconcile would have to delete those orphans, which its 30% fraction guard can refuse for a large document in the small About Basel corpus. Deleting first means every failure leaves only *missing* keys, and the reconcile repairs missing keys with no guard.
- **Two `corpus:ver` bumps per changed document** (one for a new document). The first stops retrieval-cache entries holding the old chunk ids from being served after their rows are gone; the second drops entries computed while the document had no keys. The retrieval cache is cheap to refill; tests that counted one bump now count two.
- **Answer cache: nothing to change.** Entries are validated per source chunk by `content_sha`, not by corpus version. Deleted old keys read as "changed"; new chunk ids are new keys. A crash between commit and Redis write just means answers from that document are misses until the reconcile.
- **Preloading is safe** because `ingest:lock` keeps any other ingest or reindex out for the whole run.
- **Not done:** if the MySQL commit itself fails after the old keys were deleted, the document is unsearchable until the next ingest run (which re-ingests it, since MySQL still has the old hash). The run fails loudly. Logged in BACKLOG; not worth a compensation path at one ingest per release.

## What review caught

Opus review round 1: approved, no Critical or Important findings. Minors fixed before merge: the unit test now checks each new key's `content_sha` is its own row's text (a mutation pairing ids with the wrong chunks passed before); this report gained the visitor-window risk below and the note on `_mark_run_failed`.

## Operational notes and risks

- The first release after merge behaves like any other: unchanged files are skipped, the reconcile runs as before.
- **Short visitor window per changed document.** Between deleting the old keys (step 1) and writing the new ones (step 3), a vector search can't find that document, and an answer generated in that moment can go into the answer cache (up to 24 h) without it. The window is milliseconds plus one MySQL transaction, and ingest runs right after a rollout, when traffic is low. Before this change the reverse race existed: Redis briefly had keys for rows MySQL hadn't committed yet, which made the worker raise.
- `_mark_run_failed` already used a fresh session before; the fix is the try/except around it.
- `ingestion_runs` rows can stay `running` if MySQL is unreachable when the run fails; the log line `Could not mark ingestion run N failed` says so, followed by the real traceback.

## How to verify

- Unit tests (no services): `test_write_document_commits_mysql_with_no_redis_io_inside_the_transaction` (SQLite stand-in; asserts the old keys go while MySQL still has the old hash, the new keys are written only after the new hash is committed, and a failed Redis write leaves the commit and no keys) and `test_failed_run_status_update_never_masks_the_original_error`.
- Integration (CI's MySQL and Redis): `test_crash_between_mysql_commit_and_redis_write_is_repaired_by_next_run` fails the Redis write after the commit, then checks the next run skips the file without embedding, its reconcile repairs every new key with the right `content_sha`, removes no old key (there are none), and search finds the new chunk.
- After the next release, the ingest Job log looks as before (`docs_changed=... chunks_written=...` and the reconcile lines).

## Open items

- The commit-failure case above (BACKLOG, Data pipeline).
