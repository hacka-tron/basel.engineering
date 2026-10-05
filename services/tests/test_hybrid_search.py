"""Hybrid retrieval (RAG plan phase 8): BM25 + vector legs, RRF, slots, per-document cap."""

import re
import struct
from pathlib import Path
from uuid import uuid4

import pytest
import redis.asyncio as redis
from redis.exceptions import ResponseError

from services.glassbox.cache.answer import _model_tag
from services.glassbox.cache.retrieval import RedisRetrievalCache
from services.glassbox.ingest import redis_index
from services.glassbox.ingest.redis_index import (
    EXPECTED_FIELDS,
    TEXT_VERSION,
    chunk_fields,
    ensure_index,
    index_field_names,
)
from services.glassbox.retrieval import search
from services.glassbox.retrieval.search import (
    EXPERIENCE_BY_SOURCE,
    PERSONAL_PROJECT,
    PROFESSIONAL,
    RETRIEVAL_CONFIGS,
    RetrievalConfig,
    dual_experience_slots,
    experience_of,
    hybrid_search,
    lexical_query,
    lexical_terms,
    rrf_fuse,
    select_chunks,
    tech_question_terms,
)
from services.tests.stack_ports import redis_url_for

REPO_ROOT = Path(__file__).resolve().parents[2]
PRIVATE_ABOUT_ME = REPO_ROOT / "corpus" / "about-me-private" / "about-me"


# ------------------------------------------------------------------ lexical query


def test_lexical_terms_drop_stopwords_short_tokens_and_duplicates():
    assert lexical_terms("What is the KEDA cooldown, and how does a KEDA burst work?") == [
        "keda",
        "cooldown",
        "burst",
        "work",
    ]


def test_lexical_terms_split_identifiers_like_the_indexer():
    # RediSearch indexes demo:load:lock as demo, load, lock and keeps underscores.
    assert lexical_terms("What is demo:load:lock?") == ["demo", "load", "lock"]
    assert lexical_terms("Where is _PLANNED_SOURCE_SIGNAL used?") == [
        "_planned_source_signal",
        "used",
    ]
    assert lexical_terms("retrieval:jobs on t4g.small with 512 MiB") == [
        "retrieval",
        "jobs",
        "t4g",
        "small",
        "512",
        "mib",
    ]


def test_indexed_text_has_no_newlines():
    # RediSearch 7.2 glues words across a newline into one token.
    fields = chunk_fields("about_system", "m", b"v", "docs/a.md", 1, "## Head\nline one\r\n\tline")
    assert fields["text"] == "## Head line one line"


def test_lexical_terms_of_a_stopword_only_question_are_empty():
    assert lexical_terms("What is it?") == []
    assert lexical_terms("") == []


def test_lexical_query_or_joins_terms_with_the_same_filters_as_knn():
    query = lexical_query("about_system", "m", ["demo", "load", "lock"], kinds=["doc"])
    assert query == (
        f"@corpus:{{about_system}} @model:{{{_model_tag('m')}}} @kind:{{doc}} "
        "@text:(demo|load|lock)"
    )
    # Terms are word characters only: nothing a RediSearch query treats as syntax.
    assert re.fullmatch(r"[\w|]+", query.rsplit("@text:(", 1)[1].rstrip(")"))
    with pytest.raises(ValueError):
        lexical_query("about_system", "m", [])


# ------------------------------------------------------------------ fusion and selection


def test_rrf_sums_weighted_reciprocal_ranks_and_keeps_the_vector_score():
    vector = [{"chunk_id": 1, "score": 0.9}, {"chunk_id": 2, "score": 0.8}]
    lexical = [{"chunk_id": 2}, {"chunk_id": 3}]
    fused = rrf_fuse([vector, lexical], k=60, weights=[1.0, 0.5])
    assert [entry["chunk_id"] for entry in fused] == [2, 1, 3]
    by_id = {entry["chunk_id"]: entry for entry in fused}
    assert by_id[2]["rrf"] == pytest.approx(1 / 62 + 0.5 / 61)
    assert by_id[1]["rrf"] == pytest.approx(1 / 61)
    assert by_id[3]["rrf"] == pytest.approx(0.5 / 62)
    assert by_id[2]["score"] == 0.8 and "score" not in by_id[3]
    assert by_id[2]["ranks"] == [2, 1] and by_id[3]["ranks"] == [None, 2]


def test_rrf_with_an_empty_leg_is_the_other_leg():
    vector = [{"chunk_id": 5, "score": 0.5}, {"chunk_id": 6, "score": 0.4}]
    assert [entry["chunk_id"] for entry in rrf_fuse([vector, []])] == [5, 6]
    with pytest.raises(ValueError):
        rrf_fuse([vector, []], weights=[1.0])


def _entries(*paths):
    return [
        {"chunk_id": index, "source_path": path, "score": 1.0}
        for index, path in enumerate(paths, start=1)
    ]


def test_select_caps_chunks_per_document_and_fills_from_the_rest():
    fused = _entries("a.md", "a.md", "a.md", "b.md", "a.md", "c.md")
    chosen = select_chunks(fused, top_k=4, per_document_cap=2)
    assert [entry["chunk_id"] for entry in chosen] == [1, 2, 4, 6]


def test_select_puts_slots_first_and_counts_them_against_the_cap():
    fused = _entries("a.md", "a.md", "b.md", "c.md", "a.md")
    chosen = select_chunks(fused, top_k=3, per_document_cap=2, slot_ids=[5, 99])
    assert [entry["chunk_id"] for entry in chosen] == [5, 1, 3]


def test_experience_comes_from_the_private_file():
    assert experience_of("private/microsoft.md") == PROFESSIONAL
    assert experience_of("private/google.md") == PROFESSIONAL
    assert experience_of("private/projects.md") == PERSONAL_PROJECT
    for path in ("private/skills.md", "private/bio.md", "private/personal.md", "docs/DESIGN.md"):
        assert experience_of(path) is None


def test_dual_slots_take_the_best_chunk_of_each_side_by_fused_rank():
    fused = [
        {"chunk_id": 1, "source_path": "private/skills.md"},
        {"chunk_id": 2, "source_path": "private/projects.md"},
        {"chunk_id": 3, "source_path": "private/google.md"},
        {"chunk_id": 4, "source_path": "private/microsoft.md"},
    ]
    hits = [
        {"chunk_id": 4, "source_path": "private/microsoft.md"},
        {"chunk_id": 3, "source_path": "private/google.md"},
        {"chunk_id": 2, "source_path": "private/projects.md"},
        {"chunk_id": 1, "source_path": "private/skills.md"},
    ]
    assert dual_experience_slots(fused, hits) == [3, 2]


def test_dual_slots_skip_a_side_the_data_lacks():
    fused = [{"chunk_id": 1, "source_path": "private/projects.md"}]
    hits = [{"chunk_id": 1, "source_path": "private/projects.md"}]
    assert dual_experience_slots(fused, hits) == [1]
    assert dual_experience_slots(fused, []) == []


# ------------------------------------------------------------------ tech-question detector


@pytest.mark.parametrize(
    ("question", "first_term"),
    [
        ("Have you used Redis?", "redis"),
        ("Have you worked with gRPC?", "grpc"),
        ("Do you know Python?", "python"),
        ("What's your experience with React?", "react"),
        ("Kubernetes in production?", "kubernetes"),
        ("Have you run k8s in production?", "kubernetes"),
        ("Kafka?", "kafka"),
        ("Are you familiar with Terraform?", "terraform"),
    ],
)
def test_tech_questions_fire(question, first_term):
    assert tech_question_terms(question)[0] == first_term


@pytest.mark.parametrize(
    "question",
    [
        "Where do you work?",
        "What do you do for fun?",
        "What did you study in college?",
        "Which programming languages do you like?",
        "How can I contact you?",
        "What does Basel work on at Microsoft?",
        "How did you reduce outages with distributed rate limiting?",
        "Are you open to freelance work?",
        "Why should I hire you?",
        "What is your biggest achievement?",
        "Tell me about Redis caching in Glassbox and its TTLs",
        "Do you have any pets?",
        "Have you used Go?",  # "go" is too common a word to be a technology here
        "What is your working style?",
    ],
)
def test_ordinary_questions_do_not_fire(question):
    assert tech_question_terms(question) == []


def test_tech_terms_include_every_spelling():
    assert tech_question_terms("Kubernetes in production?") == [
        "kubernetes",
        "k8s",
        "k3s",
        "eks",
        "aks",
        "gke",
    ]


@pytest.mark.skipif(not PRIVATE_ABOUT_ME.is_dir(), reason="no private About Basel checkout")
def test_experience_map_matches_the_private_front_matter():
    """Every role file is professional and every project file personal_project."""
    for path in sorted(PRIVATE_ABOUT_ME.glob("*.md")):
        match = re.search(r"^type:\s*(\w+)", path.read_text(), re.MULTILINE)
        kind = match.group(1) if match else None
        expected = {"role": PROFESSIONAL, "project": PERSONAL_PROJECT}.get(kind)
        assert EXPERIENCE_BY_SOURCE.get(f"private/{path.name}") == expected, path.name


# ------------------------------------------------------------------ hybrid_search with fakes


class _FakeSearchRedis:
    """Answers the KNN, BM25 and tech-term FT.SEARCH calls from canned lists."""

    def __init__(self, vector, lexical, tech=(), lexical_error=False, vectors=None):
        self.vector, self.lexical, self.tech = vector, lexical, list(tech)
        self.lexical_error = lexical_error
        self.vectors = vectors or {}
        self.queries = []

    async def execute_command(self, *args):
        query = args[2]
        self.queries.append(query)
        if "KNN" in query:
            rows = [
                (chunk_id, [b"distance", str(1 - score).encode(), b"source_path", path.encode()])
                for chunk_id, score, path in self.vector
            ]
        else:
            if self.lexical_error:
                raise ResponseError("Unknown field at offset 0 near text")
            source = self.tech if query.endswith("@text:(redis)") else self.lexical
            rows = [(chunk_id, [b"source_path", path.encode()]) for chunk_id, path in source]
        reply = [len(rows)]
        for chunk_id, fields in rows:
            reply += [f"chunk:{chunk_id}".encode(), fields]
        return reply

    def pipeline(self, transaction=False):
        return _FakePipeline(self.vectors)


class _FakePipeline:
    def __init__(self, vectors):
        self.vectors, self.keys = vectors, []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def hget(self, key, field):
        self.keys.append(key)

    async def execute(self):
        return [self.vectors.get(key) for key in self.keys]


EMBEDDING = [1.0] + [0.0] * 511


@pytest.mark.asyncio
async def test_hybrid_fuses_both_legs_and_scores_lexical_only_chunks_by_cosine():
    client = _FakeSearchRedis(
        vector=[(1, 0.9, "docs/a.md"), (2, 0.8, "docs/b.md")],
        lexical=[(3, "docs/c.md"), (2, "docs/b.md")],
        vectors={"chunk:3": struct.pack("512f", *([0.6, 0.8] + [0.0] * 510))},
    )
    legs = {}
    chosen = await hybrid_search(
        client, EMBEDDING, "What is demo:load:lock?", "about_system", "m", legs=legs
    )
    assert [entry["chunk_id"] for entry in chosen] == [2, 1, 3]
    assert chosen[2]["score"] == pytest.approx(0.6)
    assert client.queries[1].endswith("@text:(demo|load|lock)")
    assert [entry["chunk_id"] for entry in legs["lexical"]] == [3, 2]


@pytest.mark.asyncio
async def test_vector_anchor_keeps_the_best_vector_hits_in_fused_order():
    # Chunk 1 is the vector leg's best hit but absent from BM25; four chunks found by
    # both legs outrank it under RRF. With an anchor of 1 it stays in the top 3.
    vector = [(1, 0.9, "docs/a.md"), (2, 0.8, "docs/b.md"), (3, 0.7, "docs/c.md")]
    vector += [(4, 0.6, "docs/d.md")]
    lexical = [(2, "docs/b.md"), (3, "docs/c.md"), (4, "docs/d.md")]
    question = "What is demo:load:lock?"
    plain = RetrievalConfig(top_k=3, per_document_cap=3)
    anchored = RetrievalConfig(top_k=3, per_document_cap=3, vector_anchor=1)
    client = _FakeSearchRedis(vector=vector, lexical=lexical)
    chosen = await hybrid_search(client, EMBEDDING, question, "about_system", "m", plain)
    assert [entry["chunk_id"] for entry in chosen] == [2, 3, 4]
    chosen = await hybrid_search(client, EMBEDDING, question, "about_system", "m", anchored)
    assert [entry["chunk_id"] for entry in chosen] == [2, 3, 1]


def test_production_configs():
    system = RETRIEVAL_CONFIGS["about_system"]
    assert (system.top_k, system.per_document_cap, system.vector_anchor) == (8, 3, 4)
    about_me = RETRIEVAL_CONFIGS["about_me"]
    assert (about_me.top_k, about_me.per_document_cap, about_me.dual_experience) == (6, 2, True)


@pytest.mark.asyncio
async def test_hybrid_serves_vector_results_when_the_text_field_is_missing():
    client = _FakeSearchRedis(vector=[(1, 0.9, "docs/a.md")], lexical=[], lexical_error=True)
    chosen = await hybrid_search(client, EMBEDDING, "What is KEDA?", "about_system", "m")
    assert [entry["chunk_id"] for entry in chosen] == [1]


@pytest.mark.asyncio
async def test_hybrid_with_no_lexical_terms_skips_the_bm25_query():
    client = _FakeSearchRedis(vector=[(1, 0.9, "docs/a.md")], lexical=[])
    chosen = await hybrid_search(client, EMBEDDING, "What is it?", "about_system", "m")
    assert [entry["chunk_id"] for entry in chosen] == [1]
    assert len(client.queries) == 1


@pytest.mark.asyncio
async def test_about_basel_tech_question_gets_a_professional_and_a_project_slot():
    vector = [
        (1, 0.9, "private/skills.md"),
        (2, 0.85, "private/bio.md"),
        (3, 0.8, "private/personal.md"),
        (4, 0.7, "private/skills.md"),
        (5, 0.6, "private/bio.md"),
        (6, 0.55, "private/personal.md"),
        (7, 0.5, "private/microsoft.md"),
        (8, 0.4, "private/projects.md"),
    ]
    client = _FakeSearchRedis(
        vector=vector,
        lexical=[(1, "private/skills.md")],
        tech=[(8, "private/projects.md"), (7, "private/microsoft.md")],
    )
    legs = {}
    chosen = await hybrid_search(
        client, EMBEDDING, "Have you used Redis?", "about_me", "m", legs=legs
    )
    ids = [entry["chunk_id"] for entry in chosen]
    assert ids[:2] == [7, 8]  # professional slot, then personal project
    assert len(ids) == RETRIEVAL_CONFIGS["about_me"].top_k
    assert legs["tech_terms"] == ["redis"] and legs["slots"] == [7, 8]


@pytest.mark.asyncio
async def test_slots_apply_only_to_about_basel():
    vector = [(index, 1 - index / 10, f"docs/{index}.md") for index in range(1, 10)]
    client = _FakeSearchRedis(vector=vector, lexical=[])
    chosen = await hybrid_search(client, EMBEDDING, "Have you used Redis?", "about_system", "m")
    assert [entry["chunk_id"] for entry in chosen] == list(range(1, 9))
    assert len(client.queries) == 2  # KNN and BM25, no tech-term query


def test_retrieval_cache_key_changes_with_the_query_and_keeps_the_old_key_without_one():
    cache = RedisRetrievalCache(None)
    vector = [0.25] * 512
    plain = cache.key("about_me", 1, "m", vector)
    assert cache.key("about_me", 1, "m", vector, query="") == plain
    hybrid = cache.key("about_me", 1, "m", vector, query="hybrid-v1\0redis")
    assert hybrid != plain
    assert hybrid != cache.key("about_me", 1, "m", vector, query="hybrid-v1\0kafka")


# ------------------------------------------------------------------ real Redis Stack


@pytest.mark.asyncio
async def test_text_field_migration_and_bm25_on_real_redis(monkeypatch):
    """An index without ``text`` gains it via FT.ALTER; a rewritten key is then found
    by a whole natural-language question and by identifiers."""
    client = redis.from_url(redis_url_for())
    try:
        await client.ping()
    except Exception as exc:
        await client.aclose()
        pytest.skip(f"local Redis Stack unavailable: {exc}")
    token = uuid4().hex[:12]
    name, prefix = f"idx:p8test:{token}", f"p8test:{token}:"
    monkeypatch.setattr(redis_index, "INDEX_NAME", name)
    monkeypatch.setattr(search, "INDEX_NAME", name)
    model = f"m-{token}"
    packed = struct.pack("512f", *EMBEDDING)
    old_schema = [
        part for field, args in EXPECTED_FIELDS if field != "text" for part in (field, *args)
    ]
    texts = {
        1: "## Demo load\nThe demo:load:lock key stops two load demos at once.",
        2: "## Planned markers\n_PLANNED_SOURCE_SIGNAL marks sources about unbuilt work.",
        3: "## Queue\nJobs wait in the retrieval:jobs stream for a worker.",
    }
    try:
        await client.execute_command(
            "FT.CREATE", name, "ON", "HASH", "PREFIX", "1", prefix, "SCHEMA", *old_schema
        )
        old = chunk_fields("about_system", model, packed, "docs/a.md", 1, texts[1])
        del old["text"], old["text_v"]
        await client.hset(f"{prefix}1", mapping=old)  # written before phase 8
        assert await ensure_index(client) == frozenset({"text"})
        assert "text" in index_field_names(await client.execute_command("FT.INFO", name))

        async def lexical(question):
            hits = await search.lexical_search(
                client, lexical_terms(question), "about_system", model
            )
            return [hit["chunk_id"] for hit in hits]

        # Keys stay readable by KNN but have no text until reconcile rewrites them.
        assert await lexical("What does demo:load:lock do?") == []
        for chunk_id, text in texts.items():
            fields = chunk_fields("about_system", model, packed, f"docs/{chunk_id}.md", 1, text)
            assert fields["text_v"] == TEXT_VERSION
            await client.hset(f"{prefix}{chunk_id}", mapping=fields)
        # Redis keys and the index use the test prefix; search parses the id suffix.
        monkeypatch.setattr(search, "_rows", _prefixed_rows(prefix), raising=True)
        assert (await lexical("How does the demo:load:lock key work during a demo?"))[0] == 1
        assert (await lexical("Where is _PLANNED_SOURCE_SIGNAL defined?"))[0] == 2
        assert (await lexical("What reads the retrieval:jobs stream?"))[0] == 3
        assert await lexical("What is the capital of France?") == []
        chosen = await hybrid_search(
            client,
            EMBEDDING,
            "Which stream holds retrieval:jobs?",
            "about_system",
            model,
            RetrievalConfig(top_k=2, per_document_cap=1),
        )
        assert chosen[0]["chunk_id"] == 3 and len(chosen) == 2
    finally:
        try:
            await client.execute_command("FT.DROPINDEX", name, "DD")
        finally:
            await client.aclose()


def _prefixed_rows(prefix):
    original = search._rows

    def rows(response):
        renamed = [response[0]]
        for key, fields in zip(response[1::2], response[2::2], strict=True):
            text = key.decode() if isinstance(key, bytes) else key
            renamed += ["chunk:" + text.removeprefix(prefix), fields]
        return original(renamed)

    return rows
