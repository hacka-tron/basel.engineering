"""Prompt v19: the "Sources: ..." line post-filter (services/glassbox/sources_line.py)."""

import pytest

from services.glassbox.privacy import StreamMasker
from services.glassbox.sources_line import SourcesLineFilter, is_sources_line, strip_sources_lines

ANSWER = "Pressing it runs a short load burst, then a 5-minute cooldown."


def stream(parts: list[str]) -> tuple[str, SourcesLineFilter]:
    lines = SourcesLineFilter()
    out = "".join(lines.push(part) for part in parts) + lines.flush()
    return out, lines


@pytest.mark.parametrize(
    "line",
    [
        "Sources: 1, 4, 5, 9",
        "sources: 2",
        "Source: 3",
        "**Sources:** 1, 4",
        "**Sources**: 1",
        "- Sources: 1",
        "(Sources: 1, 2)",
        "  Sources : 1",
        "### Sources: 1",
    ],
)
def test_sources_labels_are_recognised(line):
    assert is_sources_line(line)


@pytest.mark.parametrize(
    "line",
    [
        "Source code lives in the repo.",
        "Sources of truth differ.",
        "Sure: here it is.",
        "Resources: 2 GiB",
        "Open sources: none",
    ],
)
def test_other_lines_are_kept(line):
    assert not is_sources_line(line)
    assert strip_sources_lines(line) == line


def test_trailing_sources_line_is_dropped_with_its_blank_lines():
    assert strip_sources_lines(f"{ANSWER}\n\nSources: 1, 4, 5, 9") == ANSWER
    assert strip_sources_lines(f"{ANSWER}\nSources: 1, 4, 5, 9\n") == ANSWER


def test_a_sources_line_in_the_middle_keeps_the_text_after_it():
    text = "First point.\nSources: 1\nSecond point."
    assert strip_sources_lines(text) == "First point.\nSecond point."


def test_label_split_across_chunks_is_dropped():
    parts = [ANSWER, "\n", "\n", "Sour", "ces", ": 1", ", 4, 5", ", 9"]
    out, lines = stream(parts)
    assert out == ANSWER
    assert lines.dropped == 1


def test_label_split_mid_word_with_markdown_is_dropped():
    out, _ = stream([ANSWER + "\n\n*", "*S", "ource", "s:*", "* 1, 4"])
    assert out == ANSWER


def test_text_streams_without_delay_when_it_cannot_be_a_label():
    lines = SourcesLineFilter()
    assert lines.push("Pressing it ") == "Pressing it "
    assert lines.push("runs a burst.") == "runs a burst."
    # A line start that could still be "Sources:" waits for one more token ...
    assert lines.push("\nSo") == ""
    # ... and goes out as soon as it can't be.
    assert lines.push("me more.") == "\nSome more."
    assert lines.flush() == ""


def test_held_line_start_is_released_at_the_end():
    out, lines = stream(["Done.", "\n", "Sou"])
    assert out == "Done.\nSou"
    assert lines.dropped == 0


def test_trailing_blank_lines_alone_are_dropped_at_the_end():
    out, _ = stream([ANSWER, "\n\n"])
    assert out == ANSWER


def test_every_split_point_gives_the_same_result():
    text = "Line one.\nSo it works.\n\nSources: 1, 4\nTail [x]."
    expected = "Line one.\nSo it works.\nTail [x]."
    for cut in range(1, len(text)):
        out, _ = stream([text[:cut], text[cut:]])
        assert out == expected, cut
    out, _ = stream(list(text))  # one character per chunk
    assert out == expected


def test_runs_in_front_of_the_masker_like_the_api():
    lines, masker = SourcesLineFilter(), StreamMasker()
    sent = []
    for part in ["Call me at 614", " 555", "-0100.", "\n\nSources", ": 1, 4"]:
        safe = masker.push(lines.push(part))
        if safe:
            sent.append(safe)
    sent.append(masker.push(lines.flush()) + masker.flush())
    text = "".join(sent)
    assert "Sources" not in text
    assert "555" not in text
    assert text.startswith("Call me at ")


def test_lines_inside_a_fenced_code_block_are_kept():
    text = "Here is the config:\n```yaml\nsources:\n  - docs\n```\nSources: 1, 2"
    expected = "Here is the config:\n```yaml\nsources:\n  - docs\n```"
    assert strip_sources_lines(text) == expected
    for cut in range(1, len(text)):
        out, _ = stream([text[:cut], text[cut:]])
        assert out == expected, cut
    out, _ = stream(list(text))
    assert out == expected


def test_an_answer_is_never_emptied():
    only = "Source: the retrieval worker reads the stream."
    out, lines = stream([only[:8], only[8:]])
    assert out == only
    assert lines.dropped == 0
    assert strip_sources_lines("\n\nSources: 1, 4") == "Sources: 1, 4"
