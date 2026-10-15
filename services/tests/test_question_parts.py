"""Prompt v23: multi-part questions get their parts listed after the question."""

import asyncio

import pytest

from services.glassbox.api.ask import WorkerChunk, _prompt
from services.glassbox.question_parts import (
    MAX_PARTS,
    looks_multi_part,
    parse_parts,
    parts_hint,
    split_prompt,
)
from services.tests.test_ask_endpoint import events
from services.tests.test_ask_history import RecordingLLM, harness  # noqa: F401

# The 2026-10-15 probe questions (two parts each), as visitors typed them.
MULTI_PART = [
    "Where do you want to work and what kind of roles are you after?",
    "are you open to remote work? and when could you start",
    "What did you do at Google, and why are you leaving Microsoft?",
    "Do you need sponsorship? Also what salary are you looking for?",
    "What's your favorite food and what do you do for fun?",
    "Where did you go to school and did you do any internships?",
    "Do you do freelance work? How do I reach you?",
    "How does the answer cache work, and what does the site cost to run each month?",
    "How is rate limiting done and how do deploys reach the server?",
    "Which LLM answers questions and what database stores the chunks?",
]
SINGLE_PART = [
    "What kind of roles are you looking for?",
    "Have you used Kafka and Kubernetes?",
    "What's the difference between the retrieval worker and the API?",
    "Tell me about your work at Google and Microsoft",
    "no i mean what kind of roles",
]


@pytest.mark.parametrize("question", MULTI_PART)
def test_gate_catches_two_part_questions(question):
    assert looks_multi_part(question)


@pytest.mark.parametrize("question", SINGLE_PART)
def test_gate_leaves_single_questions_alone(question):
    assert not looks_multi_part(question)


def test_parse_parts_strips_list_markers_quotes_and_duplicates():
    raw = '1. Where do you want to work?\n\n- "What roles?"\n2) where do you want to work?\n'
    assert parse_parts(raw) == ["Where do you want to work?", "What roles?"]


def test_parse_parts_caps_count_and_length():
    raw = "\n".join(f"Question {i} " + "x" * 300 for i in range(6))
    parts = parse_parts(raw)
    assert len(parts) == MAX_PARTS
    assert all(len(part) <= 200 for part in parts)


@pytest.mark.parametrize("parts", [None, [], ["Only one?"]])
def test_hint_is_empty_without_two_parts(parts):
    assert parts_hint(parts) == ""


def test_hint_lists_parts_in_order_and_keeps_the_rules_in_force():
    hint = parts_hint(["Where do you want to work?", "What roles?"])
    assert "asks 2 things: (1) Where do you want to work? (2) What roles?" in hint
    assert "untrusted" in hint
    assert "that the sources answer" in hint and "following every rule above" in hint


def test_repeated_question_marks_count_once():
    assert not looks_multi_part("You built this?? wow")


def test_split_prompt_quotes_the_message():
    assert "Message: a and b?" in split_prompt("a and b?")


def _chunk():
    return WorkerChunk(
        n=1, chunk_id=1, text="Some fact.", source_path="docs/x.md", title="X", score=1
    )


@pytest.mark.parametrize("route", ["strict", "casual"])
def test_prompt_ends_with_the_hint_on_both_routes(route):
    plain = _prompt("A and what B?", [_chunk()], None, route, "about_me")
    with_parts = _prompt("A and what B?", [_chunk()], None, route, "about_me", ["A?", "B?"])
    assert with_parts == plain + parts_hint(["A?", "B?"])
    assert _prompt("A?", [_chunk()], None, route, "about_me", ["A?"]) == plain.replace(
        "A and what B?", "A?"
    )


# --- the request flow -------------------------------------------------------------


class SplittingLLM(RecordingLLM):
    """RecordingLLM plus a split reply for the 120-token split call."""

    def __init__(self, split="Where do you want to work?\nWhat roles?", fail_split=False):
        super().__init__()
        self.split = split
        self.fail_split = fail_split

    async def generate(self, prompt, *, max_tokens, system=None):
        if max_tokens == 120:
            self.calls.append({"prompt": prompt, "max_tokens": max_tokens, "system": system})
            if self.fail_split:
                raise RuntimeError("split model unavailable")
            yield self.split
            return
        async for part in super().generate(prompt, max_tokens=max_tokens, system=system):
            yield part

    @property
    def split_calls(self):
        return [call for call in self.calls if call["max_tokens"] == 120]

    @property
    def answer_calls(self):
        return [call for call in self.calls if call["max_tokens"] not in (60, 120)]


TWO_PART = "Where do you want to work and what roles are you after?"
HINT_MARK = "(untrusted user input) asks"


def test_multi_part_question_lists_its_parts_in_the_answer_prompt(harness):  # noqa: F811
    harness["llm"] = llm = SplittingLLM()
    stream = events(harness["post"](question=TWO_PART, history=None))
    assert any(name == "done" for name, _ in stream)
    assert len(llm.split_calls) == 1
    assert TWO_PART in llm.split_calls[0]["prompt"]
    assert "untrusted" in llm.split_calls[0]["system"]
    prompt = llm.answer_calls[0]["prompt"]
    assert prompt.endswith(parts_hint(["Where do you want to work?", "What roles?"]))
    # The split is budgeted like a rewrite (1 unit), before the answer's 4.
    assert harness["budget"].reservations == [1, 4]
    assert harness["saved"][0]["timings"]["question_parts"] == 2


def test_single_question_makes_no_split_call(harness):  # noqa: F811
    harness["llm"] = llm = SplittingLLM()
    events(harness["post"](question="What roles are you after?", history=None))
    assert llm.split_calls == []
    assert HINT_MARK not in llm.answer_calls[0]["prompt"]
    assert harness["budget"].reservations == [4]


@pytest.mark.parametrize(
    ("make_llm", "cached"),
    [
        # A failed split may leave a one-part answer: never cached (review r1).
        (lambda: SplittingLLM(fail_split=True), False),
        # The split itself says it is one question (or nothing): a normal answer.
        (lambda: SplittingLLM(split="Where and what?"), True),
        (lambda: SplittingLLM(split=""), True),
    ],
)
def test_failed_or_single_split_answers_without_a_hint(harness, make_llm, cached):  # noqa: F811
    harness["llm"] = llm = make_llm()
    stream = events(harness["post"](question=TWO_PART, history=None))
    assert any(name == "done" for name, _ in stream)
    assert len(llm.answer_calls) == 1
    assert HINT_MARK not in llm.answer_calls[0]["prompt"]
    timings = harness["saved"][0]["timings"]
    assert "question_parts" not in timings
    assert harness["cache"].puts == (1 if cached else 0)
    assert ("question_split_missing" in timings) is not cached


class SlowSplitLLM(SplittingLLM):
    """A split that never finishes in time; records whether it was cancelled."""

    def __init__(self):
        super().__init__()
        self.cancelled = False

    async def generate(self, prompt, *, max_tokens, system=None):
        if max_tokens == 120:
            self.calls.append({"prompt": prompt, "max_tokens": max_tokens, "system": system})
            try:
                await asyncio.sleep(5)
            except asyncio.CancelledError:
                self.cancelled = True
                raise
            yield "never"
            return
        async for part in super().generate(prompt, max_tokens=max_tokens, system=system):
            yield part


def test_slow_split_times_out_answers_without_hint_and_is_cancelled(harness, monkeypatch):  # noqa: F811
    from services.glassbox.api import ask

    monkeypatch.setattr(ask, "_SPLIT_WAIT_S", 0.05)
    harness["llm"] = llm = SlowSplitLLM()
    stream = events(harness["post"](question=TWO_PART, history=None))
    assert any(name == "done" for name, _ in stream)
    assert llm.cancelled
    assert HINT_MARK not in llm.answer_calls[0]["prompt"]
    assert harness["cache"].puts == 0


def test_pending_split_is_cancelled_on_an_early_return(harness):  # noqa: F811
    harness["llm"] = llm = SlowSplitLLM()

    class SplitOnlyBudget(type(harness["budget"])):
        async def reserve(self, *, units=4):
            self.reservations.append(units)
            return units == 1  # the split's unit, but no answer budget left

    harness["budget"] = budget = SplitOnlyBudget()
    from services.glassbox.api import ask

    ask_budget = ask.get_daily_budget
    try:
        ask.get_daily_budget = lambda client: budget
        stream = events(harness["post"](question=TWO_PART, history=None))
    finally:
        ask.get_daily_budget = ask_budget
    done = next(data for name, data in stream if name == "done")
    assert done["mode"] == "retrieval_only"
    assert llm.cancelled
    assert llm.answer_calls == []


def test_follow_up_splits_the_standalone_rewrite(harness):  # noqa: F811
    harness["llm"] = llm = SplittingLLM()
    events(harness["post"](question="What did you build there, and why did you leave?"))
    assert len(llm.split_calls) == 1
    # RecordingLLM's rewrite, not the raw follow-up with its "there".
    assert "What else did Basel work on at YouTube?" in llm.split_calls[0]["prompt"]


def test_split_skipped_when_budget_denies_it(harness):  # noqa: F811
    harness["llm"] = llm = SplittingLLM()
    harness["budget"].allow_rewrite = False
    events(harness["post"](question=TWO_PART, history=None))
    assert llm.split_calls == []
    assert HINT_MARK not in llm.answer_calls[0]["prompt"]
