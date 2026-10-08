"""The codename rewrite (services/glassbox/codename.py): "Glassbox" -> "this site"."""

import random

import pytest

from services.glassbox.codename import CodenameFilter, rewrite
from services.tests.test_answer_cacheability import allow_all_limits  # noqa: F401 (fixture)

CASES = [
    # The owner's live example (2026-10-08).
    (
        "The Hybrid Search component in the Glassbox system uses Redis Stack and RediSearch.",
        "The Hybrid Search component in this site uses Redis Stack and RediSearch.",
    ),
    ("The Glassbox system runs on k3s.", "This site runs on k3s."),
    ("Glassbox uses MySQL and Redis.", "This site uses MySQL and Redis."),
    ("Glassbox's retrieval is hybrid.", "This site's retrieval is hybrid."),
    ("I built Glassbox's cache.", "I built this site's cache."),
    ("I use the Glassbox API over SSE", "I use this site's API over SSE"),
    ("I built Glassbox.", "I built this site."),
    ("Costs: Glassbox runs on one node.", "Costs: This site runs on one node."),
    ("A.\nGlassbox caches answers.", "A.\nThis site caches answers."),
    ("It is the Glassbox, not a box.", "It is this site, not a box."),
    ("I built the Glassbox's cache.", "I built this site's cache."),
    ("# Glassbox architecture", "# This site architecture"),
    ("- Glassbox uses Redis", "- This site uses Redis"),
    ("Caches, e.g. Glassbox uses Redis.", "Caches, e.g. this site uses Redis."),
]

UNCHANGED = [
    "The code lives in services/glassbox/api/ask.py.",
    "Set GLASSBOX_PROVIDER=bedrock.",
    "The glassbox-api pod restarts.",
    "the Glassbox-api deployment",
    "a Glassboxes word",
    "MyGlassbox is not it.",
    "No codename here at all.",
    "See Glassbox.com for more.",
    "Run `Glassbox` now.",
    "Tag @Glassbox or #Glassbox.",
]


@pytest.mark.parametrize(("text", "expected"), CASES)
def test_rewrite(text, expected):
    assert rewrite(text) == expected


@pytest.mark.parametrize("text", UNCHANGED)
def test_identifiers_and_other_words_are_left_alone(text):
    assert rewrite(text) == text


def _stream(text: str, size: int) -> tuple[str, int]:
    f = CodenameFilter()
    parts = [text[i : i + size] for i in range(0, len(text), size)]
    return "".join(f.push(part) for part in parts) + f.flush(), f.rewritten


@pytest.mark.parametrize(("text", "expected"), CASES + [(t, t) for t in UNCHANGED])
@pytest.mark.parametrize("size", [1, 2, 3, 5, 7, 1000])
def test_streaming_matches_the_whole_text_rewrite(text, expected, size):
    assert _stream(text, size)[0] == expected


@pytest.mark.parametrize(
    "tokens",
    [
        ["I built the", " Glass", "box", "'s", " cache."],
        ["In the", " Glassbox", "'s", " retrieval"],
    ],
)
def test_the_possessive_after_the_is_one_phrase_when_streamed(tokens):
    f = CodenameFilter()
    streamed = "".join(f.push(token) for token in tokens) + f.flush()
    assert streamed == rewrite("".join(tokens))
    assert "the this" not in streamed


def test_streaming_always_matches_the_whole_text_rewrite_fuzz():
    rng = random.Random(205)
    words = [
        "the",
        "The",
        "Glassbox",
        "Glassbox's",
        "system",
        "API",
        "uses",
        "Redis",
        ".",
        "\n",
        "e.g.",
        "services/glassbox",
        "Glassbox.com",
        "`Glassbox`",
        "#",
        "-",
        "I",
        "built",
    ]
    for _ in range(3000):
        text = " ".join(rng.choice(words) for _ in range(rng.randint(1, 14)))
        cuts = sorted(rng.sample(range(1, len(text)), min(len(text) - 1, rng.randint(0, 8))))
        tokens = [text[a:b] for a, b in zip([0, *cuts], [*cuts, len(text)], strict=True)]
        f = CodenameFilter()
        assert "".join(f.push(token) for token in tokens) + f.flush() == rewrite(text), tokens


def test_counts_rewrites_and_releases_plain_text_without_delay():
    f = CodenameFilter()
    assert f.push("Hello ") == "Hello "
    assert f.push("the Glass") == ""  # may still become "the Glassbox ..."
    assert f.push("box system works") == "this site works"
    assert f.flush() == ""
    assert f.rewritten == 1
    assert _stream("Glassbox and Glassbox's cache", 4) == ("This site and this site's cache", 2)


# --- in the /api/ask stream ------------------------------------------------------


def test_the_api_streams_and_caches_the_rewritten_answer(monkeypatch):
    from services.tests.test_answer_cacheability import _ask_twice
    from services.tests.test_answer_guard import TokenLLM, _tokens

    llm = TokenLLM(["Search in the Glass", "box sys", "tem uses Redis Stack."])
    cache, saved, streams, _ = _ask_twice(monkeypatch, llm)
    expected = "Search in this site uses Redis Stack."
    assert "".join(_tokens(streams[0])) == expected
    assert not any("Glass" in token for token in _tokens(streams[0]))
    assert cache.puts[0]["answer"] == expected
    assert "".join(_tokens(streams[1])) == expected
    assert saved[0]["timings"]["codename_rewritten"] == 1


def test_an_answer_cached_before_the_rewrite_is_rewritten_on_replay(monkeypatch):
    from services.tests import test_answer_cacheability as cacheability
    from services.tests.test_answer_guard import TokenLLM, _tokens

    put = cacheability.RecordingAnswerCache.put

    async def put_old_entry(self, corpus, model_id, vector, payload):
        await put(self, corpus, model_id, vector, {**payload, "answer": "Glassbox caches answers."})

    monkeypatch.setattr(cacheability.RecordingAnswerCache, "put", put_old_entry)
    _, _, streams, dones = cacheability._ask_twice(monkeypatch, TokenLLM(["It caches answers."]))
    assert dones[1]["answer_cache"] == "hit"
    assert "".join(_tokens(streams[1])) == "This site caches answers."
