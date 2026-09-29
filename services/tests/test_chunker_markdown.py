from services.glassbox.ingest.chunkers.base import Chunk
from services.glassbox.ingest.chunkers.markdown import chunk_markdown


def words(prefix: str, count: int) -> str:
    return " ".join(f"{prefix}{i}" for i in range(count))


def test_multi_heading_sections_chunk_cleanly():
    text = f"# First\n{words('a', 319)}\n## Second\n{words('b', 319)}\n"

    chunks = chunk_markdown(text, "notes.md")

    assert len(chunks) == 2
    assert all(isinstance(chunk, Chunk) for chunk in chunks)
    assert [chunk.token_count for chunk in chunks] == [321, 321]
    assert [chunk.source_path for chunk in chunks] == ["notes.md", "notes.md"]
    assert chunks[0].text.startswith("# First\n")
    assert chunks[1].text.startswith("## Second\n")


def test_short_sections_merge_forward_including_consecutive_headings():
    text = f"## A\n## B\n{words('b', 299)}\n## C\n{words('c', 319)}"

    chunks = chunk_markdown(text, "doc.md")

    assert len(chunks) == 2
    assert chunks[0].text.startswith("## A\n## B\n")
    assert chunks[0].token_count == 303
    assert chunks[1].text.startswith("## C\n")


def test_short_section_does_not_merge_past_500_words():
    text = f"# Small\n{words('a', 100)}\n# Large\n{words('b', 450)}"

    chunks = chunk_markdown(text, "doc.md")

    assert len(chunks) == 2
    assert [chunk.token_count for chunk in chunks] == [102, 452]


def test_long_section_splits_with_50_word_overlap():
    text = "# Long\n" + words("w", 999)

    chunks = chunk_markdown(text, "long.md")

    assert len(chunks) == 3
    assert all(300 <= chunk.token_count <= 500 for chunk in chunks)
    assert all(chunk.token_count == len(chunk.text.split()) for chunk in chunks)
    assert chunks[0].text.split()[-50:] == chunks[1].text.split()[:50]
    assert chunks[1].text.split()[-50:] == chunks[2].text.split()[:50]
    assert chunks[0].text.split()[0:2] == ["#", "Long"]
    assert chunks[-1].text.split()[-1] == "w998"


def test_no_headings_long_single_line_is_split():
    text = words("plain", 650)

    chunks = chunk_markdown(text, "plain.md")

    assert len(chunks) == 2
    assert all(1 <= chunk.token_count <= 500 for chunk in chunks)
    assert chunks[0].text.split()[-50:] == chunks[1].text.split()[:50]
    assert all((chunk.start_line, chunk.end_line) == (1, 1) for chunk in chunks)


def test_empty_input_returns_no_chunks():
    assert chunk_markdown("", "empty.md") == []


def test_line_ranges_track_original_multi_section_lines():
    text = "intro\n\n# One\nalpha\nbeta\n## Two\ngamma\n"

    chunks = chunk_markdown(text, "path.md")

    assert len(chunks) == 1
    assert (chunks[0].start_line, chunks[0].end_line) == (1, 7)
    assert chunks[0].text == text
    assert chunks[0].token_count == len(text.split())


def test_line_ranges_for_separate_sections():
    text = f"# One\n{words('a', 319)}\n\n## Two\n{words('b', 319)}\n"

    chunks = chunk_markdown(text, "path.md")

    assert [(chunk.start_line, chunk.end_line) for chunk in chunks] == [(1, 3), (4, 5)]
