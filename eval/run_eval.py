"""Retrieval eval v2 through the live Redis retrieval path.

Searches at the production k (8, as in ``worker/main.py``) and reports:

- file-level recall@5 and MRR@5 (unchanged, for continuity with older baselines),
- file-level recall@8 and MRR@8,
- chunk-level recall@8 and MRR@8 (a retrieved chunk's text contains a ``gold_snippet``),
- ``noise@8``: the share of retrieved chunks from tests or implementation plans,

overall, per corpus and per category. See ``eval/README.md``.
"""

import argparse
import asyncio
import hashlib
import json
import os
import re
from pathlib import Path

import redis.asyncio as redis
import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from services.glassbox.cache.embedding import normalize_question
from services.glassbox.cache.retrieval import RedisRetrievalCache
from services.glassbox.db.models import Chunk, Document
from services.glassbox.db.session import create_db_engine
from services.glassbox.ingest.redis_index import INDEX_NAME as CHUNK_INDEX_NAME
from services.glassbox.providers.factory import get_embedding_provider
from services.glassbox.retrieval.search import hybrid_search

HERE = Path(__file__).resolve().parent
GOLDEN_PATH = HERE / "golden.yaml"
QUESTIONS_PATH = HERE / "questions.yaml"
BASELINE_DIR = HERE / "baselines"

# Metrics are scored within the top 8: About This System's production size
# (``retrieval.search.RETRIEVAL_CONFIGS``); About Basel returns 6, so @8 is @6 there.
PRODUCTION_TOP_K = 8
# The fused result (what the worker serves) is the headline; each leg is also
# scored on its own top 8 (``legs`` in the result) to show what fusion adds.
LEGS = ("vector", "lexical")
CONTINUITY_K = 5
# Sources that should never ground an answer: test code and implementation plans.
NOISE_PREFIXES = ("services/tests/", "docs/superpowers/plans/")
CORPORA = ("about_me", "about_system")
TOLERANCE = 0.05
# DESIGN-005 §5.4: chunk-level recall@8 must not drop at all.
CHUNK_TOLERANCE = 0.0
MIN_CASES = 25


def score_case(
    retrieved: list[str], expected: set[str], k: int = CONTINUITY_K
) -> tuple[int, float]:
    """File-level hit and reciprocal rank within the top ``k`` sources."""
    for rank, source in enumerate(retrieved[:k], start=1):
        if source in expected:
            return 1, 1 / rank
    return 0, 0.0


def _squash(text: str) -> str:
    return " ".join(text.split()).casefold()


def contains_snippet(text: str, snippets: list[str]) -> bool:
    """True when ``text`` contains any snippet, ignoring case and whitespace runs."""
    haystack = _squash(text)
    return any(_squash(snippet) in haystack for snippet in snippets)


def is_gold_chunk(source: str, text: str, expected: set[str], snippets: list[str]) -> bool:
    """A gold chunk comes from an expected source and contains a gold snippet.

    The source check matters: tests quote doc prose verbatim, so a test chunk holding
    the snippet must not count as a hit (it is noise, not the answer's source).
    """
    return source in expected and contains_snippet(text, snippets)


def score_chunks(
    chunks: list[tuple[str, str]],
    snippets: list[str],
    expected: set[str],
    k: int = PRODUCTION_TOP_K,
) -> tuple[int, float]:
    """Chunk-level hit and reciprocal rank over ``(source, text)`` pairs, best first."""
    for rank, (source, text) in enumerate(chunks[:k], start=1):
        if is_gold_chunk(source, text, expected, snippets):
            return 1, 1 / rank
    return 0, 0.0


def is_noise(source_path: str) -> bool:
    return source_path.startswith(NOISE_PREFIXES)


def score_retrieval(item: dict, retrieved: list[tuple[int, str, str]]) -> dict:
    """Score one case from its retrieved ``(chunk_id, source_path, text)`` triples, best first."""
    top = retrieved[:PRODUCTION_TOP_K]
    sources = [source for _, source, _ in top]
    expected = set(item["expected_sources"])
    hit, reciprocal_rank = score_case(sources, expected, CONTINUITY_K)
    hit_at_8, reciprocal_rank_at_8 = score_case(sources, expected, PRODUCTION_TOP_K)
    snippets = item.get("gold_snippets") or []
    chunk_hit, chunk_rr = (
        score_chunks([(source, text) for _, source, text in top], snippets, expected)
        if snippets
        else (None, None)
    )
    noise_count = sum(is_noise(source) for source in sources)
    return {
        "id": item["id"],
        "corpus": item["corpus"],
        "category": item.get("category", "fact"),
        "expected_sources": item["expected_sources"],
        # Top 5 only, as in v1 baselines; the full top 8 is in retrieved_at_8.
        "retrieved_sources": sources[:CONTINUITY_K],
        "hit": hit,
        "reciprocal_rank": reciprocal_rank,
        "retrieved_at_8": [{"chunk_id": chunk_id, "source": source} for chunk_id, source, _ in top],
        "hit_at_8": hit_at_8,
        "reciprocal_rank_at_8": reciprocal_rank_at_8,
        "chunk_hit": chunk_hit,
        "chunk_reciprocal_rank": chunk_rr,
        "noise_count": noise_count,
        "retrieved_count": len(sources),
    }


def _mean(values: list[float]) -> float:
    return round(sum(values) / len(values), 4)


def metrics(selected: list[dict]) -> dict:
    result = {
        "count": len(selected),
        "recall_at_5": _mean([case["hit"] for case in selected]),
        "mrr": _mean([case["reciprocal_rank"] for case in selected]),
    }
    if all("hit_at_8" in case for case in selected):
        chunked = [case for case in selected if case.get("chunk_hit") is not None]
        retrieved = sum(case["retrieved_count"] for case in selected)
        result.update(
            {
                "recall_at_8": _mean([case["hit_at_8"] for case in selected]),
                "mrr_at_8": _mean([case["reciprocal_rank_at_8"] for case in selected]),
                "chunk_count": len(chunked),
                "chunk_recall_at_8": (
                    _mean([case["chunk_hit"] for case in chunked]) if chunked else None
                ),
                "chunk_mrr_at_8": (
                    _mean([case["chunk_reciprocal_rank"] for case in chunked]) if chunked else None
                ),
                # Micro average: noisy chunks over all retrieved chunks.
                "noise_at_8": (
                    round(sum(case["noise_count"] for case in selected) / retrieved, 4)
                    if retrieved
                    else 0.0
                ),
            }
        )
    return result


def summarize(cases: list[dict]) -> dict:
    def grouped(key: str, order: tuple[str, ...] = ()) -> dict:
        names = list(order) + sorted({case[key] for case in cases} - set(order))
        groups = {name: [case for case in cases if case[key] == name] for name in names}
        return {name: metrics(group) for name, group in groups.items() if group}

    summary = {"overall": metrics(cases), "by_corpus": grouped("corpus", CORPORA)}
    if all("category" in case for case in cases):
        summary["by_category"] = grouped("category")
    return summary


def default_questions_path() -> Path:
    """golden.yaml (phase 1 of the RAG quality plan) once it exists, else questions.yaml."""
    return GOLDEN_PATH if GOLDEN_PATH.exists() else QUESTIONS_PATH


def load_questions(path: Path | None = None) -> list[dict]:
    """Load the retrieval cases: those with expected sources and no conversation history.

    Accepts a top-level ``questions`` (v1) or ``cases`` (golden v2) list. Cases without
    ``expected_sources`` (unanswerable, injection) and multi-turn cases (retrieval uses a
    paid LLM rewrite there) are answer-eval cases and are skipped here.
    """
    path = path or default_questions_path()
    data = yaml.safe_load(path.read_text())
    items = data.get("cases", data.get("questions"))
    if not isinstance(items, list):
        raise ValueError(f"{path.name} needs a top-level 'cases' or 'questions' list")
    ids = [item["id"] for item in items]
    if len(ids) != len(set(ids)):
        raise ValueError("evaluation case ids must be unique")
    questions = [item for item in items if item.get("expected_sources") and not item.get("history")]
    if len(questions) < MIN_CASES:
        raise ValueError(f"evaluation needs at least {MIN_CASES} retrieval cases")
    for item in questions:
        snippets = item.get("gold_snippets", [])
        if item["corpus"] not in CORPORA or not isinstance(snippets, list):
            raise ValueError(f"invalid evaluation case: {item['id']}")
        if any(not isinstance(snippet, str) or not snippet.strip() for snippet in snippets):
            raise ValueError(f"{item['id']} has an empty gold snippet")
    return questions


def question_set_fingerprint(questions: list[dict]) -> str:
    """Hash of everything that affects scoring, so baselines from other sets are not compared."""
    material = sorted(
        (
            item["id"],
            item["corpus"],
            item["question"],
            sorted(item["expected_sources"]),
            sorted(item.get("gold_snippets") or []),
        )
        for item in questions
    )
    return hashlib.sha256(json.dumps(material).encode()).hexdigest()


async def evaluate(questions: list[dict]) -> dict:
    provider = get_embedding_provider()
    if os.getenv("GLASSBOX_PROVIDER", "fake") == "bedrock" and not os.getenv(
        "GLASSBOX_EVAL_ALLOW_PAID"
    ):
        raise ValueError("Bedrock eval needs explicit GLASSBOX_EVAL_ALLOW_PAID=1")
    engine = create_db_engine()
    client = redis.from_url(os.environ["REDIS_URL"])
    try:
        with Session(engine) as session:
            documents = list(session.scalars(select(Document)))
            source_paths = {(doc.corpus, doc.source_path) for doc in documents}
            rows = session.execute(
                select(Document.corpus, Chunk.embedding_model).join(
                    Chunk, Chunk.document_id == Document.id
                )
            ).all()
            models = {model for _, model in rows}
            if models != {provider.model_id}:
                raise ValueError(
                    f"index models {sorted(models)} do not match selected "
                    f"{provider.model_id}; re-ingest first"
                )
            chunk_rows = session.execute(
                select(Chunk.id, Document.corpus, Document.source_path, Chunk.text).join(
                    Document, Chunk.document_id == Document.id
                )
            ).all()
        by_chunk = {chunk_id: (path, text) for chunk_id, _, path, text in chunk_rows}
        fingerprint = hashlib.sha256(
            "\n".join(
                sorted(f"{doc.corpus}:{doc.source_path}:{doc.content_hash}" for doc in documents)
            ).encode()
        ).hexdigest()
        versions = {corpus: await RedisRetrievalCache(client).version(corpus) for corpus in CORPORA}
        cases = []
        unreachable = []
        for item in questions:
            missing = [
                path
                for path in item["expected_sources"]
                if (item["corpus"], path) not in source_paths
            ]
            if missing:
                raise ValueError(f"{item['id']} expected sources are not indexed: {missing}")
            snippets = item.get("gold_snippets") or []
            expected = set(item["expected_sources"])
            if snippets and not any(
                corpus == item["corpus"] and is_gold_chunk(path, text, expected, snippets)
                for _, corpus, path, text in chunk_rows
            ):
                # The snippet straddles a chunk boundary or the text moved: no chunk can hit.
                unreachable.append(item["id"])
            vector = (await provider.embed([normalize_question(item["question"])]))[0]
            legs: dict = {}
            matches = await hybrid_search(
                client, vector, item["question"], item["corpus"], provider.model_id, legs=legs
            )
            case = score_retrieval(item, _triples(matches, by_chunk))
            case["legs"] = {
                leg: score_retrieval(item, _triples(legs[leg][:PRODUCTION_TOP_K], by_chunk))
                for leg in LEGS
            }
            case["dual_experience_slots"] = len(legs["slots"])
            cases.append(case)
        return {
            "eval_version": 2,
            "top_k": PRODUCTION_TOP_K,
            "embedding_model": provider.model_id,
            "index_name": CHUNK_INDEX_NAME,
            "corpus_versions": versions,
            "dataset_fingerprint": fingerprint,
            "question_set_fingerprint": question_set_fingerprint(questions),
            "noise_prefixes": list(NOISE_PREFIXES),
            "unreachable_gold_snippets": unreachable,
            **summarize(cases),
            "legs": {leg: summarize([case["legs"][leg] for case in cases]) for leg in LEGS},
            "cases": cases,
        }
    finally:
        await client.aclose()
        engine.dispose()


def _triples(matches: list[dict], by_chunk: dict) -> list[tuple[int, str, str]]:
    return [(match["chunk_id"], *by_chunk[match["chunk_id"]]) for match in matches]


def baseline_path(model_id: str) -> Path:
    safe_id = re.sub(r"[^a-zA-Z0-9._-]", "_", model_id)
    return BASELINE_DIR / f"{safe_id}.json"


def _case_ids(run: dict) -> list[str]:
    return sorted(case["id"] for case in run.get("cases", []))


def regression_reason(result: dict, baseline: dict) -> str | None:
    """Why ``result`` fails against ``baseline``, or None.

    v1 baselines (no v2 fields) are gated on recall@5 and their case ids; the v2 gates
    apply once the baseline carries the field.
    """
    if baseline["embedding_model"] != result["embedding_model"]:
        return "Baseline embedding model differs from the selected provider"
    if baseline["index_name"] != result["index_name"]:
        return "Baseline search index differs from the current index"
    if baseline["dataset_fingerprint"] != result["dataset_fingerprint"]:
        return "Corpus content changed; review misses and refresh the baseline intentionally"
    prior_set = baseline.get("question_set_fingerprint")
    if prior_set is not None:
        changed = prior_set != result.get("question_set_fingerprint")
    else:
        # v1 baselines have no fingerprint: compare the case ids instead.
        changed = _case_ids(baseline) != _case_ids(result)
    if changed:
        return "Question set changed; review misses and refresh the baseline intentionally"
    prior, current = baseline["overall"], result["overall"]
    if current["recall_at_5"] < prior["recall_at_5"] - TOLERANCE:
        return f"Recall@5 regressed from {prior['recall_at_5']:.3f} to {current['recall_at_5']:.3f}"
    prior_chunk = prior.get("chunk_recall_at_8")
    if prior_chunk is not None:
        current_chunk = current.get("chunk_recall_at_8")
        if current_chunk is None or current_chunk < prior_chunk - CHUNK_TOLERANCE:
            shown = "n/a" if current_chunk is None else f"{current_chunk:.3f}"
            return f"Chunk-level recall@8 regressed from {prior_chunk:.3f} to {shown}"
    prior_noise = prior.get("noise_at_8")
    if prior_noise is not None:
        current_noise = current.get("noise_at_8", 1.0)
        if current_noise > prior_noise + TOLERANCE:
            return f"Noise@8 rose from {prior_noise:.3f} to {current_noise:.3f}"
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write-baseline", action="store_true")
    parser.add_argument(
        "--questions",
        type=Path,
        default=None,
        help="dataset file (default: eval/golden.yaml if present, else eval/questions.yaml)",
    )
    args = parser.parse_args()
    result = asyncio.run(evaluate(load_questions(args.questions)))
    path = baseline_path(result["embedding_model"])
    summary = {
        key: result[key]
        for key in ("embedding_model", "top_k", "overall", "by_corpus", "by_category")
    }
    print(json.dumps(summary, indent=2))
    for leg, legs_summary in result["legs"].items():
        print(f"{leg} leg alone:", json.dumps(legs_summary["by_corpus"]))
    cases = result["cases"]
    print("misses@5:", ", ".join(case["id"] for case in cases if not case["hit"]))
    print("misses@8:", ", ".join(case["id"] for case in cases if not case["hit_at_8"]))
    print("chunk misses@8:", ", ".join(case["id"] for case in cases if case["chunk_hit"] == 0))
    print("noisy cases:", ", ".join(case["id"] for case in cases if case["noise_count"]))
    if result["unreachable_gold_snippets"]:
        print("unreachable gold snippets:", ", ".join(result["unreachable_gold_snippets"]))
    if args.write_baseline:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(result, indent=2) + "\n")
        print(f"wrote {path}")
        return 0
    if not path.exists():
        raise SystemExit(
            f"No baseline for {result['embedding_model']}; review and run --write-baseline"
        )
    baseline = json.loads(path.read_text())
    if reason := regression_reason(result, baseline):
        raise SystemExit(reason)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
