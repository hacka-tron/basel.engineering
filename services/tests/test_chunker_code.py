from pathlib import Path

import pytest

from services.glassbox.ingest.chunkers.base import Chunk
from services.glassbox.ingest.chunkers.code import chunk_code


def test_python_top_level_definitions_and_leading_imports():
    text = (
        "import os\n\n"
        "def first():\n    def nested():\n        pass\n    return nested()\n\n"
        "class Thing:\n    def method(self):\n        pass\n\n"
        "async def last():\n    return 1\n"
    )

    chunks = chunk_code(text, "module.py")

    assert len(chunks) == 4
    assert all(isinstance(chunk, Chunk) for chunk in chunks)
    assert [(chunk.start_line, chunk.end_line) for chunk in chunks] == [
        (1, 2),
        (3, 7),
        (8, 11),
        (12, 13),
    ]
    assert chunks[0].text == "import os\n\n"
    assert chunks[1].text.startswith("def first():\n")
    assert chunks[2].text.startswith("class Thing:\n")
    assert chunks[3].text.startswith("async def last():\n")
    assert all(chunk.source_path == "module.py" for chunk in chunks)
    assert all(chunk.token_count == len(chunk.text.split()) for chunk in chunks)


def test_typescript_declaration_forms_and_nested_methods():
    text = (
        "// module comment\nimport { x } from './x';\n"
        "function plain() {\n}\n"
        "class Local {\n  method() {}\n}\n"
        "export function named() {\n}\n"
        "export class Exported {\n}\n"
        "export const arrow = (x: number) => {\n  return x;\n};\n"
        "export async function remote() {\n}\n"
    )

    chunks = chunk_code(text, "module.ts")

    assert len(chunks) == 7
    assert [(chunk.start_line, chunk.end_line) for chunk in chunks] == [
        (1, 2),
        (3, 4),
        (5, 7),
        (8, 9),
        (10, 11),
        (12, 14),
        (15, 16),
    ]
    assert chunks[5].text.startswith("export const arrow =")


def test_typescript_default_exports_and_arrow_declarations():
    text = (
        "export default function main() {}\n"
        "export default class App {}\n"
        "const helper = () => {\n  return 1;\n};\n"
        "export const multi = (\n  value: number,\n) => {\n  return value;\n};\n"
    )

    chunks = chunk_code(text, "module.ts")

    assert len(chunks) == 4
    assert [chunk.start_line for chunk in chunks] == [1, 2, 3, 6]
    assert chunks[2].text.startswith("const helper =")
    assert chunks[3].text.startswith("export const multi = (\n")


def test_stacked_decorators_start_with_decorated_definition():
    text = "def first():\n    pass\n@a\n@b\ndef second():\n    pass\n"

    chunks = chunk_code(text, "module.py")

    assert len(chunks) == 2
    assert chunks[0].text == "def first():\n    pass\n"
    assert chunks[1].text.startswith("@a\n@b\ndef second():\n")
    assert (chunks[1].start_line, chunks[1].end_line) == (3, 6)


def test_real_api_endpoints_include_their_decorators():
    path = Path(__file__).resolve().parents[1] / "glassbox/api/main.py"
    chunks = chunk_code(path.read_text(), str(path))

    assert len(chunks) == 5
    assert chunks[1].text.startswith("@asynccontextmanager\nasync def lifespan(")
    assert chunks[2].text.startswith('@app.get("/healthz")\nasync def healthz(')
    assert chunks[3].text.startswith('@app.get("/api/version")\nasync def version(')
    assert chunks[4].text.startswith('@app.get("/readyz")\nasync def readyz(')


def test_comment_only_preamble_is_skipped():
    chunks = chunk_code("# heading\n\n# more\ndef work():\n    pass\n", "work.py")

    assert len(chunks) == 1
    assert (chunks[0].start_line, chunks[0].end_line) == (4, 5)


def test_comment_only_file_has_no_chunks():
    assert chunk_code("# module comment\n\n# another\n", "comments.py") == []


def test_real_markdown_chunker_has_one_chunk_per_top_level_function():
    path = Path(__file__).resolve().parents[1] / "glassbox/ingest/chunkers/markdown.py"
    text = path.read_text()

    chunks = chunk_code(text, str(path))

    assert len(chunks) == 2  # imports and the file's one top-level function
    assert chunks[0].text.startswith('"""Split Markdown')
    assert chunks[1].text.startswith("def chunk_markdown(")
    assert (chunks[1].start_line, chunks[1].end_line) == (
        next(
            i
            for i, line in enumerate(text.splitlines(), 1)
            if line.startswith("def chunk_markdown(")
        ),
        len(text.splitlines()),
    )


@pytest.mark.parametrize("text", ["", " \n\t\n"])
def test_empty_or_whitespace_input_returns_no_chunks(text):
    assert chunk_code(text, "empty.py") == []
