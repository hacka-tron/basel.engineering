"""Redis reconcile: rebuild chunk keys from MySQL rows, never via an embedding call."""

import fnmatch
import struct

import pytest

from services.glassbox.cache.answer import _model_tag
from services.glassbox.ingest import reconcile as rec
from services.glassbox.ingest import run as ingest_run
from services.glassbox.ingest.reconcile import ScopeRow, plan_reconcile
from services.glassbox.ingest.redis_index import chunk_content_sha, chunk_fields

MODEL = "titan"
TAG = _model_tag(MODEL)
VECTOR = struct.pack("512f", *([0.5] * 512))


def _row(chunk_id, corpus="about_me", document_id=1, path="corpus/about-me/a.md", text=None):
    text = text if text is not None else f"chunk {chunk_id}"
    return ScopeRow(chunk_id, corpus, document_id, path, chunk_content_sha(text))


def _stored(row: ScopeRow, **overrides):
    fields = dict(
        corpus=row.corpus,
        model=TAG,
        document_id=str(row.document_id),
        source_path=row.source_path,
        content_sha=row.content_sha,
    )
    fields.update(overrides)
    return tuple(fields[name] for name in rec._COMPARED)


def test_missing_key_is_repaired_and_matching_key_left_alone():
    rows = [_row(1), _row(2)]
    reports = plan_reconcile(MODEL, rows, {1: _stored(rows[0])})
    assert reports["about_me"].repaired == [2]
    assert reports["about_me"].rewritten == [] and reports["about_me"].removed == []
    assert reports["about_me"].changed and not reports["about_system"].changed


def test_orphan_key_of_this_model_is_removed():
    rows = [_row(1)]
    orphan = ("about_me", TAG, "9", "corpus/about-me/gone.md", "x")
    reports = plan_reconcile(MODEL, rows, {1: _stored(rows[0]), 7: orphan})
    assert reports["about_me"].removed == [7]
    assert reports["about_me"].redis_keys == 2


def test_other_models_keys_are_out_of_scope():
    rows = [_row(1)]
    other = ("about_me", _model_tag("old-model"), "9", "corpus/about-me/a.md", "x")
    reports = plan_reconcile(MODEL, rows, {1: _stored(rows[0]), 7: other})
    assert not any(report.changed for report in reports.values())


@pytest.mark.parametrize(
    "overrides",
    [
        {"content_sha": chunk_content_sha("different text")},
        {"content_sha": None},  # written before content_sha existed: back-filled
        {"document_id": "99"},
        {"source_path": "corpus/about-me/other.md"},
        {"corpus": "about_system"},
        {"model": _model_tag("old-model")},
    ],
)
def test_key_that_disagrees_with_its_row_is_rewritten(overrides):
    row = _row(1)
    reports = plan_reconcile(MODEL, [row], {1: _stored(row, **overrides)})
    assert reports["about_me"].rewritten == [1]
    assert reports["about_me"].removed == [] and reports["about_system"].removed == []


def test_zero_mysql_rows_never_deletes_even_when_forced():
    keys = {chunk_id: ("about_system", TAG, "1", "docs/x.md", "sha") for chunk_id in range(1, 40)}
    rows = [_row(100)]  # about_me still has rows; about_system has none
    for force in (False, True):
        reports = plan_reconcile(MODEL, rows, {**keys, 100: _stored(rows[0])}, force=force)
        system = reports["about_system"]
        assert system.removed == [] and system.refused
        assert "zero" in system.refused and "39 keys" in system.refused
        assert reports["about_me"].refused is None


def test_force_rewrites_every_existing_key():
    rows = [_row(1), _row(2)]
    keys = {row.chunk_id: _stored(row) for row in rows}
    reports = plan_reconcile(MODEL, rows, keys, force=True)
    assert reports["about_me"].rewritten == [1, 2]


# --- reconcile() against an in-memory Redis -----------------------------------


class _Pipeline:
    def __init__(self, redis):
        self.redis, self.ops = redis, []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def hmget(self, key, fields):
        self.ops.append(("hmget", key, fields))

    def delete(self, *keys):
        self.ops.append(("delete", keys))

    def hset(self, key, mapping):
        self.ops.append(("hset", key, mapping))

    async def execute(self):
        results = []
        for op in self.ops:
            if op[0] == "hmget":
                stored = self.redis.store.get(op[1], {})
                results.append([stored.get(field) for field in op[2]])
            elif op[0] == "delete":
                results.append(await self.redis.delete(*op[1]))
            else:
                self.redis.hset(op[1], op[2])
                results.append(len(op[2]))
        return results


class _Redis:
    def __init__(self):
        self.store: dict[str, dict] = {}
        self.strings: dict[str, int] = {}
        self.multis = 0

    def hset(self, key, mapping):
        encoded = {
            name: value if isinstance(value, bytes) else str(value).encode()
            for name, value in mapping.items()
        }
        self.store.setdefault(key, {}).update(encoded)

    async def scan_iter(self, match, count):
        for key in list(self.store):
            if fnmatch.fnmatchcase(key, match):
                yield key.encode()

    def pipeline(self, transaction=True):
        self.multis += bool(transaction)
        return _Pipeline(self)

    async def delete(self, *keys):
        removed = 0
        for key in keys:
            removed += self.store.pop(key, None) is not None
            removed += self.strings.pop(key, None) is not None
        return removed

    async def incr(self, key):
        self.strings[key] = self.strings.get(key, 0) + 1
        return self.strings[key]


@pytest.fixture
def mysql(monkeypatch):
    """Fake MySQL: full chunk rows, (id, corpus, embedding, path, document_id, text)."""
    rows = {
        1: (1, "about_me", VECTOR, "corpus/about-me/a.md", 10, "alpha"),
        2: (2, "about_me", VECTOR, "corpus/about-me/a.md", 10, "beta"),
        3: (3, "about_system", VECTOR, "docs/DESIGN.md", 20, "gamma"),
    }

    def load_scope_rows(engine, model_id):
        assert model_id == MODEL
        return [
            ScopeRow(cid, corpus, doc, path, chunk_content_sha(text))
            for cid, corpus, _, path, doc, text in rows.values()
        ]

    def load_write_rows(engine, model_id, chunk_ids):
        return [rows[cid] for cid in chunk_ids if cid in rows]

    monkeypatch.setattr(rec, "load_scope_rows", load_scope_rows)
    monkeypatch.setattr(rec, "load_write_rows", load_write_rows)
    return rows


def _write(redis, row):
    cid, corpus, vector, path, doc, text = row
    redis.hset(f"chunk:{cid}", chunk_fields(corpus, MODEL, vector, path, doc, text))


@pytest.mark.asyncio
async def test_reconcile_rebuilds_a_flushed_index_from_mysql_and_bumps_versions(mysql):
    redis = _Redis()
    reports = {r.corpus: r for r in await rec.reconcile(None, redis, MODEL)}
    assert reports["about_me"].repaired == [1, 2] and reports["about_system"].repaired == [3]
    stored = redis.store["chunk:1"]
    assert stored["vector"] == VECTOR
    assert stored["content_sha"] == chunk_content_sha("alpha").encode()
    assert stored["model"] == TAG.encode() and stored["corpus"] == b"about_me"
    assert stored["document_id"] == b"10"
    assert redis.strings == {"corpus:ver:about_me": 1, "corpus:ver:about_system": 1}

    again = await rec.reconcile(None, redis, MODEL)
    assert not any(report.changed for report in again)
    assert redis.strings == {"corpus:ver:about_me": 1, "corpus:ver:about_system": 1}


@pytest.mark.asyncio
async def test_reconcile_removes_orphans_rewrites_mismatches_and_bumps_only_changed(mysql):
    redis = _Redis()
    for row in mysql.values():
        _write(redis, row)
    # An orphan (no MySQL row) plus its cached text, and a stale key for row 2.
    redis.hset("chunk:9", {"corpus": "about_me", "model": TAG, "vector": VECTOR})
    redis.hset("chunktxt:9", {"text": "old"})
    redis.hset("chunk:2", {"content_sha": chunk_content_sha("old beta")})
    redis.hset("chunktxt:2", {"text": "old beta"})
    reports = {r.corpus: r for r in await rec.reconcile(None, redis, MODEL)}
    assert reports["about_me"].removed == [9]
    assert reports["about_me"].rewritten == [2]
    assert "chunk:9" not in redis.store and "chunktxt:9" not in redis.store
    assert redis.store["chunk:2"]["content_sha"] == chunk_content_sha("beta").encode()
    assert "chunktxt:2" not in redis.store  # stale chunk-text cache dropped
    assert redis.strings == {"corpus:ver:about_me": 1}  # about_system was already right


@pytest.mark.asyncio
async def test_reconcile_back_fills_content_sha_on_keys_written_before_it(mysql):
    redis = _Redis()
    for row in mysql.values():
        _write(redis, row)
        del redis.store[f"chunk:{row[0]}"]["content_sha"]
    reports = await rec.reconcile(None, redis, MODEL)
    assert sorted(cid for report in reports for cid in report.rewritten) == [1, 2, 3]
    assert all("content_sha" in fields for fields in redis.store.values())


@pytest.mark.asyncio
async def test_reconcile_with_an_empty_mysql_deletes_nothing(monkeypatch, caplog):
    monkeypatch.setattr(rec, "load_scope_rows", lambda engine, model_id: [])
    monkeypatch.setattr(rec, "load_write_rows", lambda *args: [])
    redis = _Redis()
    for chunk_id in range(1, 6):
        redis.hset(f"chunk:{chunk_id}", {"corpus": "about_me", "model": TAG, "vector": VECTOR})
    reports = {r.corpus: r for r in await rec.reconcile(None, redis, MODEL)}
    assert reports["about_me"].refused and reports["about_me"].removed == []
    assert len(redis.store) == 5 and redis.strings == {}
    assert "REFUSED" in caplog.text


@pytest.mark.asyncio
async def test_reconcile_never_calls_an_embedding_provider(mysql, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("reconcile must not embed")

    monkeypatch.setattr(ingest_run, "get_embedding_provider", forbidden)
    await rec.reconcile(None, _Redis(), MODEL)


@pytest.mark.asyncio
async def test_invalid_stored_vector_is_skipped_not_indexed(mysql):
    mysql[2] = (2, "about_me", b"\x00" * 10, "corpus/about-me/a.md", 10, "beta")
    redis = _Redis()
    reports = {r.corpus: r for r in await rec.reconcile(None, redis, MODEL)}
    assert reports["about_me"].repaired == [1] and reports["about_me"].skipped == [2]
    assert "chunk:2" not in redis.store


def test_fraction_guard_refuses_a_large_orphan_purge_unless_forced():
    rows = [_row(chunk_id) for chunk_id in range(1, 7)]
    keys = {row.chunk_id: _stored(row) for row in rows}
    orphan = ("about_me", TAG, "9", "corpus/about-me/gone.md", "x")
    keys.update({chunk_id: orphan for chunk_id in range(100, 104)})  # 4 of 10 keys
    reports = plan_reconcile(MODEL, rows, keys)
    assert reports["about_me"].removed == [] and "30%" in reports["about_me"].refused
    forced = plan_reconcile(MODEL, rows, keys, force=True)
    assert forced["about_me"].removed == [100, 101, 102, 103]
    assert forced["about_me"].refused is None
    # Repairs and rewrites still happen when deletion is refused.
    del keys[1]
    assert plan_reconcile(MODEL, rows, keys)["about_me"].repaired == [1]


@pytest.mark.parametrize(("orphans", "kept"), [(3, 7), (2, 1)])
def test_fraction_guard_allows_up_to_30_percent_or_two_keys(orphans, kept):
    rows = [_row(chunk_id) for chunk_id in range(1, kept + 1)]
    keys = {row.chunk_id: _stored(row) for row in rows}
    keys.update({100 + n: ("about_me", TAG, "9", "x.md", "x") for n in range(orphans)})
    reports = plan_reconcile(MODEL, rows, keys)
    assert len(reports["about_me"].removed) == orphans and reports["about_me"].refused is None


def test_key_without_a_corpus_is_removed_as_unknown_without_a_refusal():
    rows = [_row(1)]
    keys = {
        1: _stored(rows[0]),
        **{chunk_id: (None, TAG, None, None, None) for chunk_id in range(50, 60)},
        70: (None, _model_tag("other"), None, None, None),  # another model: left alone
    }
    reports = plan_reconcile(MODEL, rows, keys)
    assert reports["unknown"].removed == list(range(50, 60))
    assert reports["unknown"].refused is None
    assert all(report.refused is None for report in reports.values())


@pytest.mark.asyncio
async def test_unknown_report_is_returned_only_when_it_had_keys(mysql, capsys):
    redis = _Redis()
    for row in mysql.values():
        _write(redis, row)
    assert [r.corpus for r in await rec.reconcile(None, redis, MODEL)] == [
        "about_me",
        "about_system",
    ]
    redis.hset("chunk:50", {"model": TAG, "vector": VECTOR})
    reports = {r.corpus: r for r in await rec.reconcile(None, redis, MODEL)}
    assert reports["unknown"].removed == [50] and "chunk:50" not in redis.store
    ingest_run._print_reconcile(list(reports.values()))
    assert "REFUSED" not in capsys.readouterr().out


@pytest.mark.asyncio
async def test_rewrites_go_in_small_transactions(monkeypatch):
    rows = {
        cid: (cid, "about_me", VECTOR, "corpus/about-me/a.md", 1, f"t{cid}")
        for cid in range(1, 121)
    }
    monkeypatch.setattr(
        rec,
        "load_scope_rows",
        lambda engine, model_id: [
            ScopeRow(cid, "about_me", 1, row[3], chunk_content_sha(row[5]))
            for cid, row in rows.items()
        ],
    )
    monkeypatch.setattr(
        rec, "load_write_rows", lambda engine, model_id, ids: [rows[cid] for cid in ids]
    )
    redis = _Redis()
    await rec.reconcile(None, redis, MODEL)
    assert redis.multis == 3  # 120 keys in MULTIs of 50
    assert len(redis.store) == 120


@pytest.mark.asyncio
async def test_version_is_bumped_when_a_later_batch_fails(mysql, monkeypatch):
    redis = _Redis()
    redis.hset("chunk:9", {"corpus": "about_system", "model": TAG, "vector": VECTOR})
    calls = []

    async def failing_write(client, model_id, rows):
        calls.append(rows)
        raise ConnectionError("redis went away")

    monkeypatch.setattr(rec, "_write_keys", failing_write)
    with pytest.raises(ConnectionError):
        await rec.reconcile(None, redis, MODEL)
    # The orphan delete for about_system was applied before the write failed.
    assert "chunk:9" not in redis.store
    assert redis.strings == {"corpus:ver:about_system": 1}


# --- Ingest lock --------------------------------------------------------------


class _LockRedis:
    def __init__(self, holder=None):
        self.value = holder
        self.calls = []

    async def set(self, key, value, nx, px):
        self.calls.append(("set", key, px))
        if self.value is not None:
            return None
        self.value = value
        return True

    async def eval(self, script, numkeys, key, token):
        self.calls.append(("eval", key))
        if self.value == token:
            self.value = None
            return 1
        return 0


@pytest.mark.asyncio
async def test_ingest_and_reindex_do_nothing_while_another_run_holds_the_lock(capsys):
    held = _LockRedis(holder="other-run")
    # engine=object(): touching MySQL would raise, so this proves nothing ran.
    result = await ingest_run.ingest(engine=object(), redis_client=held, sweep="off")
    assert result.locked_out and result.docs_changed == 0
    assert await ingest_run.reindex(engine=object(), redis_client=held) is None
    assert held.value == "other-run"  # never released someone else's lock
    err = capsys.readouterr().err
    assert "ANOTHER INGEST OR REINDEX HOLDS ingest:lock" in err
    # Each mode's banner states its own exit status.
    assert "this ingest did nothing (it exits 0" in err
    assert f"this reindex did nothing (it exits {ingest_run.REINDEX_LOCKED_OUT}" in err


@pytest.mark.asyncio
async def test_lock_is_taken_with_a_ttl_and_released_by_token():
    redis = _LockRedis()
    async with ingest_run.ingest_lock(redis) as acquired:
        assert acquired and redis.value is not None
    assert redis.value is None
    assert redis.calls[0] == ("set", "ingest:lock", ingest_run.INGEST_LOCK_TTL_MS)

    # Lock expired mid-run and another run took it: the release must not delete it.
    async with ingest_run.ingest_lock(redis):
        redis.value = "someone-else"
    assert redis.value == "someone-else"


def test_cli_locked_out_ingest_exits_zero_but_reindex_fails(monkeypatch):
    async def fake_ingest(**kwargs):
        return ingest_run.RunResult(locked_out=True)

    async def fake_reindex(**kwargs):
        return None

    monkeypatch.setattr(ingest_run, "ingest", fake_ingest)
    monkeypatch.setattr(ingest_run, "reindex", fake_reindex)
    assert ingest_run.main([]) == 0
    # An operator-requested reindex that did nothing must fail (Ops · Reindex).
    assert ingest_run.main(["--reindex"]) == ingest_run.REINDEX_LOCKED_OUT


# --- CLI ----------------------------------------------------------------------


@pytest.fixture
def reindex_cli(monkeypatch):
    calls = []

    async def fake_reindex(**kwargs):
        calls.append(kwargs)
        report = rec.ReconcileReport("about_me", MODEL, forced=True, mysql_chunks=3)
        report.rewritten = [1, 2, 3]
        return [report]

    async def fake_ingest(**kwargs):
        raise AssertionError("--reindex must not scan or embed")

    monkeypatch.setattr(ingest_run, "reindex", fake_reindex)
    monkeypatch.setattr(ingest_run, "ingest", fake_ingest)
    return calls


def test_cli_reindex_runs_only_the_forced_reconcile(reindex_cli, capsys):
    assert ingest_run.main(["--reindex"]) == 0
    assert reindex_cli == [{}]
    assert "reconcile about_me: repaired=0 rewritten=3 removed=0" in capsys.readouterr().out


@pytest.mark.parametrize(
    "argv",
    [
        ["--reindex", "--dry-run"],
        ["--reindex", "--force-sweep"],
        ["--reindex", "--corpus", "about_me"],
        ["--reindex", "--model", "titan"],
    ],
)
def test_cli_reindex_rejects_flags_it_would_ignore(reindex_cli, argv):
    assert ingest_run.main(argv) == 2
    assert reindex_cli == []


def test_cli_reindex_is_exclusive_with_sweep_and_clear(reindex_cli):
    for other in ("--sweep", "--no-sweep", "--clear"):
        with pytest.raises(SystemExit):
            ingest_run.main(["--reindex", other])


def test_cli_prints_a_banner_for_a_refused_reconcile(monkeypatch, capsys):
    refused = rec.ReconcileReport("about_system", MODEL, redis_keys=40)
    refused.refused = "MySQL has zero titan chunks"

    async def fake_ingest(**kwargs):
        return ingest_run.RunResult(reconcile=[refused])

    monkeypatch.setattr(ingest_run, "ingest", fake_ingest)
    assert ingest_run.main([]) == 0
    out = capsys.readouterr()
    for stream in (out.out, out.err):
        assert "!!! REDIS RECONCILE REFUSED for about_system (titan)" in stream


@pytest.mark.asyncio
async def test_prepare_index_drops_the_legacy_answer_index(monkeypatch):
    """Every ingest and reindex drops idx:answers (if present) before touching idx:chunks."""
    calls = []

    async def fake_drop(client):
        calls.append("drop idx:answers")
        return False

    async def fake_ensure(client):
        calls.append("ensure idx:chunks")
        return False

    class TagsReady:
        async def get(self, key):
            return b"1"

    monkeypatch.setattr(ingest_run, "drop_legacy_index", fake_drop)
    monkeypatch.setattr(ingest_run, "ensure_index", fake_ensure)
    await ingest_run.prepare_index(None, TagsReady())
    assert calls == ["drop idx:answers", "ensure idx:chunks"]


@pytest.mark.asyncio
async def test_reindex_releases_the_lock_on_sigterm(monkeypatch):
    """Kubernetes stops the Ops · Reindex Job with SIGTERM: the lock must not stay held."""
    import asyncio
    import os
    import signal

    redis = _LockRedis()

    async def slow_reconcile(*args, **kwargs):
        await asyncio.sleep(30)

    async def ready(*args):
        return None

    monkeypatch.setattr(ingest_run, "prepare_index", ready)
    monkeypatch.setattr(ingest_run, "reconcile", slow_reconcile)
    asyncio.get_running_loop().call_later(0.05, os.kill, os.getpid(), signal.SIGTERM)
    with pytest.raises(asyncio.CancelledError):
        await ingest_run.reindex(engine=object(), redis_client=redis)
    assert redis.value is None  # released
    assert ("eval", "ingest:lock") in redis.calls
    # The handler is removed afterwards: SIGTERM is back to its default.
    assert signal.getsignal(signal.SIGTERM) == signal.SIG_DFL


def test_cli_reindex_exits_143_when_stopped(monkeypatch, capsys):
    import asyncio

    async def stopped(**kwargs):
        raise asyncio.CancelledError

    monkeypatch.setattr(ingest_run, "reindex", stopped)
    assert ingest_run.main(["--reindex"]) == 143
    assert "ingest:lock released" in capsys.readouterr().err


@pytest.mark.asyncio
async def test_clear_takes_the_ingest_lock(monkeypatch, capsys):
    """--clear holds ingest:lock; locked out it deletes nothing. A dry run needs no lock."""
    calls = []

    async def fake_clear_scope(engine, client, corpus, model_id, *, dry_run, **kwargs):
        calls.append(dry_run)
        return "result"

    monkeypatch.setattr(ingest_run, "clear_scope", fake_clear_scope)
    held = _LockRedis(holder="other-run")
    assert (
        await ingest_run.clear("about_me", model_id="m", engine=object(), redis_client=held) is None
    )
    assert calls == [] and held.value == "other-run"
    assert "this clear did nothing (it exits 75" in capsys.readouterr().err
    assert (
        await ingest_run.clear(
            "about_me", model_id="m", dry_run=True, engine=object(), redis_client=held
        )
        == "result"
    )
    free = _LockRedis()
    assert (
        await ingest_run.clear("about_me", model_id="m", engine=object(), redis_client=free)
        == "result"
    )
    assert calls == [True, False] and free.value is None
