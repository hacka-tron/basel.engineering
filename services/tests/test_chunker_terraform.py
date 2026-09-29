import pytest

from services.glassbox.ingest.chunkers.base import Chunk
from services.glassbox.ingest.chunkers.terraform import chunk_terraform


def test_top_level_blocks_ignore_nested_braces_and_inter_block_content():
    text = (
        '# file comment\n\nresource "aws_instance" "node" {\n'
        '  tags = {\n    Name = "node"\n  }\n}\n\n'
        '# between blocks\nmodule "network" {\n  source = "./network"\n}\n'
    )

    chunks = chunk_terraform(text, "infra/main.tf")

    assert len(chunks) == 2
    assert all(isinstance(chunk, Chunk) for chunk in chunks)
    assert [(chunk.start_line, chunk.end_line) for chunk in chunks] == [(3, 7), (10, 12)]
    assert chunks[0].text == "".join(text.splitlines(keepends=True)[2:7])
    assert chunks[1].text == "".join(text.splitlines(keepends=True)[9:12])
    assert all(chunk.source_path == "infra/main.tf" for chunk in chunks)
    assert all(chunk.token_count == len(chunk.text.split()) for chunk in chunks)


def test_each_supported_block_keyword_starts_a_chunk():
    keywords = (
        "resource",
        "module",
        "variable",
        "data",
        "output",
        "provider",
        "locals",
        "terraform",
    )
    text = "".join(f'{keyword} "name" {{\n}}\n' for keyword in keywords)

    chunks = chunk_terraform(text, "main.tf")

    assert len(chunks) == len(keywords)
    assert [(chunk.start_line, chunk.end_line) for chunk in chunks] == [
        (index * 2 + 1, index * 2 + 2) for index in range(len(keywords))
    ]


def test_single_line_block_ends_on_opening_line():
    chunks = chunk_terraform('locals {}\noutput "name" {\n  value = "x"\n}\n', "main.tf")

    assert [(chunk.start_line, chunk.end_line) for chunk in chunks] == [(1, 1), (2, 4)]


def test_inline_nested_map_does_not_end_multiline_block():
    text = 'resource "aws_s3_bucket" "b" {\n  tags = { Name = "test" }\n  bucket = "my-bucket"\n}\n'

    chunks = chunk_terraform(text, "main.tf")

    assert len(chunks) == 1
    assert chunks[0].text == text
    assert (chunks[0].start_line, chunks[0].end_line) == (1, 4)


def test_inline_map_on_opening_line_does_not_end_block():
    text = 'locals { default_tags = {}\n  owner = "team"\n}\n'

    chunks = chunk_terraform(text, "main.tf")

    assert len(chunks) == 1
    assert chunks[0].text == text
    assert (chunks[0].start_line, chunks[0].end_line) == (1, 3)


def test_heredoc_json_braces_do_not_end_block():
    text = 'locals {\n  policy = <<-EOF\n{\n  "enabled": true\n}\nEOF\n  owner = "team"\n}\n'

    chunks = chunk_terraform(text, "main.tf")

    assert len(chunks) == 1
    assert chunks[0].text == text
    assert (chunks[0].start_line, chunks[0].end_line) == (1, 8)


def test_block_opening_brace_on_next_line():
    text = 'resource "aws_instance" "node"\n{\n  ami = "ami-123"\n}\n'

    chunks = chunk_terraform(text, "main.tf")

    assert len(chunks) == 1
    assert chunks[0].text == text
    assert (chunks[0].start_line, chunks[0].end_line) == (1, 4)


@pytest.mark.parametrize("text", ["", "  \n\t\n"])
def test_empty_or_whitespace_input_returns_no_chunks(text):
    assert chunk_terraform(text, "empty.tf") == []
