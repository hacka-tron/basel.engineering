"""Suggested-question warm-up: skips cached answers, stops on shared limits."""

import io
import json
import re
import socket
import urllib.error
from pathlib import Path

import pytest

from services.glassbox import warm
from services.glassbox.api.sse import PING_FRAME, frame

REPO = Path(__file__).resolve().parents[2]


def stream(*frames: str) -> io.BytesIO:
    return io.BytesIO("".join(frames).encode())


LLM_START = frame("stage", {"node": "llm", "status": "start"})


def done(**fields) -> str:
    payload = {"total_ms": 12, "mode": "full", "answer_cache": "miss", "tokens_in": 30}
    payload["tokens_out"] = 8
    payload.update(fields)
    return frame("done", payload)


def test_suggested_questions_file_is_the_frontend_source():
    from services.glassbox.corpora import CORPORA

    pairs = warm.load_questions(warm.DEFAULT_QUESTIONS)
    assert warm.DEFAULT_QUESTIONS == REPO / "frontend/src/suggested-questions.json"
    assert warm.CORPORA is CORPORA
    assert {corpus for corpus, _ in pairs} == set(CORPORA)
    assert 1 <= len(pairs) <= 10  # well under the per-client rate limit of 20 per 10 minutes
    assert [q for corpus, q in pairs if corpus == "portfolio"] == [
        "What can you build for me?",
        "Which project is most like a SaaS app?",
        "Are you available for freelance work?",
    ]
    chat = (REPO / "frontend/src/components/Chat.tsx").read_text()
    assert "suggested-questions.json" in chat
    assert not re.search(r"'What did you work on at YouTube\?'", chat)


def test_warm_up_fits_in_one_rate_limit_bucket():
    # One warm-up run asks every suggested question (all corpora) back to back from one
    # pod, so one client-IP rate-limit bucket. A full bucket holds RATE_CAPACITY asks;
    # with more questions the run relies on refill during the run, or stops early on
    # rate_limited (exit 0) and quietly leaves the last answers uncached.
    from services.glassbox.limits import RATE_CAPACITY

    assert len(warm.load_questions(warm.DEFAULT_QUESTIONS)) <= RATE_CAPACITY


def test_load_questions_rejects_unknown_corpus(tmp_path):
    path = tmp_path / "q.json"
    path.write_text(json.dumps({"about_me": ["Hi?"], "about_you": ["No?"]}))
    with pytest.raises(ValueError, match="unknown corpora"):
        warm.load_questions(path)


def test_parse_sse_skips_heartbeats():
    events = list(
        warm.parse_sse(stream(PING_FRAME, frame("stage", {"node": "api"}), PING_FRAME, done()))
    )
    assert [name for name, _ in events] == ["stage", "done"]


@pytest.mark.parametrize(
    ("frames", "result"),
    [
        (
            (frame("stage", {"node": "answer_cache", "cache": "hit"}), done(answer_cache="hit")),
            "cached",
        ),
        ((LLM_START, frame("token", {"text": "Hi"}), done()), "warmed"),
        ((done(tokens_in=0, tokens_out=0),), "no_sources"),
        ((frame("error", {"code": "internal", "message": "boom"}),), "failed"),
        ((frame("stage", {"node": "api"}),), "failed"),
    ],
)
def test_classify(frames, result):
    outcome = warm.classify("about_me", "Q?", warm.parse_sse(stream(*frames)))
    assert outcome.result == result


@pytest.mark.parametrize(
    "frames",
    [
        (frame("error", {"code": "rate_limited", "message": "slow down", "retry_after_s": 9}),),
        (frame("error", {"code": "budget_exhausted", "message": "spent"}),),
        (
            frame("retrieval", {"chunks": []}),
            done(mode="retrieval_only", tokens_in=0, tokens_out=0),
        ),
    ],
)
def test_classify_stops_on_limits(frames):
    with pytest.raises(warm.StopWarmup):
        warm.classify("about_me", "Q?", warm.parse_sse(stream(*frames)))


QUESTIONS = [("about_me", "A?"), ("about_me", "B?"), ("about_system", "C?"), ("about_system", "D?")]


def scripted(results):
    calls = []

    def ask_fn(api_url, corpus, question):
        calls.append(question)
        result = results[question]
        if isinstance(result, Exception):
            raise result
        return warm.Outcome(corpus, question, result, 5, llm_attempted=result == "warmed")

    return ask_fn, calls


def test_warm_asks_everything_and_cached_answers_cost_nothing():
    ask_fn, calls = scripted({"A?": "cached", "B?": "warmed", "C?": "cached", "D?": "warmed"})
    outcomes, stopped = warm.warm("http://api", QUESTIONS, max_llm_calls=4, ask_fn=ask_fn)
    assert stopped is None
    assert calls == ["A?", "B?", "C?", "D?"]
    assert [o.result for o in outcomes] == ["cached", "warmed", "cached", "warmed"]


def test_warm_stops_at_the_first_limit():
    ask_fn, calls = scripted(
        {"A?": "warmed", "B?": warm.StopWarmup("rate_limited"), "C?": "warmed", "D?": "warmed"}
    )
    outcomes, stopped = warm.warm("http://api", QUESTIONS, max_llm_calls=4, ask_fn=ask_fn)
    assert stopped == "rate_limited"
    assert calls == ["A?", "B?"]
    assert len(outcomes) == 1


def test_warm_caps_generated_answers():
    ask_fn, calls = scripted({"A?": "warmed", "B?": "cached", "C?": "warmed", "D?": "warmed"})
    _, stopped = warm.warm("http://api", QUESTIONS, max_llm_calls=2, ask_fn=ask_fn)
    assert calls == ["A?", "B?", "C?"]
    assert "max-llm-calls=2" in stopped

    ask_fn, calls = scripted({q: "warmed" for _, q in QUESTIONS})
    _, stopped = warm.warm("http://api", QUESTIONS, max_llm_calls=0, ask_fn=ask_fn)
    assert calls == []


def test_ask_stops_on_http_429(monkeypatch):
    def refuse(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 429, "Too Many", {}, None)

    monkeypatch.setattr(warm.urllib.request, "urlopen", refuse)
    with pytest.raises(warm.StopWarmup, match="429"):
        warm.ask("http://api", "about_me", "Q?")


def test_ask_posts_a_first_question_exactly_like_a_visitor(monkeypatch):
    seen = {}

    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def fake_urlopen(request, timeout):
        seen["url"] = request.full_url
        seen["body"] = json.loads(request.data)
        return Response(done(answer_cache="hit").encode())

    monkeypatch.setattr(warm.urllib.request, "urlopen", fake_urlopen)
    outcome = warm.ask("http://api.app.svc.cluster.local/", "about_system", "Why k3s?")
    assert outcome.result == "cached"
    assert seen == {
        "url": "http://api.app.svc.cluster.local/api/ask",
        "body": {"question": "Why k3s?", "corpus": "about_system"},
    }


def test_main_exit_codes(monkeypatch, tmp_path):
    monkeypatch.setattr(warm, "daily_cap_from_env", lambda: FakeCap(cap=10))
    path = tmp_path / "q.json"
    path.write_text(json.dumps({"about_me": ["A?", "B?"]}))
    monkeypatch.setattr(
        warm, "ask", lambda api, corpus, q: (_ for _ in ()).throw(warm.StopWarmup("budget"))
    )
    assert warm.main(["--questions", str(path)]) == 0  # a limit stop is not a failure

    monkeypatch.setattr(warm, "ask", lambda api, corpus, q: warm.Outcome(corpus, q, "failed"))
    assert warm.main(["--questions", str(path)]) == 1


# --- every attempt that may have generated counts (review finding 1) ---


@pytest.mark.parametrize(
    ("frames", "attempted"),
    [
        ((done(answer_cache="hit"),), False),
        ((done(tokens_in=0, tokens_out=0),), False),
        ((frame("error", {"code": "internal", "message": "before llm"}),), False),
        ((LLM_START, frame("error", {"code": "internal", "message": "mid answer"})), True),
        ((LLM_START, frame("token", {"text": "cut"})), True),
        ((LLM_START, done()), True),
    ],
)
def test_llm_attempted_tracks_the_llm_stage(frames, attempted):
    outcome = warm.classify("about_me", "Q?", warm.parse_sse(stream(*frames)))
    assert outcome.llm_attempted is attempted


def test_ask_counts_a_stream_that_broke_after_opening(monkeypatch):
    class Broken:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def __iter__(self):
            raise TimeoutError("read timed out")

    monkeypatch.setattr(warm.urllib.request, "urlopen", lambda request, timeout: Broken())
    outcome = warm.ask("http://api", "about_me", "Q?")
    assert outcome.result == "failed" and outcome.llm_attempted

    def refuse(request, timeout):
        raise urllib.error.URLError(ConnectionRefusedError(61, "refused"))

    monkeypatch.setattr(warm.urllib.request, "urlopen", refuse)
    assert not warm.ask("http://api", "about_me", "Q?").llm_attempted


def test_failed_generations_count_toward_the_per_run_cap():
    calls = []

    def ask_fn(api_url, corpus, question):
        calls.append(question)
        return warm.Outcome(corpus, question, "failed", detail="internal", llm_attempted=True)

    outcomes, stopped = warm.warm("http://api", QUESTIONS, max_llm_calls=2, ask_fn=ask_fn)
    assert calls == ["A?", "B?"]
    assert "max-llm-calls=2" in stopped


# --- shared daily warm-up cap (review finding 2) ---


class FakeRedis:
    def __init__(self):
        self.values = {}
        self.ttls = {}

    def incr(self, key):
        self.values[key] = self.values.get(key, 0) + 1
        return self.values[key]

    def decr(self, key):
        self.values[key] = self.values.get(key, 0) - 1
        return self.values[key]

    def expire(self, key, seconds):
        self.ttls[key] = seconds


class FakeCap(warm.DailyCap):
    def __init__(self, cap, client=None):
        super().__init__(client or FakeRedis(), cap)


def test_daily_cap_reserves_atomically_and_expires():
    from datetime import UTC, datetime

    client = FakeRedis()
    cap = warm.DailyCap(client, 2, now=lambda: datetime(2026, 9, 30, 23, tzinfo=UTC))
    assert cap.reserve() and cap.reserve()
    assert not cap.reserve()  # over the cap: the increment is undone
    assert client.values == {"warm:budget:2026-09-30": 2}
    assert client.ttls == {"warm:budget:2026-09-30": 48 * 3600}
    cap.refund("warm:budget:2026-09-30")
    assert cap.reserve() == "warm:budget:2026-09-30"


def test_daily_cap_is_shared_across_runs_and_hits_are_refunded():
    client = FakeRedis()
    results = {"A?": "cached", "B?": "warmed", "C?": "warmed", "D?": "warmed"}

    def ask_fn(api_url, corpus, question):
        result = results[question]
        return warm.Outcome(corpus, question, result, 5, llm_attempted=result == "warmed")

    # First run (e.g. the CronJob): a hit costs nothing, then two generations fill the cap.
    outcomes, stopped = warm.warm(
        "http://api", QUESTIONS, max_llm_calls=7, daily_cap=FakeCap(2, client), ask_fn=ask_fn
    )
    assert [o.result for o in outcomes] == ["cached", "warmed", "warmed"]
    assert "daily warm-up cap" in stopped

    # A second run the same day (e.g. after a deploy) can't generate anything.
    calls = []

    def counting_ask(api_url, corpus, question):
        calls.append(question)
        return ask_fn(api_url, corpus, question)

    _, stopped = warm.warm(
        "http://api", QUESTIONS, max_llm_calls=7, daily_cap=FakeCap(2, client), ask_fn=counting_ask
    )
    assert calls == []
    assert "daily warm-up cap" in stopped
    assert sum(client.values.values()) == 2


def test_an_api_without_portfolio_fails_only_those_questions_and_spends_nothing():
    # Review Focus 5: during a rollout the CronJob's new image may meet the old api,
    # which answers corpus "portfolio" with HTTP 422.
    client = FakeRedis()

    def ask_fn(api_url, corpus, question):
        if corpus == "portfolio":
            return warm.Outcome(corpus, question, "failed", detail="HTTP 422", llm_attempted=False)
        return warm.Outcome(corpus, question, "cached", llm_attempted=False)

    questions = warm.load_questions(warm.DEFAULT_QUESTIONS)
    outcomes, stopped = warm.warm(
        "http://api",
        questions,
        max_llm_calls=len(questions),
        daily_cap=FakeCap(10, client),
        ask_fn=ask_fn,
    )
    assert stopped is None
    assert len(outcomes) == len(questions)
    assert [o.result for o in outcomes if o.corpus == "portfolio"] == ["failed"] * 3
    assert sum(client.values.values()) == 0  # every reserved slot was handed back


def test_limit_stop_refunds_the_reserved_slot():
    client = FakeRedis()

    def ask_fn(api_url, corpus, question):
        raise warm.StopWarmup("rate_limited")

    warm.warm("http://api", QUESTIONS, max_llm_calls=7, daily_cap=FakeCap(5, client), ask_fn=ask_fn)
    assert sum(client.values.values()) == 0


def test_daily_cap_needs_redis(monkeypatch):
    monkeypatch.delenv("REDIS_URL", raising=False)
    with pytest.raises(KeyError):
        warm.daily_cap_from_env()
    monkeypatch.setenv("REDIS_URL", "redis://localhost:1/0")
    monkeypatch.setenv("GLASSBOX_WARM_DAILY_LLM_CAP", "3")
    assert warm.daily_cap_from_env().cap == 3


# --- round 2: refunds stay on their day; uncertain asks are never refunded ---


def test_refund_after_midnight_goes_to_the_reservation_day():
    from datetime import UTC, datetime

    clock = {"now": datetime(2026, 9, 30, 23, 59, 59, tzinfo=UTC)}
    client = FakeRedis()
    cap = warm.DailyCap(client, 10, now=lambda: clock["now"])
    for _ in range(10):
        assert cap.reserve()

    def ask_fn(api_url, corpus, question):
        clock["now"] = datetime(2026, 10, 1, 0, 0, 1, tzinfo=UTC)  # the ask spans midnight
        return warm.Outcome(corpus, question, "cached", 5, llm_attempted=False)

    client.values["warm:budget:2026-09-30"] = 9  # leave one slot for the run below
    warm.warm("http://api", QUESTIONS[:1], max_llm_calls=7, daily_cap=cap, ask_fn=ask_fn)
    assert client.values["warm:budget:2026-09-30"] == 9  # the slot came back to Sept 30
    assert client.values.get("warm:budget:2026-10-01", 0) == 0  # never negative
    for _ in range(10):
        assert cap.reserve()
    assert cap.reserve() is None  # the new day still stops at 10


def _raise(exc):
    def urlopen(request, timeout):
        raise exc

    return urlopen


@pytest.mark.parametrize(
    ("exc", "attempted"),
    [
        (urllib.error.URLError(ConnectionRefusedError(61, "refused")), False),
        (urllib.error.URLError(socket.gaierror(8, "no such host")), False),
        (TimeoutError("timed out before headers"), True),  # the POST may have arrived
        (urllib.error.URLError(TimeoutError("timed out")), True),
        (ConnectionResetError(54, "reset after send"), True),
        (urllib.error.HTTPError("http://api", 422, "Unprocessable", {}, None), False),
        (urllib.error.HTTPError("http://api", 502, "Bad Gateway", {}, None), True),
        (urllib.error.HTTPError("http://api", 504, "Gateway Timeout", {}, None), True),
    ],
)
def test_ask_is_conservative_about_uncertain_failures(monkeypatch, exc, attempted):
    monkeypatch.setattr(warm.urllib.request, "urlopen", _raise(exc))
    outcome = warm.ask("http://api", "about_me", "Q?")
    assert outcome.result == "failed"
    assert outcome.llm_attempted is attempted


def test_uncertain_failures_keep_their_daily_slot_and_count_per_run():
    client = FakeRedis()

    def ask_fn(api_url, corpus, question):
        return warm.Outcome(corpus, question, "failed", detail="TimeoutError", llm_attempted=True)

    _, stopped = warm.warm(
        "http://api", QUESTIONS, max_llm_calls=2, daily_cap=FakeCap(10, client), ask_fn=ask_fn
    )
    assert "max-llm-calls=2" in stopped
    assert sum(client.values.values()) == 2  # neither slot was refunded


def test_http_503_stops_and_keeps_its_slot(monkeypatch):
    monkeypatch.setattr(
        warm.urllib.request,
        "urlopen",
        _raise(urllib.error.HTTPError("http://api", 503, "Unavailable", {}, None)),
    )
    client = FakeRedis()
    _, stopped = warm.warm("http://api", QUESTIONS, max_llm_calls=7, daily_cap=FakeCap(10, client))
    assert stopped == "HTTP 503"
    assert sum(client.values.values()) == 1
