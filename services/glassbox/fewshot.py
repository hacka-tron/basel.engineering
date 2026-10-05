"""Owner-approved few-shot examples for the answer prompts (prompt v18).

The owner signed off a set of example answers (BACKLOG "Next up" item 2). They are
About Basel text, so they live in the private About Basel repo
(`examples/approved-answers.yaml`), never in this public one. The release image
carries that file at `corpus/about-me-private/examples/approved-answers.yaml`
(.dockerignore lets in only it and the `about-me/` Markdown); the ingest scanner
reads only `about-me/*.md`, so the examples are never indexed as corpus.

Each prompt takes a few examples, chosen by id (`STRICT_EXAMPLE_IDS`,
`CASUAL_EXAMPLE_IDS`); any approved item can be listed. A listed id that is
missing is replaced by the remaining `few_shot: true` items of the same route
(casual = the "Casual & personal" category), so a renamed id still yields a full
set. Without the file (CI, local development, a build without the
private repo) or with an unusable one, the prompts use the v17 placeholder
examples, which carry no About Basel facts.

The approved answers are the owner's exact wording: they are used verbatim, never
paraphrased.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

LOGGER = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
APPROVED_EXAMPLES_PATH = (
    REPO_ROOT / "corpus" / "about-me-private" / "examples" / "approved-answers.yaml"
)
# Tests point this at a synthetic fixture; production never sets it.
APPROVED_EXAMPLES_ENV = "GLASSBOX_APPROVED_EXAMPLES_PATH"

# The strict prompt (work, skills, this system) gets four of the owner's answer
# shapes: a work-only tech answer, a production "No, but..." answer, an impact
# answer, and a light, honest deflection (why leaving), which keeps the owner's
# joke that v18's first cut dropped. Left out on purpose:
# - the two-sided tech answer (work and a personal project): on the v18 fresh index
#   any two-sided example made Nova Lite invent a work side for a personal project
#   and misattribute a metric, in both runs (the v17 Gotcha); two-sided answers come
#   from the dual-experience rule instead;
# - the freelance answer: the longest one, it pushed list-like system answers to
#   copy whole sources (one follow-ups answer went from 20 to 406 words); the
#   freelance question is answered from the corpus, which has the same wording.
# (Ablations, 2026-10-04/05.)
STRICT_EXAMPLE_IDS = (
    "rec-tech-kafka",
    "rec-tech-k8s-prod",
    "rec-impact-1",
    "rec-adv-employer",
)
# The casual prompt gets four fun answers. Favorite color and favorite food are
# deliberately left out, so the eval can check that unseen casual questions take
# the approved tone without copying an example.
CASUAL_EXAMPLE_IDS = (
    "rec-fun-lightmode",
    "rec-fun-coffee",
    "rec-fun-movie",
    "rec-casual-fun",
)
CASUAL_CATEGORY = "Casual & personal"
_LIMITS = {"strict": len(STRICT_EXAMPLE_IDS), "casual": len(CASUAL_EXAMPLE_IDS)}
_WHITESPACE = re.compile(r"\s+")


@dataclass(frozen=True)
class Example:
    question: str
    answer: str

    def line(self) -> str:
        return f"Q: {self.question} A: {self.answer}"


@dataclass(frozen=True)
class ExampleSet:
    # "approved" (loaded from the private file) or "placeholder" (v17's examples).
    source: str
    strict: tuple[Example, ...]
    casual: tuple[Example, ...]


EMPTY = ExampleSet(source="placeholder", strict=(), casual=())


def _clean(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = _WHITESPACE.sub(" ", value).strip()
    return text or None


def _items(data: object) -> list[dict]:
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict)]


def select_examples(data: object) -> ExampleSet:
    """Pick the strict and casual examples from the parsed approved-answers file."""
    # Every approved item can be listed by id (an explicit choice); only items marked
    # `few_shot: true` fill a set that is short of listed ids.
    approved: dict[str, tuple[str, Example, bool]] = {}
    for item in _items(data):
        question, answer = _clean(item.get("question")), _clean(item.get("answer"))
        if not question or not answer:
            continue
        route = "casual" if item.get("category") == CASUAL_CATEGORY else "strict"
        approved[str(item.get("id"))] = (
            route,
            Example(question, answer),
            item.get("few_shot") is True,
        )

    def pick(route: str, ids: tuple[str, ...]) -> tuple[Example, ...]:
        chosen = [approved[i][1] for i in ids if i in approved]
        for item_id, (item_route, example, few_shot) in approved.items():
            if len(chosen) >= _LIMITS[route]:
                break
            if few_shot and item_route == route and item_id not in ids:
                chosen.append(example)
        return tuple(chosen[: _LIMITS[route]])

    strict, casual = pick("strict", STRICT_EXAMPLE_IDS), pick("casual", CASUAL_EXAMPLE_IDS)
    if not strict and not casual:
        return EMPTY
    return ExampleSet(source="approved", strict=strict, casual=casual)


def load_examples(path: Path | None = None) -> ExampleSet:
    """Read and select the approved examples; the empty (placeholder) set on any problem."""
    if path is None:
        override = os.getenv(APPROVED_EXAMPLES_ENV)
        path = Path(override) if override else APPROVED_EXAMPLES_PATH
    try:
        if not path.is_file():
            return EMPTY
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        LOGGER.warning("Approved examples unreadable; using placeholder examples")
        return EMPTY
    return select_examples(data)


@lru_cache(maxsize=1)
def get_examples() -> ExampleSet:
    """The examples the answer prompts use, loaded once per process.

    The API loads them at startup (api/main.py lifespan); the eval loads them on
    first use. Logs only counts, never example text.
    """
    examples = load_examples()
    LOGGER.info(
        "Answer examples: %s (%d strict, %d casual)",
        examples.source,
        len(examples.strict),
        len(examples.casual),
    )
    return examples
