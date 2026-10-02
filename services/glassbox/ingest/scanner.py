"""Discover public corpus files and quarantine likely secrets before ingestion."""

import hashlib
import math
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

SUPPORTED_EXTENSIONS = frozenset({".md", ".tf", ".yml", ".yaml", ".py", ".ts", ".tsx"})
SYSTEM_DIRECTORIES = ("infra", "k8s", "services", "docs")
# The owner's private About Basel repo (hacka-tron/basel.engineering-docs),
# checked out at corpus/about-me-private/ by release.yml before the image build
# when the release has its deploy key (absent otherwise). Only Markdown under
# its about-me/ folder is read (README, LICENSE and the rest are ignored), with
# the source path private/<path under about-me/>. Never committed (.gitignore).
PRIVATE_CHECKOUT_DIR = ("corpus", "about-me-private")
PRIVATE_ABOUT_ME_DIR = (*PRIVATE_CHECKOUT_DIR, "about-me")
PRIVATE_SOURCE_PREFIX = "private/"
PUBLIC_ABOUT_ME_PREFIX = "corpus/about-me/"
_AWS_KEY = re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")
_PRIVATE_KEY = re.compile(r"-----BEGIN(?: [A-Z0-9]+)? PRIVATE KEY-----")
_ASSIGNMENT = re.compile(
    r"""['"]?[A-Za-z_][A-Za-z0-9_]*['"]?\s*[:=]\s*['"]?([A-Za-z0-9_+/=-]{33,})['"]?"""
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


def _high_entropy(value: str) -> bool:
    frequencies = {character: value.count(character) for character in set(value)}
    return (
        -sum((count / len(value)) * math.log2(count / len(value)) for count in frequencies.values())
        >= 4
    )


def secret_reason(content: str) -> str | None:
    """Flag common key formats and long high-entropy assignment values."""
    for number, line in enumerate(content.splitlines(), 1):
        if _AWS_KEY.search(line):
            return f"possible AWS access key at line {number}"
        if _PRIVATE_KEY.search(line):
            return f"private key header at line {number}"
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
    """Yield supported files and denied-path candidates from both corpora.

    A public ``corpus/about-me/`` file with a private twin (``shadowed_public_paths``)
    is not yielded: the private copy replaces it, so the index never holds the
    same text twice.
    """
    root = root.resolve()
    private_sources = list(_private_sources(root))
    shadowed = shadowed_public_paths(root, private_sources)
    about_me = root / "corpus" / "about-me"
    if about_me.is_dir():
        for path in sorted(about_me.rglob("*.md")):
            source_path = path.relative_to(root).as_posix()
            if path.is_file() and not path.is_symlink() and source_path not in shadowed:
                yield SourceFile("about_me", source_path, path)
    yield from private_sources
    for directory in SYSTEM_DIRECTORIES:
        base = root / directory
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if (
                path.is_file()
                and not path.is_symlink()
                and (path.suffix in SUPPORTED_EXTENSIONS or denied_path(path.relative_to(root)))
            ):
                yield SourceFile("about_system", path.relative_to(root).as_posix(), path)


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


def private_twin(public_path: str) -> str:
    """``corpus/about-me/bio.md`` -> ``private/bio.md``."""
    return PRIVATE_SOURCE_PREFIX + public_path.removeprefix(PUBLIC_ABOUT_ME_PREFIX)


def shadowed_public_paths(root: Path, private_sources=None) -> set[str]:
    """Public about-me paths that a private file of the same relative path replaces.

    Transition aid while the public copies still exist (they stay until a
    release has ingested the private repo): with the checkout present,
    ``corpus/about-me/bio.md`` gives way to ``private/bio.md``. Without the
    checkout nothing is shadowed.
    """
    root = root.resolve()
    if private_sources is None:
        private_sources = list(_private_sources(root))
    shadowed = set()
    for source in private_sources:
        public = PUBLIC_ABOUT_ME_PREFIX + source.source_path.removeprefix(PRIVATE_SOURCE_PREFIX)
        if (root / public).is_file():
            shadowed.add(public)
    return shadowed


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
