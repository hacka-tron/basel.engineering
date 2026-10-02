"""Stale-sweep and --clear decision logic, with fakes (no MySQL or Redis needed)."""

import pytest

from services.glassbox.ingest import run as ingest_run
from services.glassbox.ingest import sweep
from services.glassbox.ingest.sweep import ScopedDocument, plan_sweep, scope_documents


def _docs(*paths: str) -> list[ScopedDocument]:
    return [ScopedDocument(index, path, (index * 10,)) for index, path in enumerate(paths, 1)]


def test_plan_has_nothing_stale_when_every_known_file_was_seen():
    plan = plan_sweep(
        "about_system",
        "m",
        _docs("docs/a.md", "docs/b.md"),
        {"docs/a.md", "docs/b.md", "docs/n.md"},
    )
    assert plan.stale == [] and plan.refused is None
    assert (plan.known, plan.seen_files) == (2, 3)


def test_plan_lists_missing_files_within_the_threshold():
    known = _docs(*(f"docs/f{i}.md" for i in range(10)))
    seen = {f"docs/f{i}.md" for i in range(10)} - {"docs/f3.md", "docs/f7.md", "docs/f1.md"}
    plan = plan_sweep("about_system", "m", known, seen, max_fraction=0.3)
    assert [doc.source_path for doc in plan.stale] == ["docs/f1.md", "docs/f3.md", "docs/f7.md"]
    assert plan.refused is None


def test_plan_refuses_a_drop_above_the_threshold_unless_forced():
    known = _docs(*(f"docs/f{i}.md" for i in range(10)))
    seen = {f"docs/f{i}.md" for i in range(6)}  # 4 of 10 missing = 40%
    refused = plan_sweep("about_system", "m", known, seen, max_fraction=0.3)
    assert refused.refused and "40%" in refused.refused and len(refused.stale) == 4
    forced = plan_sweep("about_system", "m", known, seen, max_fraction=0.3, force=True)
    assert forced.refused is None and len(forced.stale) == 4


def test_plan_always_allows_a_small_absolute_sweep():
    # Renaming two of the five About Basel files is 40% but must still work.
    plan = plan_sweep(
        "about_me",
        "m",
        _docs(*(f"corpus/about-me/{name}.md" for name in "abcde")),
        {f"corpus/about-me/{name}.md" for name in "abc"},
    )
    assert plan.refused is None and len(plan.stale) == sweep.ALWAYS_ALLOWED_STALE


def test_plan_refuses_when_the_scan_found_zero_files_even_when_forced():
    for known in (_docs("a.md"), _docs(*(f"f{i}" for i in range(20)))):
        plan = plan_sweep("about_me", "m", known, set(), force=True)
        assert plan.refused and "zero files" in plan.refused


def test_plan_refuses_when_a_whole_source_directory_disappears():
    # docs/ is 2 of 10 documents (20%, under the limit) but produced zero files,
    # which is what an image that stopped copying docs/ looks like.
    known = _docs("docs/a.md", "docs/b.md", *(f"infra/f{i}.tf" for i in range(8)))
    seen = {f"infra/f{i}.tf" for i in range(8)}
    plan = plan_sweep("about_system", "m", known, seen)
    assert plan.refused and "docs" in plan.refused and "zero scanned files" in plan.refused
    assert len(plan.stale) == 2
    forced = plan_sweep("about_system", "m", known, seen, force=True)
    assert forced.refused is None


def test_directory_guard_names_every_missing_directory():
    known = _docs("k8s/a.yaml", "docs/b.md", "services/c.py")
    plan = plan_sweep("about_system", "m", known, {"services/c.py"})
    assert "docs, k8s" in plan.refused
    about_me = plan_sweep("about_me", "m", _docs("private/a.md"), {"corpus/x.md"})
    assert "private About Basel checkout" in about_me.refused


def test_a_file_missing_from_a_directory_that_still_has_files_is_not_a_directory_drop():
    known = _docs("docs/a.md", "docs/b.md")
    plan = plan_sweep("about_system", "m", known, {"docs/a.md"})
    assert plan.refused is None and [doc.source_path for doc in plan.stale] == ["docs/b.md"]


def test_source_root():
    assert sweep.source_root("private/bio.md") == "private"
    assert sweep.source_root("infra/modules/x/main.tf") == "infra"


def test_plan_with_no_known_documents_is_a_no_op_even_with_zero_files():
    plan = plan_sweep("about_me", "m", [], set())
    assert plan.stale == [] and plan.refused is None


def test_scope_keeps_only_this_models_chunks_and_chunkless_documents():
    documents = [(1, "a.md"), (2, "b.md"), (3, "c.md"), (4, "empty.md")]
    chunks = [
        (11, 1, "titan"),
        (12, 1, "titan"),
        (21, 2, "fake"),
        (31, 3, "titan"),
        (32, 3, "fake"),
    ]
    scoped = {doc.source_path: doc.chunk_ids for doc in scope_documents(documents, chunks, "titan")}
    # b.md has only another model's chunks, so a titan sweep never touches it.
    assert scoped == {"a.md": (11, 12), "c.md": (31,), "empty.md": ()}


@pytest.mark.parametrize("value", ["delete", "yes", "1"])
def test_unknown_sweep_mode_fails_closed(monkeypatch, value):
    monkeypatch.setenv("GLASSBOX_INGEST_SWEEP", value)
    with pytest.raises(ValueError):
        sweep.sweep_mode_from_env()


def test_sweep_mode_defaults_to_report_and_fraction_to_30_percent(monkeypatch):
    monkeypatch.delenv("GLASSBOX_INGEST_SWEEP", raising=False)
    monkeypatch.delenv("GLASSBOX_INGEST_SWEEP_MAX_FRACTION", raising=False)
    assert sweep.sweep_mode_from_env() == "report"
    assert sweep.max_fraction_from_env() == 0.30
    monkeypatch.setenv("GLASSBOX_INGEST_SWEEP_MAX_FRACTION", "1.5")
    with pytest.raises(ValueError):
        sweep.max_fraction_from_env()


class _Recorder:
    def __init__(self, known_by_corpus):
        self.known_by_corpus = known_by_corpus
        self.loaded = []
        self.deleted = []

    def load(self, engine, corpus, model_id):
        self.loaded.append((corpus, model_id))
        return self.known_by_corpus.get(corpus, [])

    async def delete(self, engine, redis_client, corpus, model_id, documents):
        self.deleted.append((corpus, model_id, [doc.source_path for doc in documents]))


@pytest.fixture
def recorder(monkeypatch):
    rec = _Recorder(
        {
            "about_me": _docs("corpus/about-me/a.md", "corpus/about-me/gone.md"),
            "about_system": _docs(*(f"docs/f{i}.md" for i in range(10))),
        }
    )
    monkeypatch.setattr(sweep, "load_scope_documents", rec.load)
    monkeypatch.setattr(sweep, "delete_documents", rec.delete)
    return rec


SEEN = {
    "about_me": {"corpus/about-me/a.md"},
    # 5 of 10 about_system files missing: refused at the 30% default.
    "about_system": {f"docs/f{i}.md" for i in range(5)},
}


@pytest.mark.asyncio
async def test_apply_deletes_only_safe_corpora_scoped_to_the_model(recorder):
    plans = await sweep.run_sweep(None, None, SEEN, "titan", mode="apply")
    assert recorder.loaded == [
        ("about_me", "titan"),
        ("about_system", "titan"),
        ("portfolio", "titan"),
    ]
    assert recorder.deleted == [("about_me", "titan", ["corpus/about-me/gone.md"])]
    by_corpus = {plan.corpus: plan for plan in plans}
    assert by_corpus["about_me"].deleted and not by_corpus["about_me"].refused
    assert by_corpus["about_system"].refused and not by_corpus["about_system"].deleted


@pytest.mark.asyncio
async def test_report_mode_never_deletes(recorder, caplog):
    caplog.set_level("INFO", logger="services.glassbox.ingest.sweep")
    plans = await sweep.run_sweep(None, None, SEEN, "titan", mode="report")
    assert recorder.deleted == []
    assert [len(plan.stale) for plan in plans] == [1, 5, 0]
    assert "would delete corpus/about-me/gone.md" in caplog.text
    assert "REFUSED" in caplog.text


@pytest.mark.asyncio
async def test_off_mode_reads_nothing(recorder):
    assert await sweep.run_sweep(None, None, SEEN, "titan", mode="off") == []
    assert recorder.loaded == []


@pytest.mark.asyncio
async def test_force_lifts_the_threshold(recorder):
    await sweep.run_sweep(None, None, SEEN, "titan", mode="apply", force=True)
    assert [corpus for corpus, _, _ in recorder.deleted] == ["about_me", "about_system"]


class _FakeRedis:
    def __init__(self, events, fail=False):
        self.events = events
        self.fail = fail

    async def delete(self, *keys):
        if self.fail:
            raise ConnectionError("redis down")
        self.events.append(("redis.delete", keys))

    async def incr(self, key):
        self.events.append(("redis.incr", key))


class _FakeQuery:
    def __init__(self, events, model):
        self.events, self.model = events, model

    def filter(self, *args):
        return self

    def delete(self, synchronize_session):
        self.events.append(("mysql.delete", self.model.__tablename__))


class _FakeSession:
    def __init__(self, events):
        self.events = events

    def __enter__(self):
        self.events.append(("mysql.begin", None))
        return self

    def __exit__(self, *exc):
        self.events.append(("mysql.commit", None))

    def query(self, model):
        return _FakeQuery(self.events, model)

    def scalars(self, statement):
        return iter([])


class _FakeSessionmaker:
    def __init__(self, events):
        self.events = events

    def __call__(self, bind):
        return self

    def begin(self):
        return _FakeSession(self.events)


@pytest.mark.asyncio
async def test_delete_order_is_redis_then_version_then_mysql(monkeypatch):
    events = []
    monkeypatch.setattr(sweep, "sessionmaker", _FakeSessionmaker(events))
    docs = [ScopedDocument(1, "a.md", (11, 12)), ScopedDocument(2, "b.md", ())]
    await sweep.delete_documents(None, _FakeRedis(events), "about_me", "titan", docs)
    assert [name for name, _ in events] == [
        "redis.delete",
        "redis.incr",
        "mysql.begin",
        "mysql.delete",
        "mysql.delete",
        "mysql.commit",
    ]
    assert events[0][1] == ("chunk:11", "chunk:12", "chunktxt:11", "chunktxt:12")
    assert events[1][1] == "corpus:ver:about_me"
    assert [target for name, target in events if name == "mysql.delete"] == [
        "chunks",
        "documents",
    ]


@pytest.mark.asyncio
async def test_redis_failure_leaves_mysql_untouched(monkeypatch):
    events = []
    monkeypatch.setattr(sweep, "sessionmaker", _FakeSessionmaker(events))
    with pytest.raises(ConnectionError):
        await sweep.delete_documents(
            None, _FakeRedis(events, fail=True), "about_me", "titan", _docs("a.md")
        )
    assert events == []


# --- CLI ---------------------------------------------------------------------


@pytest.fixture
def cli(monkeypatch):
    calls = {}

    async def fake_ingest(**kwargs):
        calls["ingest"] = kwargs
        return ingest_run.RunResult()

    def fake_dry_run(**kwargs):
        calls["dry_run"] = kwargs
        return []

    planned = _docs("corpus/about-me/a.md")

    async def fake_clear(corpus, *, model_id=None, dry_run=False, documents=None, **kwargs):
        calls.setdefault("clear", []).append((corpus, model_id, dry_run))
        calls.setdefault("clear_documents", []).append(documents)
        if calls.get("clear_fails") and not dry_run:
            raise RuntimeError("mysql went away")
        if calls.get("clear_locked_out") and not dry_run:
            return None
        return sweep.ClearResult(planned if documents is None else documents, [])

    calls["planned"] = planned

    monkeypatch.setattr(ingest_run, "ingest", fake_ingest)
    monkeypatch.setattr(ingest_run, "dry_run_sweep", fake_dry_run)
    monkeypatch.setattr(ingest_run, "clear", fake_clear)
    monkeypatch.setenv("GLASSBOX_PROVIDER", "fake")
    return calls


def test_cli_default_ingests_with_the_env_sweep_mode(cli):
    assert ingest_run.main([]) == 0
    assert cli["ingest"] == {"sweep": None, "force_sweep": False}
    assert "clear" not in cli


def test_cli_sweep_flags(cli):
    assert ingest_run.main(["--sweep", "--force-sweep"]) == 0
    assert cli["ingest"] == {"sweep": "apply", "force_sweep": True}
    assert ingest_run.main(["--no-sweep"]) == 0
    assert cli["ingest"]["sweep"] == "off"


def test_cli_dry_run_ingests_nothing(cli):
    assert ingest_run.main(["--dry-run"]) == 0
    assert "dry_run" in cli and "ingest" not in cli


def test_cli_clear_requires_corpus(cli):
    assert ingest_run.main(["--clear"]) == 2
    assert "clear" not in cli


def test_cli_clear_dry_run_never_deletes(cli):
    assert ingest_run.main(["--clear", "--corpus", "about_me", "--dry-run"]) == 0
    assert cli["clear"] == [("about_me", "fake-v1", True)]


def test_cli_clear_refuses_without_yes_when_not_interactive(cli, monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    assert ingest_run.main(["--clear", "--corpus", "about_me"]) == 2
    assert all(dry_run for _, _, dry_run in cli["clear"])


def test_cli_clear_interactive_confirmation_must_match_the_corpus(cli, monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt: "y")
    assert ingest_run.main(["--clear", "--corpus", "about_me"]) == 2
    monkeypatch.setattr("builtins.input", lambda prompt: "about_me")
    assert ingest_run.main(["--clear", "--corpus", "about_me", "--model", "titan"]) == 0
    assert cli["clear"][-1] == ("about_me", "titan", False)


def test_cli_clear_with_yes_deletes(cli):
    assert ingest_run.main(["--clear", "--corpus", "about_system", "--yes"]) == 0
    assert cli["clear"][-1] == ("about_system", "fake-v1", False)


@pytest.mark.asyncio
async def test_ingest_rejects_an_unknown_sweep_mode_before_touching_any_store():
    with pytest.raises(ValueError):
        await ingest_run.ingest(sweep="delete-everything", engine=object(), redis_client=object())


def test_cli_clear_deletes_exactly_the_confirmed_list(cli):
    assert ingest_run.main(["--clear", "--corpus", "about_me", "--yes"]) == 0
    # The dry run lists without a fixed set; the delete gets that same list back.
    assert cli["clear_documents"] == [None, cli["planned"]]


def test_cli_clear_failure_says_to_rerun_clear(cli, capsys):
    cli["clear_fails"] = True
    assert ingest_run.main(["--clear", "--corpus", "about_me", "--yes"]) == 1
    assert "Re-run --clear" in capsys.readouterr().err


@pytest.mark.parametrize(
    "argv",
    [
        ["--clear", "--corpus", "about_me", "--force-sweep"],
        ["--dry-run", "--sweep"],
        ["--dry-run", "--no-sweep"],
        ["--yes"],
        ["--model", "titan"],
    ],
)
def test_cli_rejects_flags_that_would_be_ignored(cli, argv):
    assert ingest_run.main(argv) == 2
    assert "ingest" not in cli and "clear" not in cli and "dry_run" not in cli


def test_cli_dry_run_accepts_a_model(cli):
    assert ingest_run.main(["--dry-run", "--model", "titan"]) == 0
    assert cli["dry_run"]["model_id"] == "titan"


def test_cli_prints_an_unmissable_banner_for_a_refused_sweep(monkeypatch, capsys):
    refused = sweep.SweepPlan("about_system", "titan", known=10, seen_files=0)
    refused.refused = "scan found zero files"

    async def fake_ingest(**kwargs):
        return ingest_run.RunResult(sweep=[refused])

    monkeypatch.setattr(ingest_run, "ingest", fake_ingest)
    assert ingest_run.main([]) == 0
    out = capsys.readouterr()
    for stream in (out.out, out.err):
        assert "!!! STALE SWEEP REFUSED for about_system (titan): scan found zero files" in stream


# --- orphan Redis keys and run notes -------------------------------------------


class _ScanRedis:
    """Fake Redis with hashes (as bytes), strings, scan_iter, hmget pipelines, delete, incr."""

    def __init__(self, hashes):
        self.hashes = hashes
        self.deleted = []
        self.versions = {}
        self.scan_counts = []

    async def scan_iter(self, match, count):
        self.scan_counts.append(count)
        for key in list(self.hashes):
            yield key.encode()

    def pipeline(self, transaction=True):
        assert not transaction  # read-only batches
        return _HmgetPipeline(self)

    async def delete(self, *keys):
        self.deleted.append(keys)

    async def incr(self, key):
        self.versions[key] = self.versions.get(key, 0) + 1


class _HmgetPipeline:
    def __init__(self, redis):
        self.redis, self.keys = redis, []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def hmget(self, key, fields):
        self.keys.append((key, fields))

    async def execute(self):
        return [
            [self.redis.hashes.get(key, {}).get(field) for field in fields]
            for key, fields in self.keys
        ]


def _hash(corpus, model):
    return {"corpus": corpus.encode(), "model": sweep._model_tag(model).encode()}


@pytest.mark.asyncio
async def test_orphan_scan_matches_only_this_corpus_and_model_without_a_mysql_row():
    redis = _ScanRedis(
        {
            "chunk:1": _hash("about_me", "titan"),  # known to MySQL: normal path
            "chunk:2": _hash("about_me", "titan"),  # orphan
            "chunk:3": _hash("about_system", "titan"),  # other corpus
            "chunk:4": _hash("about_me", "other-model"),  # other model
            "chunk:5": {},  # no fields: not matched
            "chunk:x": _hash("about_me", "titan"),  # not a numeric id
            "chunk:7": _hash("about_me", "titan"),  # orphan
        }
    )
    orphans = await sweep.find_orphan_chunk_ids(redis, "about_me", "titan", [1])
    assert orphans == [2, 7]
    assert redis.scan_counts == [sweep.SCAN_BATCH]


@pytest.mark.asyncio
async def test_orphan_scan_batches_its_pipelines(monkeypatch):
    monkeypatch.setattr(sweep, "SCAN_BATCH", 2)
    redis = _ScanRedis({f"chunk:{i}": _hash("about_me", "titan") for i in range(1, 6)})
    assert await sweep.find_orphan_chunk_ids(redis, "about_me", "titan", []) == [1, 2, 3, 4, 5]


@pytest.mark.asyncio
async def test_clear_removes_orphans_and_chunk_text_without_documents(monkeypatch):
    events = []
    monkeypatch.setattr(sweep, "sessionmaker", _FakeSessionmaker(events))
    redis = _ScanRedis({"chunk:9": _hash("about_me", "titan")})
    monkeypatch.setattr(sweep, "load_scope_documents", lambda *a: [])
    monkeypatch.setattr(sweep, "existing_chunk_ids", lambda engine, ids: set())
    result = await sweep.clear_scope(None, redis, "about_me", "titan", dry_run=False)
    assert result.documents == [] and result.orphan_chunk_ids == [9]
    assert redis.deleted == [("chunk:9", "chunktxt:9")]
    assert redis.versions == {"corpus:ver:about_me": 1}
    assert events == []  # nothing for MySQL to do


@pytest.mark.asyncio
async def test_clear_dry_run_counts_orphans_and_deletes_nothing(monkeypatch):
    redis = _ScanRedis(
        {"chunk:91": _hash("about_me", "titan"), "chunk:92": _hash("about_me", "titan")}
    )
    monkeypatch.setattr(sweep, "load_scope_documents", lambda *a: _docs("a.md"))
    monkeypatch.setattr(sweep, "existing_chunk_ids", lambda engine, ids: set())
    result = await sweep.clear_scope(None, redis, "about_me", "titan", dry_run=True)
    assert result.orphan_chunk_ids == [91, 92] and len(result.documents) == 1
    assert redis.deleted == [] and redis.versions == {}


def test_sweep_notes_records_mode_counts_and_refusals():
    deleted = sweep.SweepPlan("about_me", "titan", known=5, seen_files=4, stale=_docs("a.md"))
    deleted.deleted = True
    refused = sweep.SweepPlan(
        "about_system", "titan", known=10, seen_files=5, stale=_docs("b.md", "c.md")
    )
    refused.refused = "too many"
    assert sweep.sweep_notes("apply", [deleted, refused]) == {
        "sweep": {
            "mode": "apply",
            "corpora": [
                {
                    "corpus": "about_me",
                    "model": "titan",
                    "known": 5,
                    "planned": 1,
                    "deleted": 1,
                    "refused": None,
                },
                {
                    "corpus": "about_system",
                    "model": "titan",
                    "known": 10,
                    "planned": 2,
                    "deleted": 0,
                    "refused": "too many",
                },
            ],
        }
    }
    assert sweep.sweep_notes("off", []) == {"sweep": {"mode": "off", "corpora": []}}


@pytest.mark.asyncio
async def test_clear_keeps_orphan_candidates_that_now_have_a_mysql_row(monkeypatch):
    # 91 was written by a concurrent ingest (or belongs to another corpus's row).
    redis = _ScanRedis({f"chunk:{i}": _hash("about_me", "titan") for i in (91, 92)})
    monkeypatch.setattr(sweep, "load_scope_documents", lambda *a: [])
    monkeypatch.setattr(sweep, "existing_chunk_ids", lambda engine, ids: {91} & set(ids))
    result = await sweep.clear_scope(None, redis, "about_me", "titan", dry_run=False)
    assert result.orphan_chunk_ids == [92]
    assert redis.deleted == [("chunk:92", "chunktxt:92")]


def test_existing_chunk_ids_queries_chunks_by_id_in_any_corpus(monkeypatch):
    seen = []

    class Session:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def scalars(self, statement):
            seen.append(str(statement))
            return iter([91])

    monkeypatch.setattr(sweep, "sessionmaker", lambda bind: lambda: Session())
    assert sweep.existing_chunk_ids(None, [91, 92]) == {91}
    assert "chunks.id IN" in seen[0] and "corpus" not in seen[0]


def test_mark_run_failed_records_notes_even_with_no_plans():
    class Run:
        notes = None

    run = Run()

    class Session:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def get(self, model, run_id):
            return run

    class Sessions:
        def begin(self):
            return Session()

    ingest_run._mark_run_failed(Sessions(), 1, ingest_run.RunResult(), "apply")
    assert run.status == "failed"
    assert run.notes == {"sweep": {"mode": "apply", "corpora": []}}


def test_cli_clear_exits_75_when_another_run_holds_the_lock(cli, capsys):
    cli["clear_locked_out"] = True
    assert ingest_run.main(["--clear", "--corpus", "about_me", "--yes"]) == 75
    assert "another ingest or reindex is running" in capsys.readouterr().err
