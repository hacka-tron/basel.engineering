import pytest

from services.glassbox.ingest.chunkers.base import Chunk
from services.glassbox.ingest.chunkers.yaml_doc import chunk_yaml


def test_documents_split_only_on_exact_separator_lines():
    text = "kind: ConfigMap\nvalue: ---\n---\nkind: Service\n ---\nport: 80\n"

    chunks = chunk_yaml(text, "manifest.yml")

    assert len(chunks) == 2
    assert all(isinstance(chunk, Chunk) for chunk in chunks)
    assert [(chunk.start_line, chunk.end_line) for chunk in chunks] == [(1, 2), (4, 6)]
    assert [chunk.text for chunk in chunks] == [
        "kind: ConfigMap\nvalue: ---\n",
        "kind: Service\n ---\nport: 80\n",
    ]
    assert all(chunk.source_path == "manifest.yml" for chunk in chunks)
    assert all(chunk.token_count == len(chunk.text.split()) for chunk in chunks)


def test_leading_and_trailing_separators_do_not_create_empty_chunks():
    chunks = chunk_yaml("---\nkind: Pod\n---\n", "pod.yaml")

    assert len(chunks) == 1
    assert chunks[0].text == "kind: Pod\n"
    assert (chunks[0].start_line, chunks[0].end_line) == (2, 2)


def test_separator_allows_trailing_horizontal_whitespace():
    chunks = chunk_yaml("kind: Pod\n--- \t\nkind: Service\n", "manifest.yaml")

    assert [chunk.text for chunk in chunks] == ["kind: Pod\n", "kind: Service\n"]
    assert [(chunk.start_line, chunk.end_line) for chunk in chunks] == [(1, 1), (3, 3)]


def test_file_without_separator_is_one_document():
    chunks = chunk_yaml("kind: Pod\nmetadata: {}", "pod.yaml")

    assert len(chunks) == 1
    assert (chunks[0].start_line, chunks[0].end_line) == (1, 2)


@pytest.mark.parametrize("text", ["", " \n\t\n"])
def test_empty_or_whitespace_input_returns_no_chunks(text):
    assert chunk_yaml(text, "empty.yaml") == []
