"""Build the pool of answers the owner labels for judge calibration (DESIGN-005 §5.3).

Natural answers are nearly all passes, so the pool oversamples failures. Item kinds:

- ``thin``: answerable cases whose stored answer misses facts (from an existing run
  JSONL, e.g. the v13 baseline), so no new model call is needed.
- ``abstention_disabled``: unanswerable cases answered with the abstention instruction
  removed, so the model is pushed to make something up. Needs the stack and a paid run.
- ``perturbed``: answers generated from sources with a number changed or a planned
  feature stated as live, but judged against the ORIGINAL sources, so they contain a
  claim the sources do not support. Needs the stack and a paid run.
- ``mismatched``: an answer written for a different question of the same corpus, paired
  with this question. Gives the relevance judge examples that fail, which the other
  kinds rarely produce. No model call.

Outputs: ``eval/runs/calibration-pool.jsonl`` (full items, gitignored: it holds source
text, and generated answers are not reproducible, so keep it), a readable
``eval/runs/calibration-sheet.md`` for labelling, and new blank entries appended to
``eval/calibration.yaml`` (existing entries are never changed).
"""

import argparse
import asyncio
import hashlib
import json
import os
import re
from pathlib import Path

import yaml

from eval.graders import ANSWERABLE_CATEGORIES
from eval.schema import load_golden

HERE = Path(__file__).resolve().parent
CALIBRATION_PATH = HERE / "calibration.yaml"
POOL_PATH = HERE / "runs" / "calibration-pool.jsonl"
SHEET_PATH = HERE / "runs" / "calibration-sheet.md"
KINDS = ("thin", "abstention_disabled", "perturbed", "mismatched")
MIN_PER_KIND = 15
DEFAULT_SEED = "rag-judge-calibration-v1"
DEFAULT_DEV_FRACTION = 0.4
LABELS = ("pass", "fail")


def answer_hash(answer: str) -> str:
    return hashlib.sha256(answer.encode("utf-8")).hexdigest()[:16]


def item_id(kind: str, case_id: str) -> str:
    return f"{kind}:{case_id}"


def assign_split(
    item: str, *, seed: str = DEFAULT_SEED, dev_fraction: float = DEFAULT_DEV_FRACTION
) -> str:
    """Deterministic dev/test split from the item id and a fixed seed.

    Depends on nothing but the id and the seed, so it can never change with the labels
    and dev and test cannot overlap (an item has exactly one split).
    """
    digest = hashlib.sha256(f"{seed}:{item}".encode()).digest()
    bucket = int.from_bytes(digest[:8], "big") / 2**64
    return "dev" if bucket < dev_fraction else "test"


# ---------------------------------------------------------------- perturbation

_NUMBER = re.compile(r"(?<![\w.])\d+(?:\.\d+)?")
_STATUS_SWAPS = (
    (re.compile(r"\bnot (?:yet )?(?:built|implemented)\b", re.I), "implemented"),
    (re.compile(r"\b(?:is|are) planned\b", re.I), "is live"),
    (re.compile(r"\bplanned\b", re.I), "live"),
    (re.compile(r"\bfuture\b", re.I), "current"),
    (re.compile(r"\bon the roadmap\b", re.I), "in production"),
)


def perturb_numbers(text: str) -> str:
    """Change every number so the text contradicts the original (n -> n*2+3)."""

    def bump(match: re.Match) -> str:
        value = match.group(0)
        if "." in value:
            return f"{float(value) * 2 + 3:.1f}"
        return str(int(value) * 2 + 3)

    return _NUMBER.sub(bump, text)


def perturb_status(text: str) -> str:
    """State planned work as built and live."""
    for pattern, replacement in _STATUS_SWAPS:
        text = pattern.sub(replacement, text)
    return text


def perturb_sources(sources: list[dict]) -> tuple[list[dict], str | None]:
    """Perturb the sources: status first (the more telling failure), else numbers.

    Returns the new sources and which perturbation changed anything (None: nothing to
    perturb, so the case is not usable).
    """
    for name, fn in (("status", perturb_status), ("number", perturb_numbers)):
        changed = [{**source, "text": fn(source["text"])} for source in sources]
        if any(a["text"] != b["text"] for a, b in zip(changed, sources, strict=True)):
            return changed, name
    return sources, None


# ---------------------------------------------------------------- pool items


def make_item(kind: str, row: dict, *, answer: str, sources: list[dict], **extra) -> dict:
    return {
        "id": item_id(kind, row["id"]),
        "case_id": row["id"],
        "kind": kind,
        "category": row["category"],
        # Standalone question: for multi_turn cases the rewrite, never the bare follow-up.
        "question": row.get("judge_question") or row.get("rewrite") or row["question"],
        "sources": sources,
        "answer": answer,
        "answer_hash": answer_hash(answer),
        **extra,
    }


def usable(row: dict) -> bool:
    return bool(row.get("grades") and not row.get("error") and row.get("sources"))


def thin_items(rows: list[dict]) -> list[dict]:
    return [
        make_item("thin", row, answer=row["answer"], sources=row["sources"])
        for row in rows
        if usable(row)
        and row["category"] in ANSWERABLE_CATEGORIES
        and not row.get("known_failure")
        and row["grades"]["fact_coverage"] < 1.0
    ]


def mismatched_items(rows: list[dict]) -> list[dict]:
    """Pair each answerable row with the next row of the same corpus (a fixed rotation)."""
    pool = sorted((row for row in rows if usable(row)), key=lambda row: row["id"])
    items = []
    for row in pool:
        # Donor answers come from answerable categories only: an abstention paired with an
        # answerable question is a trivially obvious relevance fail.
        others = [
            r
            for r in pool
            if r["corpus"] == row["corpus"]
            and r["id"] != row["id"]
            and r["category"] in ANSWERABLE_CATEGORIES
        ]
        if row["category"] not in ANSWERABLE_CATEGORIES or not others:
            continue
        # Deterministic pick by hash, not "next in list", so the pairs are not adjacent.
        other = others[int(hashlib.sha256(row["id"].encode()).hexdigest(), 16) % len(others)]
        items.append(
            make_item(
                "mismatched",
                row,
                answer=other["answer"],
                sources=row["sources"],
                answer_from=other["id"],
            )
        )
    return items


_ABSTAIN_SENTENCE = re.compile(
    r"Only if they do not answer it at all, reply with exactly \".*?\" and nothing else\. "
)


def without_abstention(prompt: str) -> str:
    """The answer prompt with its abstention instruction replaced by 'guess anyway'."""
    new, count = _ABSTAIN_SENTENCE.subn(
        "If the sources do not answer it, give your best answer anyway. ", prompt
    )
    if count != 1:
        raise RuntimeError("abstention sentence not found in the answer prompt; update this tool")
    return new


async def _generate_items(
    cases: list[dict], *, embedder, llm, retrieve, kind: str, per_kind: int
) -> list[dict]:
    """Generate ``abstention_disabled`` or ``perturbed`` items through the real pipeline."""
    from eval.judge import source_dicts
    from services.glassbox.api.ask import _ANSWER_MAX_TOKENS, _prompt
    from services.glassbox.cache.embedding import normalize_question
    from services.glassbox.providers.base import GROUNDING_RULES

    system_no_abstain = GROUNDING_RULES.replace(
        "If the sources do not answer the question at all, reply with exactly ",
        "If the sources do not answer the question, give your best answer instead of replying ",
    )
    items: list[dict] = []
    for case in cases:
        if len(items) >= per_kind:
            break
        if case.get("history"):
            continue  # a bare follow-up is not a standalone question for the judge
        vector = (await embedder.embed([normalize_question(case["question"])]))[0]
        chunks = await retrieve(vector, case["corpus"], embedder.model_id)
        if not chunks:
            continue
        shown = source_dicts(chunks)
        row = {"id": case["id"], "category": case["category"], "question": case["question"]}
        extra: dict = {}
        if kind == "abstention_disabled":
            prompt, system = (
                without_abstention(_prompt(case["question"], chunks)),
                system_no_abstain,
            )
        else:
            changed, how = perturb_sources(shown)
            if how is None:
                continue
            chunks = [
                chunk.model_copy(update={"text": c["text"]})
                for chunk, c in zip(chunks, changed, strict=True)
            ]
            prompt, system = _prompt(case["question"], chunks), None
            extra["perturbation"] = how
        kwargs = {"system": system} if system else {}
        answer = "".join(
            [part async for part in llm.generate(prompt, max_tokens=_ANSWER_MAX_TOKENS, **kwargs)]
        )
        # The judge and the owner always see the ORIGINAL sources.
        items.append(make_item(kind, row, answer=answer, sources=shown, **extra))
    return items


# ---------------------------------------------------------------- files


def read_yaml(path: Path = CALIBRATION_PATH) -> dict:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    data["items"] = data.get("items") or []
    return data


def pool_counts(items: list[dict]) -> dict[str, int]:
    return {kind: sum(1 for item in items if item["kind"] == kind) for kind in KINDS}


def merge_pool(existing: list[dict], new: list[dict]) -> list[dict]:
    """Keep every existing item (labels refer to those exact answers); add unseen ids."""
    have = {item["id"] for item in existing}
    return existing + [item for item in new if item["id"] not in have]


def read_pool(path: Path = POOL_PATH) -> list[dict]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def write_pool(items: list[dict], path: Path = POOL_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(i, ensure_ascii=False) + "\n" for i in items), "utf-8")


def yaml_block(item: dict, *, seed: str, dev_fraction: float) -> str:
    return (
        f"  - id: {item['id']}\n"
        f"    case_id: {item['case_id']}\n"
        f"    kind: {item['kind']}\n"
        f"    answer_hash: {item['answer_hash']}  # pragma: allowlist secret\n"
        "    faithful: null   # pass | fail\n"
        "    relevant: null   # pass | fail\n"
        '    reason: ""\n'
        f"    split: {assign_split(item['id'], seed=seed, dev_fraction=dev_fraction)}\n"
    )


def append_yaml_items(items: list[dict], path: Path = CALIBRATION_PATH) -> int:
    """Append blank entries for pool items not yet in the file. Existing text is untouched."""
    data = read_yaml(path)
    have = {entry["id"] for entry in data["items"]}
    fresh = [item for item in items if item["id"] not in have]
    if not fresh:
        return 0
    seed = data.get("seed", DEFAULT_SEED)
    fraction = data.get("dev_fraction", DEFAULT_DEV_FRACTION)
    text = path.read_text(encoding="utf-8")
    if re.search(r"^items:\s*\[\]\s*$", text, re.M):
        text = re.sub(r"^items:\s*\[\]\s*$", "items:", text, flags=re.M)
    if not text.endswith("\n"):
        text += "\n"
    text += "".join(yaml_block(item, seed=seed, dev_fraction=fraction) for item in fresh)
    path.write_text(text, encoding="utf-8")
    return len(fresh)


def render_sheet(
    items: list[dict], *, seed: str = DEFAULT_SEED, dev_fraction: float = DEFAULT_DEV_FRACTION
) -> str:
    out = [
        "# Calibration labelling sheet\n",
        "> **WARNING: whoever iterates on the judge prompt (a person or an agent) must read "
        "only the items marked DEV. Items marked TEST are the held-out score; reading their "
        "texts while editing the prompt burns the test labels.**\n",
        "Label each item in `eval/calibration.yaml` (same id): `faithful` and `relevant` "
        "pass/fail, plus a short `reason`. See eval/README.md for the rules.\n",
    ]
    for item in items:
        split = assign_split(item["id"], seed=seed, dev_fraction=dev_fraction).upper()
        note = "" if split == "DEV" else " (held out: do not use for prompt iteration)"
        out.append(f"\n---\n\n## {item['id']} [{split}]{note}\n")
        out.append(f"**Question:** {item['question']}\n")
        out.append("**Sources the answer was supposed to rely on:**\n")
        for source in item["sources"]:
            body = source["text"].strip().replace("\n", "\n> ")
            out.append(f"> [{source['n']}] `{source['source_path']}`\n> {body}\n")
        out.append(f"**Answer:**\n\n{item['answer']}\n")
    return "\n".join(out)


def write_sheet(items: list[dict], path: Path = SHEET_PATH) -> None:
    data = read_yaml() if CALIBRATION_PATH.is_file() else {}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        render_sheet(
            items,
            seed=data.get("seed", DEFAULT_SEED),
            dev_fraction=data.get("dev_fraction", DEFAULT_DEV_FRACTION),
        ),
        encoding="utf-8",
    )


def load_run(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


async def _generate_against_stack(cases: list[dict], per_kind: int) -> list[dict]:
    import redis.asyncio as redis
    from sqlalchemy.orm import sessionmaker

    from eval.run_answers import stack_retriever
    from services.glassbox.db.session import create_db_engine
    from services.glassbox.providers.factory import get_embedding_provider, get_llm_provider

    engine = create_db_engine()
    client = redis.from_url(os.environ["REDIS_URL"])
    try:
        common = {
            "embedder": get_embedding_provider(),
            "llm": get_llm_provider(),
            "retrieve": stack_retriever(client, sessionmaker(bind=engine)),
            "per_kind": per_kind,
        }
        unanswerable = [c for c in cases if c["category"] == "unanswerable"]
        answerable = [c for c in cases if c["category"] in ANSWERABLE_CATEGORIES]
        return await _generate_items(
            unanswerable, kind="abstention_disabled", **common
        ) + await _generate_items(answerable, kind="perturbed", **common)
    finally:
        await client.aclose()
        engine.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--from-run", type=Path, required=True, help="run_answers JSONL (thin, mismatched)"
    )
    parser.add_argument(
        "--generate",
        action="store_true",
        help="also generate abstention_disabled and perturbed answers (paid; needs the stack)",
    )
    parser.add_argument("--paid", action="store_true", help="allow a non-fake (paid) provider")
    parser.add_argument("--per-kind", type=int, default=MIN_PER_KIND + 3)
    args = parser.parse_args(argv)

    new = thin_items(load_run(args.from_run)) + mismatched_items(load_run(args.from_run))
    if args.generate:
        from eval.run_answers import check_paid_allowed

        check_paid_allowed(args.paid)
        new += asyncio.run(_generate_against_stack(load_golden(), args.per_kind))
    capped: list[dict] = []
    for kind in KINDS:
        capped += [item for item in new if item["kind"] == kind][: args.per_kind]
    pool = merge_pool(read_pool(), capped)
    write_pool(pool)
    write_sheet(pool)
    added = append_yaml_items(pool)
    counts = pool_counts(pool)
    print(f"pool: {counts}; {added} new blank entries in {CALIBRATION_PATH.name}")
    short = [kind for kind, n in counts.items() if n < MIN_PER_KIND]
    if short:
        print(f"warning: fewer than {MIN_PER_KIND} items for {short}; see eval/README.md")
    print(f"label them using {SHEET_PATH} and {CALIBRATION_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
