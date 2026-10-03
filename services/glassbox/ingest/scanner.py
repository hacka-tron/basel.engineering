"""Discover corpus files and quarantine likely secrets before ingestion.

The secret heuristic (``secret_reason``) recognizes AWS access keys, private-key
headers, provider token formats (GitHub, Slack, Anthropic, OpenAI-style, Google
API keys, Stripe live keys, JWTs, ``Bearer`` tokens) and long high-entropy
assigned values. Known limitations: Cloudflare API tokens have no distinguishing
prefix (40 plain characters), so they are only caught by the generic
high-entropy assignment rule; other providers' formats are not recognized; and
this is a line-by-line heuristic, not a replacement for ``detect-secrets``.
Placeholder examples that are long enough to look like real tokens (a Slack
bot-token prefix followed by a descriptive word, an Anthropic prefix then
filler) are quarantined too; that fails safe, so reword the doc.
"""

import hashlib
import math
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from services.glassbox.portfolio import PORTFOLIO_DIR, is_draft, portfolio_files

SUPPORTED_EXTENSIONS = frozenset({".md", ".tf", ".yml", ".yaml", ".py", ".ts", ".tsx"})
SYSTEM_DIRECTORIES = ("infra", "k8s", "services", "docs")
# About This System leaves out the test suite and dated implementation plans
# (owner decision, DESIGN-005 §9 item 4): their chunks crowded real sources out
# of the top 8 without answering visitor questions. Frontend source is not
# scanned at all. An excluded file counts as unseen, so the stale sweep lists
# (and in apply mode deletes) its earlier rows and keys.
EXCLUDED_SYSTEM_PREFIXES = ("services/tests/", "docs/superpowers/plans/")
# The owner's private About Basel repo (hacka-tron/basel.engineering-docs),
# checked out at corpus/about-me-private/ by release.yml before the image build
# when the release has its deploy key (absent otherwise). Only Markdown under
# its about-me/ folder is read (README, LICENSE and the rest are ignored), with
# the source path private/<path under about-me/>. Never committed (.gitignore).
PRIVATE_CHECKOUT_DIR = ("corpus", "about-me-private")
PRIVATE_ABOUT_ME_DIR = (*PRIVATE_CHECKOUT_DIR, "about-me")
PRIVATE_SOURCE_PREFIX = "private/"
_AWS_KEY = re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")
_PRIVATE_KEY = re.compile(r"-----BEGIN(?: [A-Z0-9]+)? PRIVATE KEY-----")
# Provider token formats: (label, pattern). Prefixes plus minimum lengths keep
# prose like "sk-learn" or "ghp_" in a sentence from matching. The JWT pattern
# starts after a non-class character (``(?<![A-Za-z0-9_-])``) so a long run of
# repeated ``eyJ-`` stays linear; the others are anchored by ``\b`` plus a
# fixed prefix or a bounded length, which keeps them linear too.
_PROVIDER_TOKENS = (
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")),
    ("GitHub token", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{50,}")),
    ("Slack token", re.compile(r"\bxox[abcdeprs]-[A-Za-z0-9-]{10,}")),
    ("Slack app token", re.compile(r"\bxapp-\d-[A-Za-z0-9-]{10,}")),
    (
        "Slack webhook URL",
        re.compile(r"hooks\.slack\.com/services/T[A-Z0-9]{6,}/B[A-Z0-9]{6,}/[A-Za-z0-9]{16,}"),
    ),
    ("Anthropic API key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}(?![0-9A-Za-z_-])")),
    ("Stripe live key", re.compile(r"\b[sr]k_live_[0-9A-Za-z]{16,}")),
    (
        "JWT",
        re.compile(
            r"(?<![A-Za-z0-9_-])eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"
        ),
    ),
)
# Kebab-case identifiers ("sk-some-long-name") also fit the OpenAI shape, so the
# body must mix letters and digits.
_OPENAI_KEY = re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{32,}")
_BEARER = re.compile(r"\bBearer\s+([A-Za-z0-9._~+/=-]{20,})", re.IGNORECASE)
# The lookbehind makes each name start at the beginning of a word run; without
# it a long run of word characters is rescanned from every position (quadratic).
# Names may start with a digit ("2fa_secret: ...").
_ASSIGNMENT = re.compile(
    r"""(?<![A-Za-z0-9_])['"]?[A-Za-z0-9_]+['"]?\s*[:=]\s*['"]?([A-Za-z0-9_+/=-]{33,})['"]?"""
)


@dataclass(frozen=True)
class SourceFile:
    corpus: str
    source_path: str
    path: Path


@dataclass(frozen=True)
class ScannedFile:
    source: SourceFile
    content_hash: str | None
    content: str | None
    error: str | None


def denied_path(path: Path) -> bool:
    """Apply path exclusions to any candidate, regardless of its allowlist origin."""
    return any(
        part == "secrets"
        or part.startswith("secrets.")
        or part.startswith(".env")
        or part.endswith(".tfvars")
        or ".tfstate" in part
        for part in (part.lower() for part in path.parts)
    )


def excluded_system_path(source_path: str) -> bool:
    """True for an About This System path outside the corpus scope."""
    return source_path.startswith(EXCLUDED_SYSTEM_PREFIXES)


def _high_entropy(value: str) -> bool:
    frequencies = {character: value.count(character) for character in set(value)}
    return (
        -sum((count / len(value)) * math.log2(count / len(value)) for count in frequencies.values())
        >= 4
    )


def _mixed(value: str) -> bool:
    """True when ``value`` has both letters and digits (not a plain word or number)."""
    return any(c.isdigit() for c in value) and any(c.isalpha() for c in value)


def _provider_token(line: str) -> str | None:
    for label, pattern in _PROVIDER_TOKENS:
        if pattern.search(line):
            return label
    if any(_mixed(match.group(0)) for match in _OPENAI_KEY.finditer(line)):
        return "OpenAI-style API key"
    if any(_mixed(match.group(1)) for match in _BEARER.finditer(line)):
        return "Bearer token"
    return None


def secret_reason(content: str) -> str | None:
    """Flag common key formats, provider tokens and long high-entropy assignment values."""
    for number, line in enumerate(content.splitlines(), 1):
        if _AWS_KEY.search(line):
            return f"possible AWS access key at line {number}"
        if _PRIVATE_KEY.search(line):
            return f"private key header at line {number}"
        if label := _provider_token(line):
            return f"possible {label} at line {number}"
        if any(_high_entropy(match.group(1)) for match in _ASSIGNMENT.finditer(line)):
            return f"possible high-entropy assigned value at line {number}"
    return None


def strip_front_matter(content: str) -> str:
    """Remove a leading --- block without interpreting its YAML values."""
    lines = content.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return content
    for index, line in enumerate(lines[1:], 1):
        if line.strip() == "---":
            return "".join(lines[index + 1 :])
    return content


def scan_sources(root: Path) -> Iterator[SourceFile]:
    """Yield supported files and denied-path candidates from every corpus.

    About Basel (``about_me``) comes only from the private checkout. A public
    ``corpus/about-me/`` directory is deliberately not scanned, so About Basel
    text can't be added to this repo and indexed by accident. Portfolio projects
    (``portfolio``) come from ``corpus/portfolio/``, drafts left out.
    """
    root = root.resolve()
    yield from _private_sources(root)
    yield from _portfolio_sources(root)
    for directory in SYSTEM_DIRECTORIES:
        base = root / directory
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            relative = path.relative_to(root)
            if (
                path.is_file()
                and not path.is_symlink()
                and not excluded_system_path(relative.as_posix())
                and (path.suffix in SUPPORTED_EXTENSIONS or denied_path(relative))
            ):
                yield SourceFile("about_system", relative.as_posix(), path)


def _private_sources(root: Path) -> Iterator[SourceFile]:
    private = root.joinpath(*PRIVATE_ABOUT_ME_DIR)
    if not private.is_dir():
        return
    for path in sorted(private.rglob("*.md")):
        relative = path.relative_to(private)
        # Skip hidden paths; never follow symlinks (they could point outside).
        if any(part.startswith(".") for part in relative.parts):
            continue
        if path.is_file() and not path.is_symlink():
            yield SourceFile("about_me", PRIVATE_SOURCE_PREFIX + relative.as_posix(), path)


def _portfolio_sources(root: Path) -> Iterator[SourceFile]:
    """Non-draft project files; hidden paths skipped, symlinks never followed.

    A file whose front matter can't be read is still yielded: ingest reports it and
    it counts as seen, so its last good version keeps serving.
    """
    base = root.joinpath(*PORTFOLIO_DIR)
    for path in portfolio_files(root):
        # A symlinked file or folder could point outside the repo: skip both.
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(base):
            continue
        try:
            draft = is_draft(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            draft = False  # scan_file reports the unreadable file
        if not draft:
            yield SourceFile("portfolio", path.relative_to(root).as_posix(), path)


def scan_file(source: SourceFile) -> ScannedFile:
    if denied_path(Path(source.source_path)):
        return ScannedFile(source, None, None, "denylisted path")
    try:
        file_bytes = source.path.read_bytes()
        content = file_bytes.decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return ScannedFile(source, None, None, f"cannot read UTF-8 content: {exc}")
    reason = secret_reason(content)
    if reason:
        return ScannedFile(source, None, None, reason)
    return ScannedFile(source, hashlib.sha256(file_bytes).hexdigest(), content, None)
