"""Split Markdown into heading-aware chunks with word-based size limits."""

import re
from bisect import bisect_right

from services.glassbox.ingest.chunkers.base import Chunk

_HEADING = re.compile(r"^#{1,6} ")
_WORD = re.compile(r"\S+")
# An H2-or-deeper heading: text before the first one is a document's preamble.
_SUBHEADING = re.compile(r"^#{2,6} ", re.MULTILINE)


def chunk_markdown(text: str, source_path: str, *, merge_short: bool = True) -> list[Chunk]:
    """Chunk Markdown by headings, merging short sections and splitting long ones.

    ``merge_short=False`` (the About Basel corpus, prompt v19) keeps every section
    its own chunk, so a question pulls the one-topic section that answers it rather
    than a chunk of several neighbouring topics (owner, 2026-10-05: "either movie or
    show"). Only a document's preamble (text before the first heading below H1,
    e.g. the H1 title and its intro) still merges into the section after it.
    """
    if not text or not text.strip():
        return []

    lines = text.splitlines(keepends=True)
    line_starts = []
    offset = 0
    for line in lines:
        line_starts.append(offset)
        offset += len(line)

    heading_starts = [
        line_starts[index] for index, line in enumerate(lines) if _HEADING.match(line)
    ]
    section_starts = [0] if not heading_starts or heading_starts[0] != 0 else []
    section_starts.extend(heading_starts)
    sections = list(zip(section_starts, section_starts[1:] + [len(text)], strict=True))
    word_counts = [len(_WORD.findall(text[start:end])) for start, end in sections]

    def make_chunk(start: int, end: int) -> Chunk:
        content = text[start:end]
        return Chunk(
            text=content,
            source_path=source_path,
            start_line=bisect_right(line_starts, start),
            end_line=bisect_right(line_starts, end - 1),
            token_count=len(content.split()),
        )

    chunks = []
    index = 0
    while index < len(sections):
        start, end = sections[index]
        count = word_counts[index]

        if count > 500:
            section_words = list(_WORD.finditer(text[start:end]))
            number_of_chunks = (count - 50 + 449) // 450
            total_window_words = count + 50 * (number_of_chunks - 1)
            window_size, larger_windows = divmod(total_window_words, number_of_chunks)
            word_start = 0
            for window_index in range(number_of_chunks):
                size = window_size + (window_index < larger_windows)
                word_end = word_start + size
                chunks.append(
                    make_chunk(
                        start + section_words[word_start].start(),
                        start + section_words[word_end - 1].end(),
                    )
                )
                word_start = word_end - 50
            index += 1
            continue

        while count < 300 and index + 1 < len(sections):
            # One-topic mode: only the preamble (no subheading yet) merges forward.
            if not merge_short and _SUBHEADING.search(text[start:end]):
                break
            next_count = word_counts[index + 1]
            if count + next_count > 500:
                break
            index += 1
            end = sections[index][1]
            count += next_count

        chunks.append(make_chunk(start, end))
        index += 1

    return chunks
