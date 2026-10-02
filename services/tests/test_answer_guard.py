"""Answer-time personal-data guard on the real /api/ask streaming path."""

import pytest

from services.glassbox.cache.cacheability import uncacheable_reason
from services.glassbox.privacy import REDACTION
from services.glassbox.providers.fake import FakeLLMProvider
from services.tests.test_answer_cacheability import (  # noqa: F401 (fixture)
    CHUNKS,
    _ask_twice,
    allow_all_limits,
)


class TokenLLM(FakeLLMProvider):
    """Streams a fixed answer in the given token pieces."""

    def __init__(self, parts):
        self.parts = parts
        self.calls = 0

    async def generate(self, prompt, *, max_tokens, system=None):
        self.calls += 1
        for part in self.parts:
            yield part


def _tokens(stream):
    return [data["text"] for name, data in stream if name == "token"]


def test_a_phone_number_split_across_llm_tokens_is_masked_before_streaming(monkeypatch):
    llm = TokenLLM(["You can reach Basel at +1 (", "614", ") 555", "-01", "00 any", " day."])
    cache, saved, streams, dones = _ask_twice(monkeypatch, llm)
    for stream in streams:
        tokens = _tokens(stream)
        assert "".join(tokens) == f"You can reach Basel at {REDACTION} any day."
        # No single SSE token frame ever carried a digit of the number.
        assert not any(char.isdigit() for token in tokens for char in token)
    # Never cached: both requests generated, nothing was written.
    assert cache.puts == []
    assert llm.calls == 2
    assert [done["answer_cache"] for done in dones] == ["miss", "miss"]
    assert all(row["timings"]["answer_pii_masked"] == 1 for row in saved)
    assert all(row["timings"]["answer_cache_skipped"] == 1 for row in saved)


def test_an_ssn_in_a_single_token_is_masked_and_not_cached(monkeypatch):
    llm = TokenLLM(["His SSN is 123-45-6789."])
    cache, saved, streams, _ = _ask_twice(monkeypatch, llm)
    assert "".join(_tokens(streams[0])) == f"His SSN is {REDACTION}."
    assert cache.puts == []


def test_ordinary_answers_with_numbers_stream_unchanged_and_are_cached(monkeypatch):
    answer = "Basel cut CPU by 53% in 2024 and ran 10,000+ tests (v1.2.3, 2026-10-01)."
    llm = TokenLLM([answer[:20], answer[20:41], answer[41:]])
    cache, saved, streams, dones = _ask_twice(monkeypatch, llm)
    assert "".join(_tokens(streams[0])) == answer
    assert len(cache.puts) == 1 and cache.puts[0]["answer"] == answer
    assert [done["answer_cache"] for done in dones] == ["miss", "hit"]
    assert "answer_pii_masked" not in saved[0]["timings"]


def test_a_cached_answer_holding_a_phone_is_masked_on_the_way_out(monkeypatch):
    from services.glassbox.api import ask

    class PreloadedCache:
        async def get(self, corpus, model_id, vector):
            return {
                "answer": "Call 614-555-0100.",
                "chunks": [
                    {
                        "n": 1,
                        "chunk_id": 1,
                        "text": "t",
                        "source_path": "corpus/about-me/bio.md",
                        "title": "Bio",
                        "score": 0.9,
                    }
                ],
            }

        async def put(self, *args):
            raise AssertionError("no write on a hit")

    from fastapi.testclient import TestClient

    from services.glassbox.api.main import app
    from services.tests.test_ask_endpoint import MemoryRedis, events

    llm = TokenLLM(["unused"])
    monkeypatch.setattr(ask, "get_answer_cache", lambda client: PreloadedCache())
    monkeypatch.setenv("REDIS_URL", "redis://unused")
    redis_client = MemoryRedis()
    saved = []
    monkeypatch.setattr(ask.redis, "from_url", lambda url: redis_client)
    monkeypatch.setattr(ask, "get_llm_provider", lambda: llm)
    monkeypatch.setattr(ask, "_save_query", lambda **kwargs: saved.append(kwargs))
    stream = events(
        TestClient(app).post("/api/ask", json={"question": "phone?", "corpus": "about_me"})
    )
    assert "".join(_tokens(stream)) == f"Call {REDACTION}."
    assert llm.calls == 0
    assert saved[0]["timings"]["answer_pii_masked"] == 1


@pytest.mark.parametrize(
    "answer",
    ["Call +1 (614) 555-0100.", "SSN 123-45-6789", "Ring +44 20 7946 0958 now"],
)
def test_cacheability_backstop_refuses_raw_personal_data(answer):
    assert uncacheable_reason(answer, CHUNKS) == "pii"


def test_cacheability_allows_answers_that_only_mention_redaction():
    assert uncacheable_reason(f"His phone number is {REDACTION} in the sources.", CHUNKS) is None
