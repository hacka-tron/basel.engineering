"""Stale-sweep and --clear decision logic, with fakes (no MySQL or Redis needed)."""

import pytest

from services.glassbox.ingest import run as ingest_run
from services.glassbox.ingest import sweep
from services.glassbox.ingest.sweep import ScopedDocument, plan_sweep, scope_documents


def _docs(*paths: str) -> list[ScopedDocument]:
    return [ScopedDocument(index, path, (index * 10,)) for index, path in enumerate(paths, 1)]


def test_plan_has_nothing_stale_when_every_known_file_was_seen():
    plan = plan_sweep("about_system", "m", _docs("a.md", "b.md"), {"a.md", "b.md", "new.md"})
    assert plan.stale == [] and plan.refused is None
    assert (plan.known, plan.seen_files) == (2, 3)


def test_plan_lists_missing_files_within_the_threshold():
    known = _docs(*(f"f{i}.md" for i in range(10)))
    seen = {f"f{i}.md" for i in range(10)} - {"f3.md", "f7.md", "f1.md"}
    plan = plan_sweep("about_system", "m", known, seen, max_fraction=0.3)
    assert [doc.source_path for doc in plan.stale] == ["f1.md", "f3.md", "f7.md"]
    assert plan.refused is None


def test_plan_refuses_a_drop_above_the_threshold_unless_forced():
    known = _docs(*(f"f{i}.md" for i in range(10)))
    seen = {f"f{i}.md" for i in range(6)}  # 4 of 10 missing = 40%
    refused = plan_sweep("about_system", "m", known, seen, max_fraction=0.3)
    assert refused.refused and "40%" in refused.refused and len(refused.stale) == 4
    forced = plan_sweep("about_system", "m", known, seen, max_fraction=0.3, force=True)
    assert forced.refused is None and len(forced.stale) == 4


def test_plan_always_allows_a_small_absolute_sweep():
    # Renaming two of the five About Basel files is 40% but must still work.
    plan = plan_sweep("about_me", "m", _docs("a", "b", "c", "d", "e"), {"a", "b", "c"})
    assert plan.refused is None and len(plan.stale) == sweep.ALWAYS_ALLOWED_STALE


def test_plan_refuses_when_the_scan_found_zero_files_even_when_forced():
    for known in (_docs("a.md"), _docs(*(f"f{i}" for i in range(20)))):
        plan = plan_sweep("about_me", "m", known, set(), force=True)
        assert plan.refused and "zero files" in plan.refused


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
    assert recorder.loaded == [("about_me", "titan"), ("about_system", "titan")]
    assert recorder.deleted == [("about_me", "titan", ["corpus/about-me/gone.md"])]
    by_corpus = {plan.corpus: plan for plan in plans}
    assert by_corpus["about_me"].deleted and not by_corpus["about_me"].refused
    assert by_corpus["about_system"].refused and not by_corpus["about_system"].deleted


@pytest.mark.asyncio
async def test_report_mode_never_deletes(recorder, caplog):
    caplog.set_level("INFO", logger="services.glassbox.ingest.sweep")
    plans = await sweep.run_sweep(None, None, SEEN, "titan", mode="report")
    assert recorder.deleted == []
    assert [len(plan.stale) for plan in plans] == [1, 5]
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
    assert events[0][1] == ("chunk:11", "chunk:12")
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

    async def fake_clear(corpus, *, model_id=None, dry_run=False, **kwargs):
        calls.setdefault("clear", []).append((corpus, model_id, dry_run))
        return _docs("corpus/about-me/a.md")

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
