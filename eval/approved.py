"""The owner-approved example answers as an eval overlay (prompt v18).

The owner signed off 51 example answers (BACKLOG "Next up" item 2). They are About
Basel text, so they live in the private repo (`examples/approved-answers.yaml`)
and are read only from a local checkout at `corpus/about-me-private/`; without
one (CI, a fresh clone) the overlay is empty and nothing here is committed.

Each item becomes a golden-style case with its `golden` checks (`must_include`,
`must_not_include`, `max_words`). Mapping:

- An item whose approved answer is an abstention ("I don't have X in my memory")
  is `unanswerable` with `expect_abstain`; every other item is `fact`. The file's
  own `expect_abstain` flag marks deflections (pay, availability: "email me"),
  which are answers, not abstentions, so it is not copied as is.
- An item whose approved answer is a denial ("No, ...") accepts the memory
  phrasing as well (`abstain_ok`): factuality and caching win over the wording.
- Every case is graded against its own approved answer first. An item whose
  approved answer fails its own checks (a stale check written before a sign-off
  round changed the answer) gets `known_failure`, so it is reported separately
  and kept out of the rates until the check is fixed in the private repo.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from eval.graders import grade_case
from services.glassbox.providers.base import is_abstention

REPO_ROOT = Path(__file__).resolve().parent.parent
APPROVED_PATH = REPO_ROOT / "corpus" / "about-me-private" / "examples" / "approved-answers.yaml"
ORIGIN = "approved"


def approved_case(item: dict) -> dict:
    """One approved item as a golden-style case (without the approved answer text)."""
    golden = item.get("golden") or {}
    answer = str(item.get("answer") or "")
    abstains = is_abstention(answer)
    case: dict = {
        "id": str(item["id"]),
        "origin": ORIGIN,
        "corpus": "about_me",
        "category": "unanswerable" if abstains else "fact",
        "question": str(item["question"]),
        "needs_owner_review": True,
        "must_include": list(golden.get("must_include") or []),
        "must_not_include": list(golden.get("must_not_include") or []),
        "approved_category": item.get("category"),
        "few_shot": item.get("few_shot") is True,
    }
    if golden.get("max_words") is not None:
        case["max_words"] = int(golden["max_words"])
    if abstains:
        case["expect_abstain"] = True
    elif answer.lower().startswith("no,"):
        # A denial: the owner rule's "I don't have X in my memory" (an abstention,
        # never cached) is accepted too (owner decision, fix round 2026-10-05).
        case["abstain_ok"] = True
    own = grade_case(case, answer)
    if not own["passed"]:
        case["known_failure"] = (
            "approved answer fails its own golden checks ("
            + ", ".join(own["failures"])
            + "); fix the check in the private repo"
        )
    return case


def load_approved_cases(path: Path = APPROVED_PATH) -> list[dict]:
    """The overlay cases, or [] when there is no private checkout."""
    if not path.is_file():
        return []
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return []
    return [
        approved_case(item)
        for item in items
        if isinstance(item, dict) and item.get("id") and item.get("question")
    ]
