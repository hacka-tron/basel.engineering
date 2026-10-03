"""Merge labels downloaded from the labelling page into eval/calibration.yaml.

``python -m eval.import_labels ~/Downloads/calibration-labels.json``

Only the ``faithful``, ``relevant`` and ``reason`` lines of existing entries change
(comments and every other field stay byte-for-byte). Null / empty values in the
download never erase a label already in the file. Ids must exist in the yaml, and when
the pool file is present each entry's ``answer_hash`` must still match its answer.
Running it twice gives the same file.
"""

import argparse
import json
import re
from pathlib import Path

from eval.calibration_candidates import CALIBRATION_PATH, POOL_PATH, answer_hash, read_yaml

LABELS = ("pass", "fail")


class ImportError_(ValueError):
    pass


def validate(labels: dict, yaml_items: list[dict], pool: dict[str, dict] | None) -> None:
    known = {entry["id"]: entry for entry in yaml_items}
    for item_id, label in labels.items():
        if item_id not in known:
            raise ImportError_(f"{item_id}: not in calibration.yaml")
        if not isinstance(label, dict):
            raise ImportError_(f"{item_id}: label must be an object")
        for field in ("faithful", "relevant"):
            if label.get(field) not in (*LABELS, None):
                raise ImportError_(f"{item_id}.{field}: must be pass, fail or null")
        if not isinstance(label.get("reason", ""), str):
            raise ImportError_(f"{item_id}.reason: must be a string")
        if pool is not None:
            row = pool.get(item_id)
            if row is None:
                raise ImportError_(f"{item_id}: missing from the pool file")
            if answer_hash(row["answer"]) != known[item_id].get("answer_hash"):
                raise ImportError_(f"{item_id}: the answer changed since the entry was made")


def merge_text(text: str, labels: dict) -> str:
    """Rewrite the label lines of matching entries; leave everything else untouched."""
    lines = text.splitlines(keepends=True)
    current = None
    for i, line in enumerate(lines):
        m = re.match(r"\s*- id:\s*(\S+)", line)
        if m:
            current = m.group(1)
            continue
        label = labels.get(current) if current else None
        if not label:
            continue
        m = re.match(r"(\s+)(faithful|relevant):", line)
        if m and label.get(m.group(2)):
            lines[i] = f"{m.group(1)}{m.group(2)}: {label[m.group(2)]}\n"
            continue
        m = re.match(r"(\s+)reason:", line)
        if m and label.get("reason", "").strip():
            lines[i] = f"{m.group(1)}reason: {json.dumps(label['reason'].strip())}\n"
    return "".join(lines)


def import_labels(
    labels_path: Path, yaml_path: Path = CALIBRATION_PATH, pool_path: Path = POOL_PATH
):
    labels = json.loads(labels_path.read_text(encoding="utf-8"))
    if not isinstance(labels, dict):
        raise ImportError_("labels file must be an object {id: {faithful, relevant, reason}}")
    pool = None
    if pool_path.is_file():
        pool = {
            r["id"]: r
            for r in map(json.loads, pool_path.read_text(encoding="utf-8").splitlines())
            if r
        }
    validate(labels, read_yaml(yaml_path)["items"], pool)
    old = yaml_path.read_text(encoding="utf-8")
    new = merge_text(old, labels)
    if new != old:
        yaml_path.write_text(new, encoding="utf-8")
    done = [e for e in read_yaml(yaml_path)["items"] if e.get("faithful") and e.get("relevant")]
    return len(labels), len(done), new != old


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("labels", type=Path)
    parser.add_argument("--yaml", type=Path, default=CALIBRATION_PATH)
    parser.add_argument("--pool", type=Path, default=POOL_PATH)
    args = parser.parse_args(argv)
    try:
        total, done, changed = import_labels(args.labels.expanduser(), args.yaml, args.pool)
    except (ImportError_, OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"import failed: {exc}") from exc
    print(
        f"{total} entries read, {done} fully labelled in the yaml, "
        f"file {'updated' if changed else 'unchanged'}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
