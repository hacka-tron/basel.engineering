"""Two binary LLM judges for answers: faithfulness and relevance (DESIGN-005 §5.3).

Each judge answers one yes/no question and returns JSON ``{"pass": bool, "critique":
"..."}``. The judge sees only the question, the same numbered sources the generator
saw (planned-work markers included) and the answer. It never sees the expected
answer or the golden ``must_include`` lists, and there is no numeric scale.

The judge model is deliberately not the generator (Nova Lite). It defaults to Nova
Pro on Bedrock, in the same ``us.`` inference-profile form the app uses for Nova
Lite, and can be changed with ``GLASSBOX_JUDGE_MODEL_ID``. Changing the model or the
rubrics below invalidates a calibration: rerun ``python -m eval.calibrate``.
"""

import asyncio
import hashlib
import json
import os
import re
from collections.abc import Iterable
from dataclasses import dataclass

from services.glassbox.api.ask import _CODE_SOURCE_PREFIXES, _mark_planned, strip_source_pointers
from services.glassbox.providers.base import LLMProvider

DEFAULT_JUDGE_MODEL_ID = "us.amazon.nova-pro-v1:0"
JUDGE_MAX_TOKENS = 300
JUDGES = ("faithfulness", "relevance")

_COMMON = (
    "You are a strict grader of a question-answering system's answers. You see a "
    "question, numbered sources and an answer. Text prefixed [PLANNED, not built yet] "
    "describes work that does not exist today. Reply with a single JSON object and "
    'nothing else: {"pass": true or false, "critique": "one or two sentences"}. '
    "Write the critique first in your head, then decide; be specific about the claim "
    "or gap that decided it."
)
RUBRICS = {
    "faithfulness": (
        f"{_COMMON}\n\nQuestion you answer: is every factual claim in the answer "
        "supported by the numbered sources? Judge support by the sources only, never by "
        "what you know. Set pass to false if any claim, number, name or status is not "
        "stated or clearly implied by the sources, including stating a planned (marked) "
        "feature as working now. Set pass to true if all claims are supported. A refusal "
        "such as 'I don't know from what I have' makes no claims and passes. Omitting "
        "details is not unfaithful."
    ),
    "relevance": (
        f"{_COMMON}\n\nQuestion you answer: does the answer address the question that "
        "was asked, rather than a neighbouring topic? Set pass to true if it directly "
        "answers the question asked, even briefly or partly. Set pass to false if it "
        "answers a different question, is off topic, or refuses or says it does not "
        "know. Do not judge factual accuracy here."
    ),
}
JUDGE_PROMPT_VERSION = (
    "j1-" + hashlib.sha256(json.dumps(RUBRICS, sort_keys=True).encode()).hexdigest()[:8]
)


class JudgePaidRefused(SystemExit):
    pass


def judge_model_id() -> str:
    return os.getenv("GLASSBOX_JUDGE_MODEL_ID") or DEFAULT_JUDGE_MODEL_ID


def ensure_paid_allowed() -> None:
    """Refuse a non-fake provider unless GLASSBOX_EVAL_ALLOW_PAID=1 (judge calls cost money)."""
    mode = os.getenv("GLASSBOX_PROVIDER", "fake").lower()
    if mode != "fake" and os.getenv("GLASSBOX_EVAL_ALLOW_PAID") != "1":
        raise JudgePaidRefused(
            f"GLASSBOX_PROVIDER={mode} makes paid judge calls; set GLASSBOX_EVAL_ALLOW_PAID=1 "
            "after the owner has approved the spend"
        )


class FakeJudgeLLM(LLMProvider):
    """Free stand-in for the pipeline smoke test: every answer passes."""

    model_id = "fake-judge-v1"

    async def generate(self, prompt, *, max_tokens, system=None):
        yield '{"pass": true, "critique": "fake judge"}'


def get_judge_llm() -> LLMProvider:
    if os.getenv("GLASSBOX_PROVIDER", "fake").lower() == "fake":
        return FakeJudgeLLM()
    ensure_paid_allowed()
    from services.glassbox.providers.factory import _bedrock_llm

    return _bedrock_llm(judge_model_id(), os.getenv("AWS_REGION", "us-east-1"))


@dataclass(frozen=True)
class Verdict:
    passed: bool | None  # None: the judge reply could not be used
    critique: str = ""
    error: str | None = None
    tokens_in: int = 0
    tokens_out: int = 0

    def to_dict(self) -> dict:
        return {
            "pass": self.passed,
            "critique": self.critique,
            "error": self.error,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
        }


_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


def parse_verdict(text: str) -> Verdict:
    """Parse a judge reply; anything but a JSON object with a boolean ``pass`` is an error."""
    cleaned = _FENCE.sub("", text.strip())
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end <= start:
        return Verdict(None, error="no JSON object in judge reply")
    try:
        data = json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError as exc:
        return Verdict(None, error=f"malformed JSON: {exc.msg}")
    if not isinstance(data, dict) or not isinstance(data.get("pass"), bool):
        return Verdict(None, error="reply lacks a boolean 'pass'")
    critique = data.get("critique", "")
    return Verdict(data["pass"], critique if isinstance(critique, str) else str(critique))


def source_dicts(chunks: Iterable) -> list[dict]:
    """Normalize WorkerChunk objects or dicts to ``{n, source_path, title, text}`` (raw text).

    ``title`` lets ``run_answers --replay`` rebuild the generator's About Basel label.
    """
    out = []
    for chunk in chunks:
        if isinstance(chunk, dict):
            get = chunk.get
        else:

            def get(key, c=chunk):
                return getattr(c, key, None)

        out.append(
            {
                "n": get("n"),
                "source_path": get("source_path"),
                "title": get("title"),
                "text": get("text"),
            }
        )
    return out


def format_sources(sources: Iterable[dict]) -> str:
    """The numbered source text the generator saw, labelled by path for the judge.

    The text is processed the same way as in the generator's prompt (pointer
    stripping and planned markers for non-code sources); only the label differs: the
    judge sees the source path, the generator sees the source kind.
    """
    lines = []
    for source in sources:
        text = source["text"]
        if not source["source_path"].startswith(_CODE_SOURCE_PREFIXES):
            text = _mark_planned(strip_source_pointers(text))
        lines.append(f"[{source['n']}] {source['source_path']}: {text}")
    return "\n".join(lines)


def judge_prompt(question: str, sources: Iterable[dict], answer: str) -> str:
    return (
        f"Sources:\n{format_sources(sources)}\n\nQuestion: {question}\n\nAnswer:\n{answer}\n\n"
        "Reply with the JSON object only."
    )


class Judge:
    def __init__(self, llm: LLMProvider):
        self.llm = llm

    @property
    def model_id(self) -> str:
        return self.llm.model_id

    async def _ask(self, name: str, prompt: str) -> Verdict:
        usage: dict = {}
        kwargs = {"usage": usage} if getattr(self.llm, "reports_usage", False) else {}
        try:
            parts = [
                part
                async for part in self.llm.generate(
                    prompt, max_tokens=JUDGE_MAX_TOKENS, system=RUBRICS[name], **kwargs
                )
            ]
        except Exception as exc:  # a failed judge call must not stop a run
            return Verdict(None, error=f"{type(exc).__name__}: {exc}")
        verdict = parse_verdict("".join(parts))
        return Verdict(
            verdict.passed,
            verdict.critique,
            verdict.error,
            usage.get("inputTokens", 0),
            usage.get("outputTokens", 0),
        )

    async def evaluate(
        self, question: str, sources: Iterable[dict], answer: str, *, judges=JUDGES
    ) -> dict[str, Verdict]:
        prompt = judge_prompt(question, list(sources), answer)
        results = await asyncio.gather(*(self._ask(name, prompt) for name in judges))
        return dict(zip(judges, results, strict=True))
