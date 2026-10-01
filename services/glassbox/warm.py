"""Keep the suggested questions' answers cached (DESIGN.md §7.3).

Asks each suggested question from ``frontend/src/suggested-questions.json``
through the real ``POST /api/ask`` path, exactly as a visitor's first question
would, so the embedding, retrieval and answer caches fill the same way. A
question whose answer is already cached comes back as an answer-cache hit and
costs no LLM call; only misses generate (one answer each).

Cost guards, all counting every ask that may have reached generation (an
answer that errored after the LLM started still spent a budget slot):

- at most ``--max-llm-calls`` per run (default: the number of questions);
- at most ``GLASSBOX_WARM_DAILY_LLM_CAP`` (default 10) per UTC day across all
  runs (the CronJob and each deploy's run), via an atomic Redis counter
  ``warm:budget:{date}``, so warm-ups can never take more than that share of
  the visitors' daily answer budget. A slot is reserved before each ask and
  handed back when the ask provably made no LLM call (cache hit, no sources,
  error before generation);
- the run stops at the first sign of the shared limits (HTTP 429/503, a
  ``rate_limited``/``budget_exhausted`` error, or a ``retrieval_only`` answer,
  which the API sends when the daily LLM budget is spent or the LLM is off).

The warm-up goes through the same rate limiter and daily budget as visitors.

    python -m services.glassbox.warm --api-url http://api.app.svc.cluster.local
"""

import argparse
import json
import logging
import os
import socket
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

LOGGER = logging.getLogger("glassbox.warm")
DEFAULT_QUESTIONS = Path(__file__).resolve().parents[2] / "frontend/src/suggested-questions.json"
CORPORA = ("about_me", "about_system")
REQUEST_TIMEOUT_S = 90.0
DEFAULT_DAILY_LLM_CAP = 10
_DAILY_KEY_TTL_S = 48 * 3600


class StopWarmup(Exception):
    """A shared limit was hit; asking more would only spend visitors' budget.

    ``llm_attempted`` is True when the stop came from a response that doesn't
    prove nothing was generated (an HTTP 503 from a proxy), so its slot is kept.
    """

    def __init__(self, message: str, *, llm_attempted: bool = False):
        super().__init__(message)
        self.llm_attempted = llm_attempted


@dataclass
class Outcome:
    corpus: str
    question: str
    result: str  # "cached", "warmed", "no_sources", "failed"
    total_ms: int | None = None
    detail: str = ""
    # True when this ask may have made an LLM call. Conservative: False only
    # when it is certain nothing was generated (a hit, no sources, an error
    # event before the llm stage, a 4xx, or a connection that failed before the
    # request was sent). Anything uncertain counts against both caps.
    llm_attempted: bool = True


class DailyCap:
    """Warm-up LLM calls per UTC day, shared by every run (Redis INCR is atomic)."""

    def __init__(self, client, cap: int, *, now=None):
        self.client = client
        self.cap = cap
        self.now = now or (lambda: datetime.now(UTC))

    def _key(self) -> str:
        return f"warm:budget:{self.now().date().isoformat()}"

    def reserve(self) -> str | None:
        """Take one slot; returns its day key (pass it to ``refund``), or None if full."""
        key = self._key()
        used = self.client.incr(key)
        if used == 1:
            self.client.expire(key, _DAILY_KEY_TTL_S)
        if used > self.cap:
            self.client.decr(key)
            return None
        return key

    def refund(self, key: str) -> None:
        """Give back a slot on the day it was taken, even if the date has changed."""
        self.client.decr(key)


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


def classify(
    corpus: str,
    question: str,
    events: Iterable[tuple[str, dict]],
    progress: dict | None = None,
) -> Outcome:
    """Turn one /api/ask stream into an outcome, or raise StopWarmup on a limit.

    ``progress["llm"]`` is set once the stream shows the LLM stage starting, so a
    caller that loses the stream afterwards still knows generation began.
    """
    progress = {} if progress is None else progress
    progress.setdefault("llm", False)
    for event, data in events:
        if event == "stage" and data.get("node") == "llm":
            progress["llm"] = True
        elif event == "error":
            code = data.get("code")
            if code in {"rate_limited", "budget_exhausted"}:
                raise StopWarmup(f"{code}: {data.get('message', '')}")
            return Outcome(
                corpus,
                question,
                "failed",
                detail=f"{code}: {data.get('message', '')}",
                llm_attempted=progress["llm"],
            )
        elif event == "done":
            if data.get("mode") == "retrieval_only":
                raise StopWarmup("daily LLM budget spent or LLM switched off (retrieval_only)")
            total_ms = data.get("total_ms")
            if data.get("answer_cache") == "hit":
                return Outcome(corpus, question, "cached", total_ms, llm_attempted=False)
            if not progress["llm"]:
                # No chunks indexed for this corpus/model yet: nothing was generated.
                return Outcome(corpus, question, "no_sources", total_ms, llm_attempted=False)
            return Outcome(corpus, question, "warmed", total_ms)
    return Outcome(
        corpus,
        question,
        "failed",
        detail="stream ended without a done event",
        llm_attempted=progress["llm"],
    )


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
    progress: dict = {"llm": False}
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            # Read to `done`: the API writes the answer cache just before it,
            # and leaving early would log the request as stopped, uncached.
            return classify(corpus, question, parse_sse(response), progress)
    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            raise StopWarmup("HTTP 429") from exc
        # A 4xx is the API refusing the request; a 5xx (often a proxy) can't
        # prove the api didn't start generating.
        attempted = exc.code >= 500
        if exc.code == 503:
            raise StopWarmup("HTTP 503", llm_attempted=attempted) from exc
        return Outcome(
            corpus, question, "failed", detail=f"HTTP {exc.code}", llm_attempted=attempted
        )
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        return Outcome(
            corpus,
            question,
            "failed",
            detail=f"{type(exc).__name__}: {exc}",
            llm_attempted=not _never_sent(exc),
        )


def _never_sent(exc: BaseException) -> bool:
    """True only when the request certainly never reached the server.

    A refused connection or a failed DNS lookup happen before anything is sent.
    Everything else (timeouts, resets, a broken stream) may come after the POST
    arrived, so the server may already be generating.
    """
    reason = exc.reason if isinstance(exc, urllib.error.URLError) else exc
    return isinstance(reason, ConnectionRefusedError | socket.gaierror)


def warm(
    api_url: str,
    questions: list[tuple[str, str]],
    *,
    max_llm_calls: int,
    daily_cap: DailyCap | None = None,
    ask_fn=None,
) -> tuple[list[Outcome], str | None]:
    """Ask each question once; returns outcomes and the reason it stopped early, if any."""
    ask_fn = ask_fn or ask
    outcomes: list[Outcome] = []
    llm_calls = 0
    for corpus, question in questions:
        if llm_calls >= max_llm_calls:
            return outcomes, f"reached --max-llm-calls={max_llm_calls}"
        slot = None
        if daily_cap is not None:
            slot = daily_cap.reserve()
            if slot is None:
                return outcomes, f"reached the daily warm-up cap ({daily_cap.cap} LLM calls)"
        try:
            outcome = ask_fn(api_url, corpus, question)
        except StopWarmup as exc:
            if slot is not None and not exc.llm_attempted:
                daily_cap.refund(slot)
            return outcomes, str(exc)
        outcomes.append(outcome)
        if outcome.llm_attempted:
            llm_calls += 1
        elif slot is not None:
            daily_cap.refund(slot)
        LOGGER.info(
            "%-10s %-12s %5sms  %s%s",
            outcome.result,
            corpus,
            outcome.total_ms if outcome.total_ms is not None else "-",
            question,
            f"  ({outcome.detail})" if outcome.detail else "",
        )
    return outcomes, None


def daily_cap_from_env() -> DailyCap:
    """The shared daily cap; needs REDIS_URL (fails closed without it)."""
    raw = os.getenv("GLASSBOX_WARM_DAILY_LLM_CAP", str(DEFAULT_DAILY_LLM_CAP))
    cap = int(raw)
    if cap < 0:
        raise ValueError("GLASSBOX_WARM_DAILY_LLM_CAP must be >= 0")
    import redis  # imported here so tests and --help don't need a server

    return DailyCap(redis.from_url(os.environ["REDIS_URL"]), cap)


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
    # Fails closed: without REDIS_URL the shared daily cap can't be enforced.
    daily_cap = daily_cap_from_env()
    started = time.monotonic()
    outcomes, stopped = warm(
        args.api_url, questions, max_llm_calls=max_llm_calls, daily_cap=daily_cap
    )
    counts = {
        name: sum(o.result == name for o in outcomes)
        for name in ("cached", "warmed", "no_sources", "failed")
    }
    LOGGER.info(
        "warm-up: %d/%d asked, %s, LLM answer calls (incl. failed) %d, %.1fs%s",
        len(outcomes),
        len(questions),
        ", ".join(f"{name} {count}" for name, count in counts.items()),
        sum(o.llm_attempted for o in outcomes),
        time.monotonic() - started,
        f"; stopped early: {stopped}" if stopped else "",
    )
    # A limit stop is expected and not a failure; an unreachable API or a
    # broken stream is, so the Job shows it.
    return 1 if counts["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
