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
#
# v19 sign-off round 3: the personal-project Kubernetes answer, right after the
# production one, but only for a question that names Kubernetes (STRICT_EXAMPLE_TOPICS).
# Without it, "Have you worked with / used Kubernetes?" copied the nearest example:
# the work-only Kafka answer's employer ("Yes, I've used Kubernetes in production. At
# <Company>...", an invented claim) or the production answer's wording. Placed before
# the production example it cost "Kubernetes in production?" its "No"; in every strict
# prompt it made "Terraform?" answer from the "learning right now" section instead of
# the project (0/6, against 6/6 without it). Stored-retrieval ablations, 2026-10-05.
STRICT_EXAMPLE_IDS = (
    "rec-tech-kafka",
    "rec-tech-k8s-prod",
    "rec-tech-kubernetes",
    "rec-impact-1",
    "rec-adv-employer",
    "rec-adv-salary",
)
# Examples used only when the question matches the pattern (case-insensitive).
# v20: the pay answer joins pay questions only. "What do you make at <Company>?" was
# read as what I build there (a list of systems, 2/2 replays); with the pay answer
# next to the question it answers the pay deflection (4/4), and "Kubernetes in
# production?" keeps its "No" because other questions never see it. As a placeholder
# for every question it changed nothing for pay and cost the production "No".
STRICT_EXAMPLE_TOPICS = {
    "rec-tech-kubernetes": r"\b(kubernetes|k8s|k3s)\b",
    "rec-adv-salary": (
        r"\b(salary|salaries|compensation|earn|earnings|hourly rate|day rate|your rate"
        r"|pay range|get paid)\b|\b(what|how much) do you (make|earn)\b"
    ),
}
# The casual prompt gets four fun answers. Favorite color and favorite food are
# deliberately left out, so the eval can check that unseen casual questions take
# the approved tone without copying an example. v19: the favorite-show answer
# replaces the combined "movie or anime" one (owner, 2026-10-05: one topic per
# answer; a show question was answered with the movie).
CASUAL_EXAMPLE_IDS = (
    "rec-fun-lightmode",
    "rec-fun-coffee",
    "rec-fun-show",
    "rec-casual-fun",
)
CASUAL_CATEGORY = "Casual & personal"
# Prompt v19: playful replies to flirty or off-topic personal questions ("Do you love
# me?"), added to the casual prompt only for those questions (api/ask.py
# playful_question). Their own category, so they never fill the strict or casual
# sets; any `few_shot: true` item of the category fills a short set.
PLAYFUL_EXAMPLE_IDS = ("rec-playful-love", "rec-playful-marry")
PLAYFUL_CATEGORY = "Playful & off-topic"
_LIMITS = {
    "strict": len(STRICT_EXAMPLE_IDS),
    "casual": len(CASUAL_EXAMPLE_IDS),
    "playful": len(PLAYFUL_EXAMPLE_IDS),
}
_WHITESPACE = re.compile(r"\s+")


@dataclass(frozen=True)
class Example:
    question: str
    answer: str
    # A regex: the example is used only for questions it matches (None: always).
    topic: str | None = None

    def fits(self, question: str) -> bool:
        return self.topic is None or re.search(self.topic, question, re.IGNORECASE) is not None

    def line(self) -> str:
        return f"Q: {self.question} A: {self.answer}"


@dataclass(frozen=True)
class ExampleSet:
    # "approved" (loaded from the private file) or "placeholder" (v17's examples).
    source: str
    strict: tuple[Example, ...]
    casual: tuple[Example, ...]
    playful: tuple[Example, ...] = ()


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
        if item.get("draft") is True:
            # v19 review: an answer awaiting the owner's sign-off never goes live.
            continue
        category = item.get("category")
        route = (
            "casual"
            if category == CASUAL_CATEGORY
            else "playful"
            if category == PLAYFUL_CATEGORY
            else "strict"
        )
        item_id = str(item.get("id"))
        approved[item_id] = (
            route,
            Example(question, answer, STRICT_EXAMPLE_TOPICS.get(item_id)),
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
    playful = pick("playful", PLAYFUL_EXAMPLE_IDS)
    return ExampleSet(source="approved", strict=strict, casual=casual, playful=playful)


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
        "Answer examples: %s (%d strict, %d casual, %d playful)",
        examples.source,
        len(examples.strict),
        len(examples.casual),
        len(examples.playful),
    )
    return examples
