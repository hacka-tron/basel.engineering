"""Suggested-question warm-up: skips cached answers, stops on shared limits."""

import io
import json
import re
import urllib.error
from pathlib import Path

import pytest

from services.glassbox import warm
from services.glassbox.api.sse import PING_FRAME, frame

REPO = Path(__file__).resolve().parents[2]


def stream(*frames: str) -> io.BytesIO:
    return io.BytesIO("".join(frames).encode())


def done(**fields) -> str:
    payload = {"total_ms": 12, "mode": "full", "answer_cache": "miss", "tokens_in": 30}
    payload["tokens_out"] = 8
    payload.update(fields)
    return frame("done", payload)


def test_suggested_questions_file_is_the_frontend_source():
    pairs = warm.load_questions(warm.DEFAULT_QUESTIONS)
    assert warm.DEFAULT_QUESTIONS == REPO / "frontend/src/suggested-questions.json"
    assert {corpus for corpus, _ in pairs} == {"about_me", "about_system"}
    assert 1 <= len(pairs) <= 10  # under the per-client rate limit of 10 per 10 minutes
    chat = (REPO / "frontend/src/components/Chat.tsx").read_text()
    assert "suggested-questions.json" in chat
    assert not re.search(r"'What did Basel work on at YouTube\?'", chat)


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
        ((frame("token", {"text": "Hi"}), done()), "warmed"),
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
        return warm.Outcome(corpus, question, result, 5)

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
    path = tmp_path / "q.json"
    path.write_text(json.dumps({"about_me": ["A?", "B?"]}))
    monkeypatch.setattr(
        warm, "ask", lambda api, corpus, q: (_ for _ in ()).throw(warm.StopWarmup("budget"))
    )
    assert warm.main(["--questions", str(path)]) == 0  # a limit stop is not a failure

    monkeypatch.setattr(warm, "ask", lambda api, corpus, q: warm.Outcome(corpus, q, "failed"))
    assert warm.main(["--questions", str(path)]) == 1
