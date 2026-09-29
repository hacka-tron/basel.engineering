"""Split YAML files at document separators."""

from services.glassbox.ingest.chunkers.base import Chunk


def chunk_yaml(text: str, source_path: str) -> list[Chunk]:
    """Return one chunk per non-empty YAML document."""
    if not text.strip():
        return []

    lines = text.splitlines(keepends=True)
    chunks = []
    start = 0

    def append_document(end: int) -> None:
        content = "".join(lines[start:end])
        if content.strip():
            chunks.append(Chunk(content, source_path, start + 1, end, len(content.split())))

    for index, line in enumerate(lines):
        if line.rstrip(" \t\r\n") == "---":
            append_document(index)
            start = index + 1

    append_document(len(lines))
    return chunks
