"""Only real, grounded answers reach the semantic answer cache."""

import json
import struct

import pytest
from fastapi.testclient import TestClient

from services.glassbox.api.main import app
from services.glassbox.cache.answer import RedisAnswerCache
from services.glassbox.cache.cacheability import uncacheable_reason
from services.glassbox.providers.base import ABSTENTION_ANSWER, is_abstention
from services.glassbox.providers.fake import FAKE_ABSTAIN_MARKER, FakeLLMProvider
from services.tests.test_ask_endpoint import MemoryRedis, events

CHUNKS = [{"chunk_id": 42, "n": 1}]


@pytest.fixture(autouse=True)
def allow_all_limits(monkeypatch):
    from services.glassbox.api import ask

    class AllowAll:
        async def allow(self, client_hash):
            return True, 0

        async def reserve(self):
            return True

    monkeypatch.setattr(ask, "get_rate_limiter", lambda client: AllowAll())
    monkeypatch.setattr(ask, "get_daily_budget", lambda client: AllowAll())


@pytest.mark.parametrize(
    "answer",
    [
        ABSTENTION_ANSWER,
        "I don't know from what I have",
        "  i DON'T know   from what I have!  ",
        "I don’t know from what I have.",
        "I do not know from what I have.",
        '"I don\'t know from what I have."',
        "I don't know from what I have. The sources cover other topics.",
        "I don't know from what I have, but the sources mention Redis.",
        # Drift from the requested sentence must not be cached either.
        "I don't know from the provided sources.",
        "The sources don't say whether the queue is live.",
        "The provided sources do not mention the queue.",
        "None of the sources describe the queue.",
        "There is no information about the queue in the sources.",
        "I cannot answer that from the sources.",
        "I have no information about the queue.",
        "It is unclear whether the queue is live.",
        "Unfortunately, I cannot answer that from the sources.",
        # Intentionally an abstention: a hedge that opens with "I don't know".
        "I don't know from what I have learned so far whether the ASG is live.",
    ],
)
def test_abstention_variants_are_detected(answer):
    assert is_abstention(answer)
    assert uncacheable_reason(answer, CHUNKS) == "abstention"


@pytest.mark.parametrize(
    "answer",
    [
        "The queue is a Redis Stream named retrieval:jobs.",
        "I know the queue is a Redis Stream.",
        "Workers read the queue; I don't know from what I have is not an answer here.",
        "The sources describe the queue as a Redis Stream.",
        # A refusal opener that goes on to answer from the sources is an answer.
        "None of the sources mention X, but they show the queue is a Redis Stream.",
        "I don't know of any queue failures; Redis Streams is live and workers read it.",
        "It is unclear from the logs how often it runs. The worker reads retrieval:jobs. "
        "KEDA scales it from 1 to 3.",
        "Sources show the worker reads retrieval:jobs. It does not say more.",
    ],
)
def test_real_answers_are_cacheable(answer):
    assert not is_abstention(answer)
    assert uncacheable_reason(answer, CHUNKS) is None


@pytest.mark.parametrize(
    ("answer", "chunks", "reason"),
    [
        ("", CHUNKS, "empty"),
        ("   \n\t", CHUNKS, "empty"),
        (None, CHUNKS, "empty"),
        ("A real answer.", [], "no_sources"),
        ("A real answer.", None, "no_sources"),
    ],
)
def test_empty_answers_and_sourceless_answers_are_not_cacheable(answer, chunks, reason):
    assert uncacheable_reason(answer, chunks) == reason


class StubSearchClient:
    """Enough of redis.asyncio for RedisAnswerCache.get to find one exact match."""

    def __init__(self, payload):
        self.payload = payload

    async def execute_command(self, *args):
        if args[0] == "FT.INFO":
            return []
        return [1, b"ans2:about_system:entry", [b"distance", b"0"]]

    async def hget(self, key, field):
        return json.dumps({**self.payload, "sources": {"42": "sha-of-42"}})

    def pipeline(self, transaction=True):
        return _UnchangedSources()


class _UnchangedSources:
    """Every source chunk still holds the text the answer was built from."""

    def __init__(self):
        self.calls = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def hget(self, key, field):
        self.calls += 1

    async def execute(self):
        return [b"sha-of-42"] * self.calls


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("payload", "hit"),
    [
        ({"answer": ABSTENTION_ANSWER, "chunks": CHUNKS}, False),
        ({"answer": "i don't know from what i have", "chunks": CHUNKS}, False),
        ({"answer": "", "chunks": CHUNKS}, False),
        ({"answer": "The queue is a Redis Stream.", "chunks": CHUNKS}, True),
    ],
)
async def test_legacy_cached_refusals_read_as_a_miss(payload, hit):
    cache = RedisAnswerCache(StubSearchClient(payload))
    result = await cache.get("about_system", "model", [0.0] * 511 + [1.0])
    assert result == ({**payload, "sources": {"42": "sha-of-42"}} if hit else None)


class RecordingAnswerCache:
    def __init__(self):
        self.values = {}
        self.puts = []
        self.puts_model_ids = []
        self.model_ids = []

    async def get(self, corpus, model_id, vector):
        self.model_ids.append(model_id)
        return self.values.get((corpus, model_id, struct.pack("512f", *vector)))

    async def put(self, corpus, model_id, vector, payload):
        self.puts.append(payload)
        self.puts_model_ids.append(model_id)
        self.values[(corpus, model_id, struct.pack("512f", *vector))] = payload


class ScriptedLLM(FakeLLMProvider):
    def __init__(self, reply=None):
        self.reply = reply
        self.calls = 0

    async def generate(self, prompt, *, max_tokens, system=None):
        self.calls += 1
        if self.reply is not None:
            if self.reply:
                yield self.reply
            return
        async for part in super().generate(prompt, max_tokens=max_tokens, system=system):
            yield part


def _ask_twice(monkeypatch, llm, question="How does the queue work?"):
    from services.glassbox.api import ask

    cache = RecordingAnswerCache()
    saved = []
    monkeypatch.setenv("REDIS_URL", "redis://unused")
    redis_client = MemoryRedis()
    monkeypatch.setattr(ask.redis, "from_url", lambda url: redis_client)
    monkeypatch.setattr(ask, "get_answer_cache", lambda client: cache)
    monkeypatch.setattr(ask, "get_llm_provider", lambda: llm)
    monkeypatch.setattr(ask, "_save_query", lambda **kwargs: saved.append(kwargs))
    http = TestClient(app)
    body = {"question": question, "corpus": "about_me"}
    streams = [events(http.post("/api/ask", json=body)) for _ in range(2)]
    dones = [next(data for name, data in stream if name == "done") for stream in streams]
    return cache, saved, streams, dones


@pytest.mark.parametrize("reply", [ABSTENTION_ANSWER, "I don't know from what I have", "", "   "])
def test_refusals_and_empty_answers_are_never_cached(monkeypatch, reply):
    llm = ScriptedLLM(reply)
    cache, saved, _, dones = _ask_twice(monkeypatch, llm)
    assert cache.puts == []
    assert [done["answer_cache"] for done in dones] == ["miss", "miss"]
    assert llm.calls == 2
    for row in saved:
        assert row["timings"]["answer_cache_skipped"] == 1
        assert row["timings"].get("abstained") == (1 if reply.strip() else None)


@pytest.mark.parametrize(
    ("reply", "flag"),
    [
        (ABSTENTION_ANSWER, True),
        ("i dont know from what i have", True),
        ("I do not know from what I have.", True),
        # Loose abstentions (kept out of the cache) that still say something: not flagged.
        ("I don't know from what I have, but he did build a RAG system on k3s.", False),
        ("It is unclear whether he used Kafka. He used Redis streams for the queue.", False),
        ("The sources don't say. Basel built it on k3s.", False),
        ("A real answer about the queue.", False),
    ],
)
def test_done_flags_only_the_exact_abstention_sentence(monkeypatch, reply, flag):
    llm = ScriptedLLM(reply)
    _, _, _, dones = _ask_twice(monkeypatch, llm)
    assert [done["abstained"] for done in dones] == [flag, flag]


def test_fake_provider_abstains_on_marker_and_the_refusal_is_not_cached(monkeypatch):
    llm = ScriptedLLM()
    cache, saved, streams, dones = _ask_twice(
        monkeypatch, llm, question=f"{FAKE_ABSTAIN_MARKER} How does the queue work?"
    )
    answer = "".join(data["text"] for name, data in streams[0] if name == "token")
    assert answer == ABSTENTION_ANSWER
    assert cache.puts == []
    assert [done["answer_cache"] for done in dones] == ["miss", "miss"]
    assert all(row["timings"]["abstained"] == 1 for row in saved)
    assert [done["abstained"] for done in dones] == [True, True]


def test_normal_answer_is_cached_under_the_current_prompt_version(monkeypatch):
    from services.glassbox.api import ask

    assert ask._PROMPT_VERSION == "v21"
    llm = ScriptedLLM()
    cache, saved, _, dones = _ask_twice(monkeypatch, llm)
    assert len(cache.puts) == 1
    assert cache.puts[0]["answer"] == "This is a fake response for local development."
    assert [done["answer_cache"] for done in dones] == ["miss", "hit"]
    assert llm.calls == 1
    # Prompt v18: the answer route is the last part of the identity; reads look up
    # both routes, the write goes under the route that answered.
    assert all(model_id.split("|")[-2] == "v21" for model_id in cache.model_ids)
    assert {model_id.split("|")[-1] for model_id in cache.model_ids} == {"strict", "casual"}
    assert cache.puts_model_ids == [cache.model_ids[0]]
    assert cache.model_ids[0].endswith("|v21|strict")
    assert "answer_cache_skipped" not in saved[0]["timings"]
    assert "abstained" not in saved[0]["timings"]
    assert [done["abstained"] for done in dones] == [False, False]


def test_prompt_version_is_part_of_the_answer_cache_key(monkeypatch):
    from services.glassbox.api import ask

    llm = ScriptedLLM()
    cache, _, _, _ = _ask_twice(monkeypatch, llm)
    assert len(cache.puts) == 1
    # Same question, same models, older prompt version: the v15 entry is unreachable.
    monkeypatch.setattr(ask, "_PROMPT_VERSION", "v14")
    monkeypatch.setattr(ask, "get_answer_cache", lambda client: cache)
    stream = events(
        TestClient(app).post(
            "/api/ask", json={"question": "How does the queue work?", "corpus": "about_me"}
        )
    )
    assert next(data for name, data in stream if name == "done")["answer_cache"] == "miss"
    assert cache.model_ids[-1].endswith("|v14|casual")


class TemperatureLLM(ScriptedLLM):
    def __init__(self):
        super().__init__()
        self.temperatures = []

    async def generate(self, prompt, *, max_tokens, system=None, temperature=None):
        self.temperatures.append(temperature)
        async for part in super().generate(prompt, max_tokens=max_tokens, system=system):
            yield part


def test_the_casual_route_is_part_of_the_cache_key_and_the_trace(monkeypatch):
    """Prompt v18: a casual answer is cached under the casual identity only, replayed
    from it, and the route name (only) shows in the trace."""
    from services.glassbox.api import ask

    monkeypatch.setattr(ask, "answer_route", lambda chunks, corpus, question="": ask.CASUAL_ROUTE)
    llm = TemperatureLLM()
    cache, saved, streams, dones = _ask_twice(monkeypatch, llm)
    assert len(cache.puts_model_ids) == 1
    assert cache.puts_model_ids[0].endswith("|v21|casual")
    assert [done["answer_cache"] for done in dones] == ["miss", "hit"]
    assert llm.temperatures == [ask.CASUAL_TEMPERATURE]
    assert saved[0]["timings"]["answer_route_casual"] == 1
    llm_start = next(
        data
        for name, data in streams[0]
        if name == "stage" and data["node"] == "llm" and data["status"] == "start"
    )
    assert llm_start["route"] == "casual"
    hit = next(
        data for name, data in streams[1] if name == "stage" and data["node"] == "answer_cache"
    )
    assert hit["cache"] == "hit" and hit["route"] == "casual"


def test_a_strict_answer_is_never_replayed_from_the_casual_identity(monkeypatch):
    llm = TemperatureLLM()
    cache, _, _, dones = _ask_twice(monkeypatch, llm)
    assert cache.puts_model_ids[0].endswith("|v21|strict")
    # Strict answers use the provider's default temperature (0): none is passed.
    assert llm.temperatures == [None]
    assert dones[1]["answer_cache"] == "hit"


def test_grounding_rules_ask_for_the_exact_abstention_sentence():
    from services.glassbox.providers.base import GROUNDING_RULES

    assert f'exactly "{ABSTENTION_ANSWER}" and nothing else' in GROUNDING_RULES
    assert "Design prose alone is not evidence" not in GROUNDING_RULES
    assert "labelled code, Kubernetes manifest or infrastructure" in GROUNDING_RULES


def test_no_sources_refusal_is_flagged_in_the_query_log(monkeypatch):
    from services.glassbox.api import ask

    saved = []
    monkeypatch.setenv("REDIS_URL", "redis://unused")
    redis_client = MemoryRedis(outcome="empty")
    monkeypatch.setattr(ask.redis, "from_url", lambda url: redis_client)
    monkeypatch.setattr(ask, "get_answer_cache", lambda client: RecordingAnswerCache())
    monkeypatch.setattr(ask, "_save_query", lambda **kwargs: saved.append(kwargs))
    stream = events(
        TestClient(app).post("/api/ask", json={"question": "Anything?", "corpus": "about_me"})
    )
    assert "".join(d["text"] for n, d in stream if n == "token") == ABSTENTION_ANSWER
    assert saved[0]["timings"]["abstained"] == 1
    assert saved[0]["timings"]["answer_cache_skipped"] == 1
    assert next(d for n, d in stream if n == "done")["abstained"] is True


class FilteredLLM(FakeLLMProvider):
    """Stops like Bedrock's content filter, optionally after some streamed text."""

    def __init__(self, before=""):
        self.before = before
        self.calls = 0

    async def generate(self, prompt, *, max_tokens, system=None):
        from services.glassbox.providers.base import ContentFilteredError

        self.calls += 1
        if self.before:
            yield self.before
        raise ContentFilteredError("Bedrock generation stopped: content_filtered")


def test_content_filter_stop_becomes_an_uncached_abstention(monkeypatch):
    llm = FilteredLLM()
    cache, saved, streams, dones = _ask_twice(monkeypatch, llm)
    for stream in streams:
        assert not any(name == "error" for name, _ in stream)
        tokens = "".join(data["text"] for name, data in stream if name == "token")
        assert tokens == ABSTENTION_ANSWER
    assert [done["abstained"] for done in dones] == [True, True]
    assert cache.puts == [] and llm.calls == 2
    for row in saved:
        assert row["timings"]["content_filtered"] == 1
        assert row["timings"]["answer_cache_skipped"] == 1


def test_content_filter_after_partial_text_keeps_it_and_skips_the_cache(monkeypatch):
    llm = FilteredLLM(before="The queue is a Redis Stream.")
    cache, saved, streams, dones = _ask_twice(monkeypatch, llm)
    tokens = "".join(data["text"] for name, data in streams[0] if name == "token")
    assert tokens == "The queue is a Redis Stream."
    assert dones[0]["abstained"] is False
    assert cache.puts == []
    assert saved[0]["timings"]["content_filtered"] == 1


def test_v17_memory_phrasing_is_a_refusal_only_on_its_own():
    # Prompt v17: gaps get "I don't have that in my memory". Alone it is a refusal
    # (never cached); followed by what the sources do say, it is a real answer.
    assert is_abstention("I don't have that in my memory.")
    assert is_abstention("Sorry, I don't have that in my memory.")
    # Absent tech (round 1 review): the memory phrase, never a cached denial.
    assert is_abstention("I don't have Go in my memory.")
    # Review leftovers: a long tech name and an opener before the phrase.
    assert is_abstention("I don't have Ruby on Rails or Go in my memory.")
    assert is_abstention("Hmm, I don't have Go in my memory.")
    assert is_abstention("Well, honestly, I don't have Ruby on Rails or Go in my memory.")
    assert not is_abstention("Hmm, I used Go in my personal project, a rate limiter.")
    assert not is_abstention("Well, I don't have Go in my memory, but I know Python well.")
    assert not is_abstention(
        "I don't have a lot of free time, but I built the whole system and keep it in my memory."
    )
    assert not is_abstention("Hmm, at Google I built tests; I don't have Go in my memory.")
    assert not is_abstention("No, I don't write Go.")  # the phrasing the prompt avoids
    assert not is_abstention("I don't have that in my memory, but at Google I built tests.")
    assert not is_abstention(
        "Yes, I used it in my personal project; professional use of it isn't in my memory."
    )


@pytest.mark.parametrize(
    ("reply", "flags"),
    [
        ("I built the queue on Redis streams.", set()),
        ("Basel built the queue on Redis streams.", {"answer_check_third_person"}),
        ("I built it. " + "word " * 90, {"answer_check_too_long"}),
    ],
)
def test_v17_answer_checks_are_logged_and_the_answer_still_streams(monkeypatch, reply, flags):
    # Logged in stage_timings_ms, never regenerated: the answer has already streamed.
    llm = ScriptedLLM(reply)
    cache, saved, streams, _ = _ask_twice(monkeypatch, llm)
    tokens = "".join(data["text"] for name, data in streams[0] if name == "token")
    assert tokens == reply
    logged = {key for key in saved[0]["timings"] if key.startswith("answer_check_")}
    assert logged == flags
    assert len(cache.puts) == 1  # a flagged answer is still cached (temperature 0)
