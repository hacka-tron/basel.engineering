"""Measure recall@5 and MRR through the live Redis retrieval path."""

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
from services.glassbox.retrieval.search import search_chunks

HERE = Path(__file__).resolve().parent
QUESTIONS_PATH = HERE / "questions.yaml"
BASELINE_DIR = HERE / "baselines"


def score_case(retrieved: list[str], expected: set[str]) -> tuple[int, float]:
    for rank, source in enumerate(retrieved[:5], start=1):
        if source in expected:
            return 1, 1 / rank
    return 0, 0.0


def summarize(cases: list[dict]) -> dict:
    def metrics(selected: list[dict]) -> dict:
        return {
            "count": len(selected),
            "recall_at_5": round(sum(case["hit"] for case in selected) / len(selected), 4),
            "mrr": round(sum(case["reciprocal_rank"] for case in selected) / len(selected), 4),
        }

    return {
        "overall": metrics(cases),
        "by_corpus": {
            corpus: metrics([case for case in cases if case["corpus"] == corpus])
            for corpus in ("about_me", "about_system")
        },
    }


def load_questions(path: Path = QUESTIONS_PATH) -> list[dict]:
    data = yaml.safe_load(path.read_text())
    questions = data["questions"]
    ids = [item["id"] for item in questions]
    if len(ids) != len(set(ids)) or len(questions) < 25:
        raise ValueError("evaluation needs at least 25 unique questions")
    for item in questions:
        if item["corpus"] not in {"about_me", "about_system"} or not item["expected_sources"]:
            raise ValueError(f"invalid evaluation case: {item['id']}")
    return questions


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
            by_chunk = dict(
                session.execute(
                    select(Chunk.id, Document.source_path).join(
                        Document, Chunk.document_id == Document.id
                    )
                ).all()
            )
        fingerprint = hashlib.sha256(
            "\n".join(
                sorted(f"{doc.corpus}:{doc.source_path}:{doc.content_hash}" for doc in documents)
            ).encode()
        ).hexdigest()
        versions = {
            corpus: await RedisRetrievalCache(client).version(corpus)
            for corpus in ("about_me", "about_system")
        }
        cases = []
        for item in questions:
            missing = [
                path
                for path in item["expected_sources"]
                if (item["corpus"], path) not in source_paths
            ]
            if missing:
                raise ValueError(f"{item['id']} expected sources are not indexed: {missing}")
            vector = (await provider.embed([normalize_question(item["question"])]))[0]
            matches = await search_chunks(client, vector, item["corpus"], top_k=5)
            retrieved = [by_chunk[match["chunk_id"]] for match in matches]
            hit, reciprocal_rank = score_case(retrieved, set(item["expected_sources"]))
            cases.append(
                {
                    "id": item["id"],
                    "corpus": item["corpus"],
                    "expected_sources": item["expected_sources"],
                    "retrieved_sources": retrieved,
                    "hit": hit,
                    "reciprocal_rank": reciprocal_rank,
                }
            )
        return {
            "embedding_model": provider.model_id,
            "index_name": CHUNK_INDEX_NAME,
            "corpus_versions": versions,
            "dataset_fingerprint": fingerprint,
            **summarize(cases),
            "cases": cases,
        }
    finally:
        await client.aclose()
        engine.dispose()


def baseline_path(model_id: str) -> Path:
    safe_id = re.sub(r"[^a-zA-Z0-9._-]", "_", model_id)
    return BASELINE_DIR / f"{safe_id}.json"


def regression_reason(result: dict, baseline: dict) -> str | None:
    if baseline["embedding_model"] != result["embedding_model"]:
        return "Baseline embedding model differs from the selected provider"
    if baseline["index_name"] != result["index_name"]:
        return "Baseline search index differs from the current index"
    if baseline["dataset_fingerprint"] != result["dataset_fingerprint"]:
        return "Corpus content changed; review misses and refresh the baseline intentionally"
    prior = baseline["overall"]["recall_at_5"]
    current = result["overall"]["recall_at_5"]
    if current < prior - 0.05:
        return f"Recall@5 regressed from {prior:.3f} to {current:.3f}"
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write-baseline", action="store_true")
    args = parser.parse_args()
    result = asyncio.run(evaluate(load_questions()))
    path = baseline_path(result["embedding_model"])
    summary = {key: result[key] for key in ("embedding_model", "overall", "by_corpus")}
    print(json.dumps(summary, indent=2))
    print("misses:", ", ".join(case["id"] for case in result["cases"] if not case["hit"]))
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
