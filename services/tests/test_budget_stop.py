"""The AWS budget stop (infra/modules/compute/budget.tf) as the app sees it.

At $25 of monthly spend AWS Budgets attaches a deny policy for the answer
models to the node's role, and every Bedrock answer call fails with a botocore
ClientError ``AccessDeniedException``. These tests drive the shipped Bedrock
provider with a stubbed boto client raising exactly that, through the real
``POST /api/ask`` stream: the visitor must get the retrieval-only answer
(sources, the frontend's budget line), never an error event or a 500, and
nothing may be cached as an answer.
"""

import io

import pytest
from botocore.exceptions import ClientError
from fastapi.testclient import TestClient

from services.glassbox import warm
from services.glassbox.api.main import app
from services.glassbox.providers.base import LLMAccessDeniedError
from services.glassbox.providers.bedrock import BedrockLLMProvider
from services.tests.test_ask_endpoint import MemoryRedis, events

HISTORY = [
    {"role": "user", "content": "What did Basel do at YouTube?"},
    {"role": "assistant", "content": "He worked on the ingestion pipeline."},
]


def _client_error(code: str, operation: str = "ConverseStream") -> ClientError:
    return ClientError(
        {"Error": {"Code": code, "Message": "not authorized to perform bedrock:..."}},
        operation,
    )


class DeniedBedrockClient:
    """A bedrock-runtime client under the budget deny policy."""

    def __init__(self, code: str = "AccessDeniedException"):
        self.code = code
        self.calls = 0

    def converse_stream(self, **kwargs):
        self.calls += 1
        raise _client_error(self.code)


class RecordingAnswerCache:
    def __init__(self):
        self.puts = []

    async def get(self, *args):
        return None

    async def put(self, *args):
        self.puts.append(args)


class AllowAll:
    def __init__(self):
        self.reservations = []

    async def allow(self, client_hash):
        return True, 0

    async def reserve(self, *, units=4):
        self.reservations.append(units)
        return True


@pytest.fixture
def denied(monkeypatch):
    from services.glassbox.api import ask

    client = DeniedBedrockClient()
    state = {
        "bedrock": client,
        "llm": BedrockLLMProvider(client=client, model_id="us.amazon.nova-lite-v1:0"),
        "redis": MemoryRedis(),
        "cache": RecordingAnswerCache(),
        "limits": AllowAll(),
        "saved": [],
    }
    monkeypatch.setenv("REDIS_URL", "redis://unused")
    monkeypatch.setattr(ask.redis, "from_url", lambda url: state["redis"])
    monkeypatch.setattr(ask, "get_llm_provider", lambda: state["llm"])
    monkeypatch.setattr(ask, "get_answer_cache", lambda client: state["cache"])
    monkeypatch.setattr(ask, "get_rate_limiter", lambda client: state["limits"])
    monkeypatch.setattr(ask, "get_daily_budget", lambda client: state["limits"])
    monkeypatch.setattr(ask, "_save_query", lambda **kwargs: state["saved"].append(kwargs))
    return state


# --- provider ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_bedrock_access_denied_becomes_typed_error():
    provider = BedrockLLMProvider(client=DeniedBedrockClient())
    with pytest.raises(LLMAccessDeniedError) as caught:
        async for _ in provider.generate("q", max_tokens=50):
            pass
    assert isinstance(caught.value.__cause__, ClientError)


@pytest.mark.asyncio
async def test_other_bedrock_client_errors_are_not_mistaken_for_the_budget_stop():
    provider = BedrockLLMProvider(client=DeniedBedrockClient("ThrottlingException"))
    with pytest.raises(ClientError) as caught:
        async for _ in provider.generate("q", max_tokens=50):
            pass
    assert not isinstance(caught.value, LLMAccessDeniedError)


# --- ask path ----------------------------------------------------------------


def test_denied_answer_degrades_to_retrieval_only_with_sources(denied):
    response = TestClient(app).post(
        "/api/ask", json={"question": "Who is Basel?", "corpus": "about_me"}
    )
    assert response.status_code == 200
    stream = events(response)
    names = [name for name, _ in stream]
    assert "error" not in names
    assert "token" not in names
    retrieval = next(data for name, data in stream if name == "retrieval")
    assert retrieval["chunks"][0]["snippet"] == "Basel builds software."
    done = next(data for name, data in stream if name == "done")
    assert done["mode"] == "retrieval_only"
    assert done["abstained"] is False
    assert done["answer_cache"] == "miss"
    # The llm stage that started is closed, so the trace diagram doesn't hang.
    llm_stages = [
        data["status"] for name, data in stream if name == "stage" and data["node"] == "llm"
    ]
    assert llm_stages == ["start", "end"]
    assert denied["bedrock"].calls == 1
    assert denied["cache"].puts == []
    assert denied["saved"][0]["mode"] == "retrieval_only"
    assert denied["saved"][0]["timings"]["llm_access_denied"] == 1


def test_denied_follow_up_skips_the_rewrite_and_still_shows_sources(denied):
    response = TestClient(app).post(
        "/api/ask",
        json={"question": "tell me more", "corpus": "about_me", "history": HISTORY},
    )
    assert response.status_code == 200
    stream = events(response)
    assert "error" not in [name for name, _ in stream]
    done = next(data for name, data in stream if name == "done")
    assert done["mode"] == "retrieval_only"
    # Rewrite and answer were both attempted and both refused; retrieval used the
    # original question.
    assert denied["bedrock"].calls == 2
    assert denied["saved"][0]["rewritten_query"] is None
    assert denied["cache"].puts == []


def test_non_permission_failures_still_report_an_error(denied):
    denied["bedrock"].code = "ThrottlingException"
    stream = events(
        TestClient(app).post("/api/ask", json={"question": "Who is Basel?", "corpus": "about_me"})
    )
    assert stream[-1] == (
        "error",
        {"code": "internal", "message": "The request could not be completed"},
    )
    assert denied["cache"].puts == []


def test_warm_up_stops_on_a_denied_answer_and_hands_its_slot_back(denied):
    # The warm-up reads the same stream: a denied answer is retrieval_only, a
    # limit stop that proves nothing was generated (exit 0, slot refunded).
    response = TestClient(app).post(
        "/api/ask", json={"question": "Who is Basel?", "corpus": "about_me"}
    )
    with pytest.raises(warm.StopWarmup) as caught:
        warm.classify("about_me", "Who is Basel?", warm.parse_sse(io.BytesIO(response.content)))
    assert caught.value.llm_attempted is False
