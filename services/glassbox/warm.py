"""Keep the suggested questions' answers cached (DESIGN.md §7.3).

Asks each suggested question from ``frontend/src/suggested-questions.json``
through the real ``POST /api/ask`` path, exactly as a visitor's first question
would, so the embedding, retrieval and answer caches fill the same way. A
question whose answer is already cached comes back as an answer-cache hit and
costs no LLM call; only misses generate (one answer each).

Cost guards: at most ``--max-llm-calls`` misses per run (default: the number of
suggested questions), and the run stops at the first sign of the shared limits
(HTTP 429, a ``rate_limited``/``budget_exhausted`` error, or a
``retrieval_only`` answer, which is what the API sends when the daily LLM
budget is spent or the LLM is switched off). The warm-up goes through the same
rate limiter and daily budget as visitors; it never bypasses them.

Standard library only, so the container stays small.

    python -m services.glassbox.warm --api-url http://api.app.svc.cluster.local
"""

import argparse
import json
import logging
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

LOGGER = logging.getLogger("glassbox.warm")
DEFAULT_QUESTIONS = Path(__file__).resolve().parents[2] / "frontend/src/suggested-questions.json"
CORPORA = ("about_me", "about_system")
REQUEST_TIMEOUT_S = 90.0


class StopWarmup(Exception):
    """A shared limit was hit; asking more would only spend visitors' budget."""


@dataclass
class Outcome:
    corpus: str
    question: str
    result: str  # "cached", "warmed", "no_sources", "failed"
    total_ms: int | None = None
    detail: str = ""


def load_questions(path: Path) -> list[tuple[str, str]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    pairs: list[tuple[str, str]] = []
    for corpus in CORPORA:
        for question in data.get(corpus, []):
            if not isinstance(question, str) or not question.strip():
                raise ValueError(f"invalid suggested question in {corpus}: {question!r}")
            pairs.append((corpus, question))
    unknown = set(data) - set(CORPORA)
    if unknown:
        raise ValueError(f"unknown corpora in {path}: {sorted(unknown)}")
    return pairs


def parse_sse(lines: Iterable[bytes]) -> Iterator[tuple[str, dict]]:
    """Yield (event, data) pairs from an SSE byte stream; comment pings are skipped."""
    event = "message"
    data: list[str] = []
    for raw in lines:
        line = raw.decode("utf-8").rstrip("\r\n")
        if not line:
            if data:
                yield event, json.loads("\n".join(data))
            event, data = "message", []
        elif line.startswith(":"):
            continue
        elif line.startswith("event:"):
            event = line[len("event:") :].strip()
        elif line.startswith("data:"):
            data.append(line[len("data:") :].strip())
    if data:
        yield event, json.loads("\n".join(data))


def classify(corpus: str, question: str, events: Iterable[tuple[str, dict]]) -> Outcome:
    """Turn one /api/ask stream into an outcome, or raise StopWarmup on a limit."""
    for event, data in events:
        if event == "error":
            code = data.get("code")
            if code in {"rate_limited", "budget_exhausted"}:
                raise StopWarmup(f"{code}: {data.get('message', '')}")
            return Outcome(corpus, question, "failed", detail=f"{code}: {data.get('message', '')}")
        if event == "done":
            if data.get("mode") == "retrieval_only":
                raise StopWarmup("daily LLM budget spent or LLM switched off (retrieval_only)")
            total_ms = data.get("total_ms")
            if data.get("answer_cache") == "hit":
                return Outcome(corpus, question, "cached", total_ms)
            if data.get("tokens_in", 0) == 0 and data.get("tokens_out", 0) == 0:
                # No chunks indexed for this corpus/model yet: nothing was generated.
                return Outcome(corpus, question, "no_sources", total_ms)
            return Outcome(corpus, question, "warmed", total_ms)
    return Outcome(corpus, question, "failed", detail="stream ended without a done event")


def ask(api_url: str, corpus: str, question: str, *, timeout: float = REQUEST_TIMEOUT_S) -> Outcome:
    body = json.dumps({"question": question, "corpus": corpus}).encode()
    request = urllib.request.Request(
        f"{api_url.rstrip('/')}/api/ask",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "User-Agent": "glassbox-warm/1",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            # Read to `done`: the API writes the answer cache just before it,
            # and leaving early would log the request as stopped, uncached.
            return classify(corpus, question, parse_sse(response))
    except urllib.error.HTTPError as exc:
        if exc.code in {429, 503}:
            raise StopWarmup(f"HTTP {exc.code}") from exc
        return Outcome(corpus, question, "failed", detail=f"HTTP {exc.code}")
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        return Outcome(corpus, question, "failed", detail=f"{type(exc).__name__}: {exc}")


def warm(
    api_url: str,
    questions: list[tuple[str, str]],
    *,
    max_llm_calls: int,
    ask_fn=None,
) -> tuple[list[Outcome], str | None]:
    """Ask each question once; returns outcomes and the reason it stopped early, if any."""
    ask_fn = ask_fn or ask
    outcomes: list[Outcome] = []
    llm_calls = 0
    for corpus, question in questions:
        if llm_calls >= max_llm_calls:
            return outcomes, f"reached --max-llm-calls={max_llm_calls}"
        try:
            outcome = ask_fn(api_url, corpus, question)
        except StopWarmup as exc:
            return outcomes, str(exc)
        outcomes.append(outcome)
        if outcome.result == "warmed":
            llm_calls += 1
        LOGGER.info(
            "%-8s %-12s %5sms  %s%s",
            outcome.result,
            corpus,
            outcome.total_ms if outcome.total_ms is not None else "-",
            question,
            f"  ({outcome.detail})" if outcome.detail else "",
        )
    return outcomes, None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--api-url", default="http://api.app.svc.cluster.local")
    parser.add_argument("--questions", type=Path, default=DEFAULT_QUESTIONS)
    parser.add_argument(
        "--max-llm-calls",
        type=int,
        default=None,
        help="stop after this many generated (uncached) answers; default: one per question",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)

    questions = load_questions(args.questions)
    max_llm_calls = len(questions) if args.max_llm_calls is None else args.max_llm_calls
    if max_llm_calls < 0:
        parser.error("--max-llm-calls must be >= 0")
    started = time.monotonic()
    outcomes, stopped = warm(args.api_url, questions, max_llm_calls=max_llm_calls)
    counts = {
        name: sum(o.result == name for o in outcomes)
        for name in ("cached", "warmed", "no_sources", "failed")
    }
    LOGGER.info(
        "warm-up: %d/%d asked, %s, LLM answer calls %d, %.1fs%s",
        len(outcomes),
        len(questions),
        ", ".join(f"{name} {count}" for name, count in counts.items()),
        counts["warmed"],
        time.monotonic() - started,
        f"; stopped early: {stopped}" if stopped else "",
    )
    # A limit stop is expected and not a failure; an unreachable API or a
    # broken stream is, so the Job shows it.
    return 1 if counts["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
