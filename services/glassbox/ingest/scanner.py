"""Discover public corpus files and quarantine likely secrets before ingestion."""

import hashlib
import math
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

SUPPORTED_EXTENSIONS = frozenset({".md", ".tf", ".yml", ".yaml", ".py", ".ts", ".tsx"})
SYSTEM_DIRECTORIES = ("infra", "k8s", "services", "docs")
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
    """Yield supported files and denied-path candidates from both corpora."""
    root = root.resolve()
    about_me = root / "corpus" / "about-me"
    if about_me.is_dir():
        for path in sorted(about_me.rglob("*.md")):
            if path.is_file() and not path.is_symlink():
                yield SourceFile("about_me", path.relative_to(root).as_posix(), path)
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
