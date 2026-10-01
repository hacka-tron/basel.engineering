"""Redis reconcile: rebuild chunk keys from MySQL rows, never via an embedding call."""

import fnmatch
import struct

import pytest

from services.glassbox.cache.answer import _model_tag
from services.glassbox.ingest import reconcile as rec
from services.glassbox.ingest import run as ingest_run
from services.glassbox.ingest.reconcile import ScopeRow, plan_reconcile
from services.glassbox.ingest.redis_index import chunk_fields, content_sha

MODEL = "titan"
TAG = _model_tag(MODEL)
VECTOR = struct.pack("512f", *([0.5] * 512))


def _row(chunk_id, corpus="about_me", document_id=1, path="corpus/about-me/a.md", text=None):
    text = text if text is not None else f"chunk {chunk_id}"
    return ScopeRow(chunk_id, corpus, document_id, path, content_sha(text))


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
        {"content_sha": content_sha("different text")},
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
            ScopeRow(cid, corpus, doc, path, content_sha(text))
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
    assert stored["content_sha"] == content_sha("alpha").encode()
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
    redis.hset("chunk:2", {"content_sha": content_sha("old beta")})
    redis.hset("chunktxt:2", {"text": "old beta"})
    reports = {r.corpus: r for r in await rec.reconcile(None, redis, MODEL)}
    assert reports["about_me"].removed == [9]
    assert reports["about_me"].rewritten == [2]
    assert "chunk:9" not in redis.store and "chunktxt:9" not in redis.store
    assert redis.store["chunk:2"]["content_sha"] == content_sha("beta").encode()
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
