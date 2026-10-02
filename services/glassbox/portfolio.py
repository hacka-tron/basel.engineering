"""Portfolio project files: format, validation and the text the chatbot indexes.

One Markdown file per project in ``corpus/portfolio/<slug>.md`` (portfolio spec
§6.1). YAML front matter carries the card and sheet fields; the body is "About the
project". This module is the one Python definition of the format:

* the ingest scanner skips ``draft: true`` files (``is_draft``), and ingestion
  indexes ``index_text(parse_project(...))``;
* CI checks the repo with ``validate_tree`` (``services/tests/test_portfolio.py``);
  locally: ``python -m services.glassbox.portfolio``.

The frontend parses the same files with its own TypeScript code (portfolio spec §7);
keep it in step with these rules.

Rules. Required: ``title`` and ``one_liner`` (text), ``kind`` (``personal`` or
``freelance``), ``year`` (a whole number, 1990 to 2100), ``stack`` (a non-empty list
of text). Optional: ``order`` (whole number; grid order, ascending), ``links``
(``live`` and/or ``code``, each an ``https://`` URL), ``visuals`` (a list of ``src``,
``alt``, ``aspect`` and an optional ``caption``; ``src`` is a path under
``frontend/public/portfolio/`` starting with the file's slug; ``aspect`` is ``16/10``,
``4/3`` or ``9/19.5``) and ``draft`` (``true`` or ``false``). Any other field is an
error, so a typo can't pass silently, and so is a repeated field. Only the literal
words ``true`` and ``false`` are booleans (YAML 1.2 rules: ``yes``/``on`` are text).
The slug is the file name without ``.md``: lowercase letters, digits and hyphens,
after at most one leading underscore. Screenshots must be real files, not symlinks.
The CI check also fails a file the personal-data guard would redact, because the site shows
these files as written.
"""

import argparse
import hashlib
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

import yaml

from services.glassbox.privacy import find_pii

PORTFOLIO_DIR = ("corpus", "portfolio")
PUBLIC_DIR = ("frontend", "public", "portfolio")
# Bump when index_text's output changes: it is folded into every portfolio document's
# content hash, so each project is re-embedded once in the new format.
INDEX_VERSION = "1"
REQUIRED_FIELDS = ("title", "one_liner", "kind", "year", "stack")
OPTIONAL_FIELDS = ("order", "links", "visuals", "draft")
KINDS = ("personal", "freelance")
KIND_LABELS = {"personal": "Personal project", "freelance": "Freelance project"}
ASPECTS = ("16/10", "4/3", "9/19.5")
LINK_KEYS = ("live", "code")
LINK_LABELS = {"live": "Live site", "code": "Code"}
VISUAL_FIELDS = ("src", "alt", "caption", "aspect")
MIN_YEAR, MAX_YEAR = 1990, 2100
_SLUG = re.compile(r"_?[a-z0-9][a-z0-9-]*")
_HTTPS_URL = re.compile(r"https://[^\s/]+\S*")


class PortfolioError(ValueError):
    """A portfolio file that breaks the format; the message lists every problem."""


@dataclass(frozen=True)
class Visual:
    src: str
    alt: str
    aspect: str
    caption: str | None = None


@dataclass(frozen=True)
class Project:
    slug: str
    title: str
    one_liner: str
    kind: str
    year: int
    stack: tuple[str, ...]
    body: str
    order: int | None = None
    links: dict[str, str] = field(default_factory=dict)
    visuals: tuple[Visual, ...] = ()
    draft: bool = False


class _DuplicateField(yaml.YAMLError):
    def __init__(self, name):
        super().__init__(name)
        self.name = name


class _StrictLoader(yaml.SafeLoader):
    """PyYAML's safe loader, made as strict as a YAML 1.2 parser on two points.

    Only the literal words ``true`` and ``false`` are booleans (YAML 1.1 also reads
    ``yes``, ``on``, ``On``... as true; the frontend's YAML 1.2 parser reads them as
    text), and a repeated field is an error instead of the last one silently winning.
    Without this, CI and the frontend could disagree on whether a project is a draft.
    """

    def construct_mapping(self, node, deep=False):
        seen = set()
        for key_node, _ in node.value:
            key = self.construct_object(key_node, deep=True)
            try:
                duplicate = key in seen
            except TypeError:  # an unhashable key; construct_mapping reports it
                continue
            if duplicate:
                raise _DuplicateField(key)
            seen.add(key)
        return super().construct_mapping(node, deep=deep)


_BOOL_TAG = "tag:yaml.org,2002:bool"
_StrictLoader.yaml_implicit_resolvers = {
    first: [resolver for resolver in resolvers if resolver[0] != _BOOL_TAG]
    for first, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
_StrictLoader.add_implicit_resolver(_BOOL_TAG, re.compile(r"^(?:true|false)$"), list("tf"))


def split_front_matter(text: str) -> tuple[dict, str]:
    """The YAML front matter as a dict, and the body after it.

    A leading UTF-8 byte-order mark (some Windows editors add one) is ignored, as in
    the frontend's loader (frontend/vite-plugins/portfolio.ts).
    """
    lines = text.removeprefix("\ufeff").splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        raise PortfolioError("missing front matter: the file must start with a '---' line")
    end = next((i for i, line in enumerate(lines[1:], 1) if line.strip() == "---"), None)
    if end is None:
        raise PortfolioError("front matter is not closed by a second '---' line")
    try:
        data = yaml.load("".join(lines[1:end]), Loader=_StrictLoader)  # a SafeLoader subclass
    except _DuplicateField as exc:
        raise PortfolioError(
            f"front matter has the field '{exc.name}' more than once; keep one"
        ) from None
    except yaml.YAMLError as exc:
        raise PortfolioError(f"front matter is not valid YAML: {exc}") from None
    if not isinstance(data, dict):
        raise PortfolioError("front matter must be a list of 'field: value' lines")
    return data, "".join(lines[end + 1 :])


def is_draft(text: str) -> bool:
    """True only for a readable file whose front matter says ``draft: true``."""
    try:
        data, _ = split_front_matter(text)
    except PortfolioError:
        return False
    return data.get("draft") is True


def _text(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _whole_number(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _visual(index: int, item, slug: str, errors: list[str]) -> Visual | None:
    where = f"visuals[{index}]"
    if not isinstance(item, dict):
        errors.append(f"{where} must have src, alt and aspect fields")
        return None
    before = len(errors)
    unknown = sorted(str(key) for key in item if key not in VISUAL_FIELDS)
    if unknown:
        errors.append(f"{where} has unknown fields: {', '.join(unknown)}")
    src, alt, aspect, caption = (item.get(name) for name in ("src", "alt", "aspect", "caption"))
    if not _text(src):
        errors.append(f"{where}.src is missing")
    else:
        parts = PurePosixPath(src).parts
        outside = "\\" in src or src.startswith("/") or ".." in parts
        if outside or len(parts) < 2 or parts[0] != slug:
            errors.append(
                f"{where}.src must be a path like {slug}/picture.png under "
                f"frontend/public/portfolio/ (got {src!r})"
            )
    if not _text(alt):
        errors.append(f"{where}.alt is missing (describe the image for screen readers)")
    if aspect not in ASPECTS:
        hint = ""
        if aspect is not None and not isinstance(aspect, str):
            # aspect: 16:10 unquoted is read by YAML as the number 970.
            hint = '; put it in quotes, like aspect: "16/10"'
        errors.append(f"{where}.aspect must be one of {', '.join(ASPECTS)} (got {aspect!r}){hint}")
    if caption is not None and not isinstance(caption, str):
        errors.append(f"{where}.caption must be text")
    if len(errors) > before:
        return None
    return Visual(src=src, alt=alt.strip(), aspect=aspect, caption=caption)


def parse_project(text: str, slug: str) -> Project:
    """Parse and validate one project file; raises PortfolioError listing every problem."""
    data, body = split_front_matter(text)
    errors: list[str] = []
    if not _SLUG.fullmatch(slug):
        errors.append(
            f"file name {slug}.md must be lowercase letters, digits and hyphens (like jobpilot.md)"
        )
    unknown = sorted(str(key) for key in data if key not in REQUIRED_FIELDS + OPTIONAL_FIELDS)
    if unknown:
        errors.append(f"unknown fields: {', '.join(unknown)}")
    for name in REQUIRED_FIELDS:
        if data.get(name) is None:
            errors.append(f"missing required field '{name}'")
    title, one_liner, kind, year, stack = (data.get(name) for name in REQUIRED_FIELDS)
    for name, value in (("title", title), ("one_liner", one_liner)):
        if value is not None and not _text(value):
            errors.append(f"{name} must be text")
    if kind is not None and kind not in KINDS:
        errors.append(f"kind must be one of {', '.join(KINDS)} (got {kind!r})")
    if year is not None and not (_whole_number(year) and MIN_YEAR <= year <= MAX_YEAR):
        errors.append(f"year must be a whole number like 2026 (got {year!r})")
    if stack is not None and not (
        isinstance(stack, list) and stack and all(_text(item) for item in stack)
    ):
        errors.append("stack must be a list like [TypeScript, React]")
    order = data.get("order")
    if order is not None and not _whole_number(order):
        errors.append(f"order must be a whole number (got {order!r})")
    links = data.get("links") or {}
    if not isinstance(links, dict):
        errors.append("links must have live and/or code entries")
        links = {}
    for key, url in links.items():
        if key not in LINK_KEYS:
            errors.append(f"unknown link '{key}' (use {', '.join(LINK_KEYS)})")
        elif not (isinstance(url, str) and _HTTPS_URL.fullmatch(url)):
            errors.append(f"links.{key} must be an https:// URL (got {url!r})")
    raw_visuals = data.get("visuals") or []
    if not isinstance(raw_visuals, list):
        errors.append("visuals must be a list")
        raw_visuals = []
    visuals = [_visual(index, item, slug, errors) for index, item in enumerate(raw_visuals)]
    draft = data.get("draft", False)
    if not isinstance(draft, bool):
        errors.append(f"draft must be true or false (got {draft!r})")
    if errors:
        raise PortfolioError("; ".join(errors))
    return Project(
        slug=slug,
        title=title.strip(),
        one_liner=one_liner.strip(),
        kind=kind,
        year=year,
        stack=tuple(item.strip() for item in stack),
        body=body,
        order=order,
        links={key: links[key] for key in LINK_KEYS if key in links},
        visuals=tuple(visuals),
        draft=draft,
    )


def index_text(project: Project) -> str:
    """What the chatbot indexes: a short preface of the card fields, then the body.

    The preface makes questions like "which projects use React?" retrieve the file.
    """
    lines = [
        f"# {project.title}",
        "",
        project.one_liner,
        "",
        f"- Kind: {KIND_LABELS[project.kind]}",
        f"- Year: {project.year}",
        f"- Stack: {', '.join(project.stack)}",
    ]
    lines += [f"- {LINK_LABELS[key]}: {url}" for key, url in project.links.items()]
    body = project.body.strip()
    return "\n".join(lines) + "\n" + (f"\n{body}\n" if body else "")


def index_content_hash(raw_hash: str) -> str:
    """The file hash with the index format version folded in (see INDEX_VERSION)."""
    return hashlib.sha256(f"{raw_hash}:portfolio-index-v{INDEX_VERSION}".encode()).hexdigest()


def portfolio_files(root: Path) -> list[Path]:
    """Every Markdown file under corpus/portfolio/, hidden paths left out."""
    base = root.joinpath(*PORTFOLIO_DIR)
    if not base.is_dir():
        return []
    return [
        path
        for path in sorted(base.rglob("*.md"))
        if not any(part.startswith(".") for part in path.relative_to(base).parts)
    ]


def _symlinked(public_root: Path, src: str) -> bool:
    """True if the image or any folder between it and public_root is a symlink."""
    path = public_root
    for part in PurePosixPath(src).parts:
        path = path / part
        if path.is_symlink():
            return True
    return False


def missing_visual_files(project: Project, public_root: Path) -> list[str]:
    """Screenshots that aren't committed as real files (symlinks are never read)."""
    problems = []
    for visual in project.visuals:
        if _symlinked(public_root, visual.src):
            problems.append(
                f"visual {visual.src} is a symlink; commit the image itself "
                "(symlinks are never read)"
            )
        elif not (public_root / visual.src).is_file():
            problems.append(f"visual {visual.src} is not in frontend/public/portfolio/")
    return problems


def personal_data_problems(text: str) -> list[str]:
    """Where the personal-data guard would redact something; never the value itself."""
    problems = []
    for finding in find_pii(text):
        line = text.count("\n", 0, finding.start) + 1
        problems.append(f"possible personal data ({finding.category}) at line {line}")
    return problems


def validate_tree(root: Path) -> dict[str, list[str]]:
    """Problems per repo-relative file under corpus/portfolio/; empty when all are valid."""
    base = root.joinpath(*PORTFOLIO_DIR)
    public = root.joinpath(*PUBLIC_DIR)
    problems: dict[str, list[str]] = {}
    for path in portfolio_files(root):
        errors: list[str] = []
        if path.parent != base:
            errors.append("project files go directly in corpus/portfolio/, not in a subfolder")
        if path.is_symlink():
            errors.append("symlinks are never read; commit the file itself")
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            errors.append(f"cannot read UTF-8 content: {exc}")
        else:
            try:
                project = parse_project(text, path.stem)
            except PortfolioError as exc:
                errors.append(str(exc))
            else:
                if not project.draft:
                    errors.extend(missing_visual_files(project, public))
            errors.extend(personal_data_problems(text))
        if errors:
            problems[path.relative_to(root).as_posix()] = errors
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m services.glassbox.portfolio",
        description="Check the portfolio project files (corpus/portfolio/*.md).",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
        help="repository root (default: this checkout)",
    )
    args = parser.parse_args(argv)
    problems = validate_tree(args.root)
    for path, errors in problems.items():
        for error in errors:
            print(f"{path}: {error}")
    count = len(portfolio_files(args.root))
    if problems:
        print(f"{len(problems)} of {count} portfolio files have problems", file=sys.stderr)
        return 1
    print(f"{count} portfolio files OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
