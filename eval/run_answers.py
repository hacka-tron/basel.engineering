"""Generate answers for the golden set in-process and grade them deterministically.

Uses the same building blocks as `/api/ask` (follow-up rewrite prompt, question
embedding, Redis KNN search, MySQL chunk load, answer prompt, provider generate)
but calls them directly, so the live rate limit, daily budget and answer cache are
never touched. With the fake provider it is a free pipeline smoke test; with
Bedrock it makes paid calls and needs `--paid`.
"""

import argparse
import asyncio
import json
import os
import statistics
import subprocess
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path

from eval.graders import ANSWERABLE_CATEGORIES, CATEGORIES, grade_case
from eval.judge import Judge, get_judge_llm, source_dicts
from eval.schema import load_golden
from services.glassbox.api.ask import (
    _ANSWER_MAX_TOKENS,
    _FOLLOW_UP_SYSTEM,
    _PROMPT_VERSION,
    _REWRITE_MAX_TOKENS,
    _REWRITE_SYSTEM,
    HistoryMessage,
    WorkerChunk,
    _clean_rewrite,
    _prompt,
    _rewrite_prompt,
    _token_counts,
    bounded_history,
)
from services.glassbox.cache.embedding import normalize_question
from services.glassbox.providers.base import (
    ABSTENTION_ANSWER,
    ContentFilteredError,
    EmbeddingProvider,
    LLMProvider,
)

HERE = Path(__file__).resolve().parent
RUNS_DIR = HERE / "runs"
# Matches the retrieval worker (services/glassbox/worker/main.py, top_k=8).
RETRIEVAL_TOP_K = 8

Retriever = Callable[[list[float], str, str], Awaitable[list[WorkerChunk]]]


class PaidRunRefused(SystemExit):
    pass


def check_paid_allowed(paid: bool) -> None:
    """Refuse any non-fake provider unless --paid AND GLASSBOX_EVAL_ALLOW_PAID=1.

    Two keys on purpose, matching run_eval.py's env-var guard: a stray flag or a
    leftover environment variable alone cannot start a paid run.
    """
    mode = os.getenv("GLASSBOX_PROVIDER", "fake").lower()
    if mode == "fake":
        return
    if not paid or os.getenv("GLASSBOX_EVAL_ALLOW_PAID") != "1":
        raise PaidRunRefused(
            f"GLASSBOX_PROVIDER={mode} makes paid model calls; once the owner has "
            "approved the spend, rerun with --paid and GLASSBOX_EVAL_ALLOW_PAID=1"
        )


def select_cases(
    cases: list[dict],
    *,
    ids: list[str] | None = None,
    categories: list[str] | None = None,
    max_cases: int | None = None,
) -> list[dict]:
    if ids:
        unknown = set(ids) - {case["id"] for case in cases}
        if unknown:
            raise SystemExit(f"unknown case ids: {sorted(unknown)}")
        cases = [case for case in cases if case["id"] in ids]
    if categories:
        unknown = set(categories) - CATEGORIES
        if unknown:
            raise SystemExit(f"unknown categories: {sorted(unknown)}")
        cases = [case for case in cases if case["category"] in categories]
    if max_cases is not None:
        cases = cases[:max_cases]
    return cases


def stack_retriever(redis_client, session_factory) -> Retriever:
    """Retrieval over the real Redis index and MySQL chunk table, as the worker does."""
    from services.glassbox.retrieval.search import search_chunks
    from services.glassbox.worker.main import _load_chunks

    async def retrieve(vector: list[float], corpus: str, model_id: str) -> list[WorkerChunk]:
        matches = await search_chunks(redis_client, vector, corpus, model_id, top_k=RETRIEVAL_TOP_K)
        rows = await asyncio.to_thread(_load_chunks, session_factory, matches)
        return [WorkerChunk.model_validate(row) for row in rows]

    return retrieve


async def run_case(
    case: dict,
    *,
    embedder: EmbeddingProvider,
    llm: LLMProvider,
    retrieve: Retriever,
    judge: Judge | None = None,
) -> dict:
    """Answer one golden case the way /api/ask would, then grade it."""
    row: dict = {
        "id": case["id"],
        "category": case["category"],
        "corpus": case["corpus"],
        "holdout": bool(case.get("holdout", False)),
        "known_failure": case.get("known_failure"),
        "question": case["question"],
        "history_turns": len(case.get("history", [])),
        "prompt_version": _PROMPT_VERSION,
        "embedding_model": embedder.model_id,
        "llm_model": llm.model_id,
    }
    started = time.monotonic()
    try:
        history = bounded_history([HistoryMessage(**item) for item in case.get("history", [])])
        rewrite = None
        retrieval_query = case["question"]
        rewrite_error = None
        if history:
            try:
                parts = [
                    part
                    async for part in llm.generate(
                        _rewrite_prompt(case["question"], history),
                        max_tokens=_REWRITE_MAX_TOKENS,
                        system=_REWRITE_SYSTEM,
                    )
                ]
                rewrite = _clean_rewrite("".join(parts))
            except Exception as exc:
                # Like the API: a failed rewrite falls back to the original question.
                rewrite_error = f"{type(exc).__name__}: {exc}"
            if rewrite:
                retrieval_query = rewrite
        row["rewrite_error"] = rewrite_error
        vector = (await embedder.embed([normalize_question(retrieval_query)]))[0]
        chunks = await retrieve(vector, case["corpus"], embedder.model_id)
        usage: dict = {}
        first_token_ms = None
        if not chunks:
            # Same as the API: no sources means the canonical abstention, no LLM call.
            answer, tokens_in, tokens_out = ABSTENTION_ANSWER, 0, 0
        else:
            prompt = _prompt(case["question"], chunks, history)
            system_kwargs = {"system": _FOLLOW_UP_SYSTEM} if history else {}
            usage_kwargs = {"usage": usage} if getattr(llm, "reports_usage", False) else {}
            answer_parts = []
            llm_started = time.monotonic()
            try:
                async for part in llm.generate(
                    prompt, max_tokens=_ANSWER_MAX_TOKENS, **system_kwargs, **usage_kwargs
                ):
                    if first_token_ms is None:
                        first_token_ms = round((time.monotonic() - llm_started) * 1000)
                    answer_parts.append(part)
            except ContentFilteredError:
                # Same as the API: a provider filter stop becomes the abstention.
                row["content_filtered"] = True
                if not "".join(answer_parts).strip():
                    answer_parts = [ABSTENTION_ANSWER]
            answer = "".join(answer_parts)
            tokens_in, tokens_out = _token_counts(prompt, answer, usage)
        row.update(
            {
                "rewrite": rewrite,
                "retrieved": [
                    {
                        "n": c.n,
                        "chunk_id": c.chunk_id,
                        "source_path": c.source_path,
                        "score": round(c.score, 4),
                    }
                    for c in chunks
                ],
                # Raw chunk text, so a stored run can be judged or labelled later.
                "sources": source_dicts(chunks),
                "answer": answer,
                "answer_words": len(answer.split()),
                "tokens_in": tokens_in,
                "tokens_out": tokens_out,
                "tokens_measured": isinstance(usage.get("outputTokens"), int),
                "llm_first_token_ms": first_token_ms,
                "latency_ms": round((time.monotonic() - started) * 1000),
                "grades": grade_case(case, answer, rewrite),
                "error": None,
            }
        )
        if judge is not None and chunks:
            # A multi_turn follow-up alone ("and the second one?") is meaningless to the
            # judge: it gets the standalone rewrite when there is one.
            judge_question = rewrite or case["question"]
            verdicts = await judge.evaluate(judge_question, source_dicts(chunks), answer)
            row["judge"] = {
                "model": judge.model_id,
                "question": judge_question,
                "verdicts": {name: v.to_dict() for name, v in verdicts.items()},
            }
    except Exception as exc:  # one broken case must not stop the run
        row.update(
            {
                "latency_ms": round((time.monotonic() - started) * 1000),
                "grades": None,
                "error": f"{type(exc).__name__}: {exc}",
            }
        )
    row["passed"] = bool(row["grades"] and row["grades"]["passed"])
    return row


def _rate(values: list[bool]) -> float | None:
    return round(sum(values) / len(values), 4) if values else None


def judge_metrics(graded: list[dict], answerable: list[dict]) -> dict:
    """Judge pass rates (only present when the run used --judge).

    Faithfulness covers every judged answer. Relevance covers answerable cases only:
    a correct abstention on an unanswerable case would otherwise count as irrelevant.
    Unusable judge replies are counted in `judge_errors`. Dropping them would inflate
    the rates, so the rates are None (invalid) unless `judge_errors` is 0; the partial
    figures stay available as `*_rate_partial` for debugging, never for a gate.
    """
    judged = [row for row in graded if row.get("judge")]
    if not judged:
        return {}

    def verdicts(rows: list[dict], name: str) -> list[dict]:
        return [row["judge"]["verdicts"][name] for row in rows if row.get("judge")]

    def rate(items: list[dict]) -> float | None:
        return _rate([v["pass"] for v in items if v["pass"] is not None])

    every = [v for name in ("faithfulness", "relevance") for v in verdicts(judged, name)]
    errors = sum(v["pass"] is None for v in every)
    faithfulness = rate(verdicts(judged, "faithfulness"))
    relevance = rate(verdicts(answerable, "relevance"))
    return {
        "judge_count": len(judged),
        "judge_faithfulness_rate": faithfulness if errors == 0 else None,
        "judge_relevance_rate": relevance if errors == 0 else None,
        "judge_faithfulness_rate_partial": faithfulness if errors else None,
        "judge_relevance_rate_partial": relevance if errors else None,
        "judge_errors": errors,
        "judge_tokens_in": sum(v["tokens_in"] for v in every),
        "judge_tokens_out": sum(v["tokens_out"] for v in every),
    }


def p90(values: list[int]) -> float | None:
    """The 90th percentile (inclusive method); None for no values."""
    if not values:
        return None
    if len(values) == 1:
        return float(values[0])
    return round(statistics.quantiles(values, n=10, method="inclusive")[-1], 1)


def summarize(rows: list[dict]) -> dict:
    """Per-category and overall numbers (DESIGN-005 §5.2)."""

    def metrics(selected: list[dict]) -> dict:
        graded = [row for row in selected if row["grades"]]
        answerable = [row for row in graded if row["category"] in ANSWERABLE_CATEGORIES]
        unanswerable = [row for row in graded if row["category"] == "unanswerable"]
        status = [row for row in graded if row["grades"]["status_ok"] is not None]
        rewrites = [row for row in graded if row["grades"]["rewrite_ok"] is not None]
        injection = [row for row in graded if row["category"] == "injection"]
        words = [row["answer_words"] for row in graded]
        return {
            "count": len(selected),
            "passed": sum(row["passed"] for row in selected),
            "pass_rate": _rate([row["passed"] for row in selected]),
            "errors": sum(1 for row in selected if row["error"]),
            "fact_coverage": (
                round(statistics.mean(row["grades"]["fact_coverage"] for row in graded), 4)
                if graded
                else None
            ),
            "abstain_rate_unanswerable": _rate([r["grades"]["abstained"] for r in unanswerable]),
            "false_abstain_rate": _rate([r["grades"]["abstained"] for r in answerable]),
            "status_ok_rate": _rate([r["grades"]["status_ok"] for r in status]),
            "rewrite_ok_rate": _rate([r["grades"]["rewrite_ok"] for r in rewrites]),
            "injection_ok_rate": _rate(
                [
                    not r["grades"]["prompt_leaks"] and not r["grades"]["forbidden_hits"]
                    for r in injection
                ]
            ),
            "mentions_source_path": sum(
                bool(r["grades"].get("source_path_mentions")) for r in graded
            ),
            "mentions_source_ref": sum(
                bool(r["grades"].get("source_ref_mentions")) for r in graded
            ),
            "median_answer_words": statistics.median(words) if words else None,
            "p90_answer_words": p90(words),
            **judge_metrics(graded, answerable),
        }

    # Known failures (a BACKLOG item, e.g. a corpus fix awaiting the owner) are
    # reported on their own and kept out of every rate, so a gate is not blocked
    # by an expected failure and a fix shows up as a change in this list.
    known = [row for row in rows if row.get("known_failure")]
    scored = [row for row in rows if not row.get("known_failure")]
    categories = sorted({row["category"] for row in scored})
    return {
        "overall": metrics(scored),
        "holdout": metrics([row for row in scored if row["holdout"]]),
        "by_category": {
            category: metrics([row for row in scored if row["category"] == category])
            for category in categories
        },
        "failed": sorted(row["id"] for row in scored if not row["passed"]),
        "known_failures": [
            {"id": row["id"], "reference": row["known_failure"], "passed": row["passed"]}
            for row in sorted(known, key=lambda row: row["id"])
        ],
    }


async def run_cases(
    cases: list[dict],
    *,
    embedder: EmbeddingProvider,
    llm: LLMProvider,
    retrieve: Retriever,
    judge: Judge | None = None,
) -> list[dict]:
    return [
        await run_case(case, embedder=embedder, llm=llm, retrieve=retrieve, judge=judge)
        for case in cases
    ]


async def _run_against_stack(cases: list[dict], *, use_judge: bool = False) -> list[dict]:
    import redis.asyncio as redis
    from sqlalchemy.orm import sessionmaker

    from services.glassbox.db.session import create_db_engine
    from services.glassbox.providers.factory import get_embedding_provider, get_llm_provider

    engine = create_db_engine()
    client = redis.from_url(os.environ["REDIS_URL"])
    try:
        return await run_cases(
            cases,
            embedder=get_embedding_provider(),
            llm=get_llm_provider(),
            retrieve=stack_retriever(client, sessionmaker(bind=engine)),
            judge=Judge(get_judge_llm()) if use_judge else None,
        )
    finally:
        await client.aclose()
        engine.dispose()


def _git_sha() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            cwd=HERE,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "nogit"


BASELINES_DIR = HERE / "baselines"
REDACTED = "[redacted: private corpus]"


def _is_committed_path(out: Path) -> bool:
    return BASELINES_DIR in out.resolve().parents


def redact_row(row: dict) -> dict:
    """Copy of a row without free text that can restate the private About Basel corpus.

    Drops question, rewrite, answer, raw `sources` text and judge critiques; keeps ids,
    grades (matched patterns become counts), metrics, word counts and retrieved paths
    and chunk ids. Only about_me rows are touched.
    """
    if row.get("corpus") != "about_me":
        return row
    out = {k: v for k, v in row.items() if k not in ("question", "answer", "sources")}
    if out.get("rewrite") is not None:
        out["rewrite"] = REDACTED
    if out.get("error"):
        out["error"] = str(out["error"]).split(":", 1)[0]
    grades = out.get("grades")
    if isinstance(grades, dict):
        grades = dict(grades)
        for key in (
            "missing_facts",
            "forbidden_hits",
            "prompt_leaks",
            "source_path_mentions",
            "source_ref_mentions",
        ):
            if isinstance(grades.get(key), list):
                grades[f"{key}_count"] = len(grades.pop(key))
        out["grades"] = grades
    elif "missing_facts" in out:  # flattened baseline rows
        out["missing_facts_count"] = len(out.pop("missing_facts"))
    judge = out.get("judge")
    if isinstance(judge, dict):
        out["judge"] = {
            "model": judge.get("model"),
            "verdicts": {
                name: {k: v for k, v in verdict.items() if k != "critique"}
                for name, verdict in judge.get("verdicts", {}).items()
            },
        }
    return out


def write_run(
    rows: list[dict],
    summary: dict,
    out: Path | None = None,
    *,
    redact_about_me: bool | None = None,
) -> Path:
    """Write rows (JSONL) and a summary.

    Files under eval/baselines/ are committed, so about_me free text is always
    redacted there. The default eval/runs/ output (gitignored) keeps full text for
    local review; pass redact_about_me=True (CLI --redact-about-me) to redact elsewhere.
    """
    if out is None:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        out = RUNS_DIR / f"{stamp}-{_PROMPT_VERSION}-{_git_sha()}.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    if redact_about_me or (redact_about_me is None and _is_committed_path(out)):
        rows = [redact_row(row) for row in rows]
    with out.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    out.with_suffix(".summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", help="comma-separated case ids")
    parser.add_argument("--category", help="comma-separated categories")
    parser.add_argument("--max-cases", type=int)
    parser.add_argument("--paid", action="store_true", help="allow a non-fake (paid) provider")
    parser.add_argument(
        "--judge",
        action="store_true",
        help="also run the faithfulness and relevance LLM judges (eval/judge.py; paid unless fake)",
    )
    parser.add_argument(
        "--redact-about-me",
        action="store_true",
        help="strip about_me free text from the output (always on under eval/baselines/)",
    )
    parser.add_argument("--out", type=Path, help="JSONL output path (default eval/runs/)")
    args = parser.parse_args(argv)
    check_paid_allowed(args.paid)
    cases = select_cases(
        load_golden(),
        ids=args.cases.split(",") if args.cases else None,
        categories=args.category.split(",") if args.category else None,
        max_cases=args.max_cases,
    )
    rows = asyncio.run(_run_against_stack(cases, use_judge=args.judge))
    summary = summarize(rows)
    path = write_run(rows, summary, args.out, redact_about_me=args.redact_about_me or None)
    print(json.dumps(summary, indent=2))
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
