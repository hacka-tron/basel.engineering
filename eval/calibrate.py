"""Measure how well each judge agrees with the owner's labels (DESIGN-005 §5.3).

Agreement is the true-positive rate (owner pass, judge pass) and true-negative rate
(owner fail, judge fail), reported per judge on the **test split only**. Both must be
at least 0.85, with at least 10 test cases in each class, before any gate uses a
judge. The tool refuses to report rates with fewer than 10 test cases in a class.

Prompt iteration uses the **dev split only**: ``--split dev`` prints each dev
disagreement (question, answer, judge critique, owner reason) as few-shot material.
Test-split texts are never printed, and a repeated test run with a different judge
prompt or model is flagged: choosing between prompt versions on the test split more
than once means the test labels are burned and fresh ones must be drawn.

Needs the pool from ``eval.calibration_candidates`` and labels in ``calibration.yaml``.
Paid unless GLASSBOX_PROVIDER=fake: needs GLASSBOX_EVAL_ALLOW_PAID=1.
"""

import argparse
import asyncio
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from eval.calibration_candidates import (
    CALIBRATION_PATH,
    DEFAULT_DEV_FRACTION,
    DEFAULT_SEED,
    HERE,
    LABELS,
    POOL_PATH,
    assign_split,
    read_pool,
    read_yaml,
)
from eval.judge import JUDGE_PROMPT_VERSION, Judge, ensure_paid_allowed, get_judge_llm

MIN_PER_CLASS = 10
TARGET_RATE = 0.85
LABEL_FIELD = {"faithfulness": "faithful", "relevance": "relevant"}
TEST_LOG = HERE / "runs" / "calibration-test-log.jsonl"


class CalibrationError(ValueError):
    pass


def agreement(pairs: list[tuple[bool, bool | None]]) -> dict:
    """Per-class agreement from (owner label passed, judge verdict passed) pairs.

    A verdict of None (unusable reply) counts as a disagreement in both classes.
    """
    owner_pass = [v for label, v in pairs if label]
    owner_fail = [v for label, v in pairs if not label]
    return {
        "n_pass": len(owner_pass),
        "n_fail": len(owner_fail),
        "tpr": sum(v is True for v in owner_pass) / len(owner_pass) if owner_pass else None,
        "tnr": sum(v is False for v in owner_fail) / len(owner_fail) if owner_fail else None,
    }


def evaluate_agreement(pairs: list[tuple[bool, bool | None]]) -> dict:
    """``agreement`` plus the refusal rule and the 0.85 gate."""
    result = agreement(pairs)
    if min(result["n_pass"], result["n_fail"]) < MIN_PER_CLASS:
        # Refuse: with too few cases a rate is noise, so no rate is returned at all.
        return {
            "n_pass": result["n_pass"],
            "n_fail": result["n_fail"],
            "tpr": None,
            "tnr": None,
            "reportable": False,
            "meets_target": None,
        }
    return {
        **result,
        "reportable": True,
        "meets_target": result["tpr"] >= TARGET_RATE and result["tnr"] >= TARGET_RATE,
    }


def parse_label(value, where: str) -> bool | None:
    if value is None:
        return None
    if value not in LABELS:
        raise CalibrationError(f"{where}: label must be pass, fail or null, got {value!r}")
    return value == "pass"


def load_labelled(yaml_path: Path = CALIBRATION_PATH, pool_path: Path = POOL_PATH) -> list[dict]:
    """Join owner labels with the pool texts, checking splits, hashes and duplicates."""
    data = read_yaml(yaml_path)
    seed = data.get("seed", DEFAULT_SEED)
    fraction = data.get("dev_fraction", DEFAULT_DEV_FRACTION)
    pool = {item["id"]: item for item in read_pool(pool_path)}
    seen: set[str] = set()
    out = []
    for entry in data["items"]:
        item_id = entry["id"]
        if item_id in seen:
            raise CalibrationError(f"{item_id}: listed twice")
        seen.add(item_id)
        expected = assign_split(item_id, seed=seed, dev_fraction=fraction)
        if entry.get("split") != expected:
            raise CalibrationError(
                f"{item_id}: split is {entry.get('split')!r} but the fixed seed assigns "
                f"{expected!r}; splits must not be edited"
            )
        labels = {
            judge: parse_label(entry.get(field), f"{item_id}.{field}")
            for judge, field in LABEL_FIELD.items()
        }
        if all(v is None for v in labels.values()):
            continue
        item = pool.get(item_id)
        if item is None:
            raise CalibrationError(f"{item_id}: labelled but missing from the pool file")
        if item["answer_hash"] != entry.get("answer_hash"):
            raise CalibrationError(f"{item_id}: the answer changed since it was labelled")
        out.append({**item, "labels": labels, "reason": entry.get("reason", ""), "split": expected})
    return out


async def judge_items(judge: Judge, items: list[dict]) -> list[dict]:
    """Attach each judge's verdict to every labelled item (one call per judge per item)."""
    results = []
    for item in items:
        needed = [name for name, label in item["labels"].items() if label is not None]
        verdicts = await judge.evaluate(
            item["question"], item["sources"], item["answer"], judges=needed
        )
        results.append({**item, "verdicts": verdicts})
    return results


def pairs_for(judged: list[dict], judge_name: str, split: str) -> list[tuple[bool, bool | None]]:
    return [
        (item["labels"][judge_name], item["verdicts"][judge_name].passed)
        for item in judged
        if item["split"] == split and item["labels"][judge_name] is not None
    ]


def pairs_by_kind(
    judged: list[dict], judge_name: str, split: str
) -> dict[str, list[tuple[bool, bool | None]]]:
    out: dict[str, list[tuple[bool, bool | None]]] = {}
    for item in judged:
        if item["split"] == split and item["labels"][judge_name] is not None:
            out.setdefault(item["kind"], []).append(
                (item["labels"][judge_name], item["verdicts"][judge_name].passed)
            )
    return out


def dev_disagreements(judged: list[dict], judge_name: str) -> list[dict]:
    """Dev-split items where the judge disagrees with the owner (few-shot candidates)."""
    return [
        item
        for item in judged
        if item["split"] == "dev"
        and item["labels"][judge_name] is not None
        and item["verdicts"][judge_name].passed != item["labels"][judge_name]
    ]


def report(judged: list[dict], *, splits: tuple[str, ...]) -> dict:
    """The printable result. Contains test-split numbers and ids-free counts only."""
    result: dict = {}
    for judge_name in LABEL_FIELD:
        entry: dict = {}
        if "test" in splits:
            entry["test"] = evaluate_agreement(pairs_for(judged, judge_name, "test"))
            # Per kind, so an easy kind (mismatched answers) cannot hide a weak one.
            entry["test_by_kind"] = {
                kind: agreement(pairs)
                for kind, pairs in sorted(pairs_by_kind(judged, judge_name, "test").items())
            }
        if "dev" in splits:
            entry["dev"] = agreement(pairs_for(judged, judge_name, "dev"))
            entry["dev_disagreements"] = [
                {
                    "id": item["id"],
                    "question": item["question"],
                    "answer": item["answer"],
                    "owner": "pass" if item["labels"][judge_name] else "fail",
                    "owner_reason": item["reason"],
                    "judge_critique": item["verdicts"][judge_name].critique
                    or item["verdicts"][judge_name].error,
                }
                for item in dev_disagreements(judged, judge_name)
            ]
        result[judge_name] = entry
    return result


def note_test_use(model_id: str, path: Path = TEST_LOG) -> str | None:
    """Log this test-split evaluation; warn if a different judge prompt/model already used it."""
    entry = {
        "at": datetime.now(UTC).isoformat(timespec="seconds"),
        "judge_model": model_id,
        "prompt_version": JUDGE_PROMPT_VERSION,
    }
    previous = []
    if path.is_file():
        previous = [json.loads(line) for line in path.read_text().splitlines() if line]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry) + "\n")
    others = [
        p
        for p in previous
        if (p["judge_model"], p["prompt_version"]) != (model_id, JUDGE_PROMPT_VERSION)
    ]
    if others:
        return (
            f"warning: the test split was already scored with {len(others)} other judge "
            "prompt/model version(s). Choosing between prompt versions on the test split "
            "more than once overfits it: draw fresh test labels before trusting this."
        )
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--split",
        choices=["dev", "test", "both"],
        default="dev",
        help="dev (default): print disagreements for prompt iteration; test: the held-out score",
    )
    parser.add_argument("--paid", action="store_true", help="allow a non-fake (paid) provider")
    args = parser.parse_args(argv)
    ensure_paid_allowed()
    if os.getenv("GLASSBOX_PROVIDER", "fake").lower() != "fake" and not args.paid:
        raise SystemExit("judge calibration makes paid calls; pass --paid as well")
    items = load_labelled()
    if not items:
        raise SystemExit("no labelled items in eval/calibration.yaml; see eval/README.md")
    splits = ("dev", "test") if args.split == "both" else (args.split,)
    items = [item for item in items if item["split"] in splits]
    judge = Judge(get_judge_llm())
    judged = asyncio.run(judge_items(judge, items))
    result = report(judged, splits=splits)
    warning = note_test_use(judge.model_id) if "test" in splits else None
    print(
        json.dumps(
            {"judge_model": judge.model_id, "prompt_version": JUDGE_PROMPT_VERSION, **result},
            indent=2,
        )
    )
    if warning:
        print(warning, file=sys.stderr)
    failed = False
    for name, entry in result.items():
        test = entry.get("test")
        if test is None:
            continue
        if not test["reportable"]:
            print(
                f"REFUSED {name}: needs at least {MIN_PER_CLASS} test cases per class, have "
                f"{test['n_pass']} pass / {test['n_fail']} fail",
                file=sys.stderr,
            )
            failed = True
        elif not test["meets_target"]:
            print(
                f"{name}: below {TARGET_RATE} on the test split; do not gate on it", file=sys.stderr
            )
            failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
