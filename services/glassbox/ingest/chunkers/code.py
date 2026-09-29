"""Split Python and TypeScript files at top-level definitions."""

import re

from services.glassbox.ingest.chunkers.base import Chunk

_DEFINITION = re.compile(
    r"^(?:def |async def |class |(?:export (?:default )?)?(?:async )?function |"
    r"(?:export (?:default )?)?class |(?:export )?const [A-Za-z_$][\w$]*\s*=\s*"
    r"(?:async\s*)?\()"
)


def chunk_code(text: str, source_path: str) -> list[Chunk]:
    """Return a leading content chunk and one chunk per top-level definition.

    Known limitations: decorator grouping only walks back over consecutive
    single-line `@...` decorators — a multi-line decorator call (e.g.
    `@app.get(\n    "/items",\n)`) won't be recognized as part of the
    following definition. TypeScript matching doesn't cover typed arrow
    functions (`const f: Handler = (...) =>`) or generic type parameters
    (`const f = <T>(x: T) =>`). No TypeScript files exist in this repo yet
    (frontend lands in a later phase) — revisit with real content then.
    """
    if not text.strip():
        return []

    lines = text.splitlines(keepends=True)
    definitions = []
    for index, line in enumerate(lines):
        if _DEFINITION.match(line):
            start = index
            while start > 0 and lines[start - 1].startswith("@"):
                start -= 1
            definitions.append(start)

    def has_content(candidate_lines: list[str]) -> bool:
        return any(
            line.strip() and not line.lstrip().startswith(("#", "//")) for line in candidate_lines
        )

    if not definitions:
        if not has_content(lines):
            return []
        content = "".join(lines)
        return [Chunk(content, source_path, 1, len(lines), len(content.split()))]

    chunks = []
    first = definitions[0]
    preamble = "".join(lines[:first])
    if has_content(lines[:first]):
        chunks.append(Chunk(preamble, source_path, 1, first, len(preamble.split())))

    for start, end in zip(definitions, definitions[1:] + [len(lines)], strict=True):
        content = "".join(lines[start:end])
        chunks.append(Chunk(content, source_path, start + 1, end, len(content.split())))

    return chunks
