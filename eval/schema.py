"""Load and validate the golden answer dataset (`eval/golden.yaml`)."""

import re
from pathlib import Path

import yaml

from eval.graders import ANSWERABLE_CATEGORIES, CATEGORIES

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent
GOLDEN_PATH = HERE / "golden.yaml"
CORPORA = frozenset({"about_me", "about_system"})
_CASE_KEYS = frozenset(
    {
        "id",
        "corpus",
        "category",
        "question",
        "history",
        "expected_sources",
        "gold_snippets",
        "must_include",
        "must_not_include",
        "rewrite_must_include",
        "expect_abstain",
        "holdout",
        "needs_owner_review",
        "origin",
        "notes",
        "live_but_off",
        "known_failure",
    }
)
_ORIGINS = frozenset({"questions.yaml", "suggested", "new"})
_ID = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_WHITESPACE = re.compile(r"\s+")


def normalize_snippet_text(text: str) -> str:
    return _WHITESPACE.sub(" ", text).strip().casefold()


def snippet_in(text: str, snippet: str) -> bool:
    """True when the snippet occurs in the text, ignoring case and whitespace runs.

    Phase 2's chunk-level retrieval hit uses the same rule, so a snippet that
    wraps across lines in the source still matches a chunk.
    """
    return normalize_snippet_text(snippet) in normalize_snippet_text(text)


class GoldenError(ValueError):
    pass


def _fail(case_id: str, message: str) -> None:
    raise GoldenError(f"{case_id}: {message}")


PRIVATE_PREFIX = "private/"


def _source_file(root: Path, source: str) -> Path | None:
    """The file behind an expected source, or None for a private file with no local checkout.

    ``private/bio.md`` is ``corpus/about-me-private/about-me/bio.md`` in the git-ignored
    release-time checkout; CI and fresh clones have none, so those snippets go unchecked.
    """
    if not source.startswith(PRIVATE_PREFIX):
        return root / source
    path = root / "corpus" / "about-me-private" / "about-me" / source.removeprefix(PRIVATE_PREFIX)
    return path if path.is_file() else None


def validate_case(case: dict, root: Path = REPO_ROOT) -> None:
    case_id = case.get("id", "<missing id>")
    if not isinstance(case_id, str) or not _ID.match(case_id):
        _fail(str(case_id), "id must be lowercase letters, digits and dashes")
    unknown = set(case) - _CASE_KEYS
    if unknown:
        _fail(case_id, f"unknown keys {sorted(unknown)}")
    if case.get("corpus") not in CORPORA:
        _fail(case_id, f"unknown corpus {case.get('corpus')!r}")
    category = case.get("category")
    if category not in CATEGORIES:
        _fail(case_id, f"unknown category {category!r}")
    if not isinstance(case.get("question"), str) or not case["question"].strip():
        _fail(case_id, "question is required")
    if case.get("origin", "new") not in _ORIGINS:
        _fail(case_id, f"unknown origin {case.get('origin')!r}")

    for key in ("must_include", "must_not_include", "rewrite_must_include"):
        patterns = case.get(key, [])
        if not isinstance(patterns, list):
            _fail(case_id, f"{key} must be a list")
        for pattern in patterns:
            try:
                re.compile(pattern, re.IGNORECASE)
            except (re.error, TypeError) as exc:
                _fail(case_id, f"{key} pattern {pattern!r} does not compile: {exc}")

    sources = case.get("expected_sources", [])
    for source in sources:
        # About Basel files live in a private repo, so a `private/...` source can only be
        # checked when a local checkout of it exists (see _source_file).
        if not source.startswith(PRIVATE_PREFIX) and not (root / source).is_file():
            _fail(case_id, f"expected source {source} does not exist")
        if (case["corpus"] == "about_me") != source.startswith(PRIVATE_PREFIX):
            _fail(case_id, f"expected source {source} is outside corpus {case['corpus']}")
    snippets = case.get("gold_snippets", [])
    if snippets and not sources:
        _fail(case_id, "gold_snippets need expected_sources to check against")
    checkable = [_source_file(root, source) for source in sources]
    contents = [path.read_text(encoding="utf-8") for path in checkable if path is not None]
    # Every source is a private file with no local checkout: snippets can't be verified here.
    verifiable = bool(contents) or not sources
    for snippet in snippets:
        if not isinstance(snippet, str) or not snippet.strip():
            _fail(case_id, "each gold snippet must be a non-empty string")
        if verifiable and not any(snippet_in(content, snippet) for content in contents):
            _fail(case_id, f"gold snippet not found in any expected source: {snippet!r}")

    if category in ANSWERABLE_CATEGORIES and not (sources and snippets):
        _fail(case_id, "answerable cases need expected_sources and gold_snippets")
    if category == "unanswerable" and case.get("expect_abstain") is not True:
        _fail(case_id, "unanswerable cases must set expect_abstain: true")
    if category in ANSWERABLE_CATEGORIES and case.get("expect_abstain"):
        _fail(case_id, "an answerable case cannot expect an abstention")

    known_failure = case.get("known_failure")
    if known_failure is not None and (
        not isinstance(known_failure, str) or not known_failure.strip()
    ):
        _fail(case_id, "known_failure must be a short BACKLOG reference string")
    if "live_but_off" in case and (category != "live" or case["live_but_off"] is not True):
        _fail(case_id, "live_but_off: true is only for live cases")

    history = case.get("history", [])
    if category == "multi_turn" and not history:
        _fail(case_id, "multi_turn cases need history")
    if category == "multi_turn" and not case.get("rewrite_must_include"):
        _fail(case_id, "multi_turn cases need rewrite_must_include")
    if case.get("rewrite_must_include") and not history:
        _fail(case_id, "rewrite_must_include needs history (first questions are not rewritten)")
    for index, message in enumerate(history):
        expected_role = "user" if index % 2 == 0 else "assistant"
        if set(message) != {"role", "content"} or message["role"] != expected_role:
            _fail(case_id, "history must alternate user/assistant, starting with user")
        if not message["content"].strip():
            _fail(case_id, "history messages need content")
    if history and history[-1]["role"] != "assistant":
        _fail(case_id, "history must end with an assistant turn")

    if case["corpus"] == "about_me" and case.get("needs_owner_review") is not True:
        _fail(case_id, "About Basel cases must be marked needs_owner_review: true")


def load_golden(path: Path = GOLDEN_PATH, root: Path = REPO_ROOT) -> list[dict]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if data.get("version") != 2:
        raise GoldenError("golden.yaml must declare version: 2")
    cases = data["cases"]
    ids = [case.get("id") for case in cases]
    duplicates = sorted({case_id for case_id in ids if ids.count(case_id) > 1})
    if duplicates:
        raise GoldenError(f"duplicate case ids: {duplicates}")
    for case in cases:
        validate_case(case, root)
    return cases
