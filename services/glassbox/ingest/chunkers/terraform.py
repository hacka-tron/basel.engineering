"""Split Terraform files into top-level blocks."""

import re

from services.glassbox.ingest.chunkers.base import Chunk

_BLOCK_START = re.compile(r"^(?:resource|module|variable|data|output|provider|locals|terraform)\b")
_NEXT_LINE_BRACE = re.compile(r"^\s*\{")


def chunk_terraform(text: str, source_path: str) -> list[Chunk]:
    """Return one chunk for each top-level Terraform block.

    Known limitation: block boundaries are found by counting every `{`/`}`
    character in the text, without stripping string literals or comments
    first. An unbalanced brace inside a string value or a `#`/`//` comment
    (e.g. `default = "use { here"`) will throw off block detection for the
    rest of the file. No real .tf files exist in this repo yet (Terraform
    lands in Milestone 2) — revisit with real content before relying on
    this for actual infra/ ingestion.
    """
    if not text.strip():
        return []

    lines = text.splitlines(keepends=True)
    chunks = []
    start = None
    depth = 0
    opened = False
    for index, line in enumerate(lines):
        if start is None:
            if not _BLOCK_START.match(line):
                continue
            if "{" not in line and (
                index + 1 == len(lines) or not _NEXT_LINE_BRACE.match(lines[index + 1])
            ):
                continue
            start = index

        if "{" in line:
            opened = True
        depth += line.count("{") - line.count("}")
        if opened and depth == 0:
            content = "".join(lines[start : index + 1])
            chunks.append(Chunk(content, source_path, start + 1, index + 1, len(content.split())))
            start = None
            opened = False

    if start is not None:
        content = "".join(lines[start:])
        chunks.append(Chunk(content, source_path, start + 1, len(lines), len(content.split())))

    return chunks
