"""The portfolio file format (portfolio spec §6.1) and the CI check over the repo."""

from pathlib import Path

import pytest
import yaml

from services.glassbox import portfolio
from services.glassbox.portfolio import (
    PortfolioError,
    index_content_hash,
    index_text,
    is_draft,
    parse_project,
    validate_tree,
)

REPO = Path(__file__).resolve().parents[2]

VALID = """---
title: JobPilot
one_liner: Job-search copilot that tailors applications and tracks every lead.
kind: personal
year: 2026
order: 1
stack: [TypeScript, React, FastAPI, Postgres]
links:
  live: https://jobpilot.example.com
  code: https://github.com/hacka-tron/jobpilot
visuals:
  - src: jobpilot/board.png
    alt: Pipeline board with applications grouped by stage
    caption: Pipeline board, from saved to offer.
    aspect: 16/10
---

## The problem

Tailoring each application took an hour.
"""

BASE = {
    "title": "JobPilot",
    "one_liner": "Job-search copilot.",
    "kind": "personal",
    "year": 2026,
    "stack": ["React"],
}


def doc(fields: dict, body: str = "Body.\n") -> str:
    return "---\n" + yaml.safe_dump(fields, sort_keys=False) + "---\n\n" + body


def errors_for(text: str, slug: str = "jobpilot") -> str:
    with pytest.raises(PortfolioError) as caught:
        parse_project(text, slug)
    return str(caught.value)


# --- the repo's own files: this is the CI schema check -------------------------------


def test_every_portfolio_file_in_the_repo_is_valid():
    assert validate_tree(REPO) == {}


def test_the_example_is_a_draft_that_documents_every_field():
    text = (REPO / "corpus/portfolio/_example.md").read_text()
    assert is_draft(text)
    project = parse_project(text, "_example")
    assert project.draft is True
    for name in (*portfolio.REQUIRED_FIELDS, *portfolio.OPTIONAL_FIELDS, "src", "alt", "aspect"):
        assert name in text, name


def test_pyyaml_is_a_runtime_dependency():
    # The ingest Job parses front matter inside the image, which installs only this file.
    runtime = (REPO / "services/requirements.txt").read_text().splitlines()
    assert any(line.startswith("PyYAML==") for line in runtime)


# --- parsing ----------------------------------------------------------------------


def test_a_full_project_parses():
    project = parse_project(VALID, "jobpilot")
    assert project.title == "JobPilot"
    assert project.kind == "personal" and project.year == 2026 and project.order == 1
    assert project.stack == ("TypeScript", "React", "FastAPI", "Postgres")
    assert project.links == {
        "live": "https://jobpilot.example.com",
        "code": "https://github.com/hacka-tron/jobpilot",
    }
    assert project.visuals == (
        portfolio.Visual(
            src="jobpilot/board.png",
            alt="Pipeline board with applications grouped by stage",
            aspect="16/10",
            caption="Pipeline board, from saved to offer.",
        ),
    )
    assert project.draft is False
    assert project.body.lstrip().startswith("## The problem")


def test_optional_fields_default():
    project = parse_project(doc(BASE), "jobpilot")
    assert project.order is None and project.links == {} and project.visuals == ()
    assert project.draft is False


@pytest.mark.parametrize("name", ["title", "one_liner", "kind", "year", "stack"])
def test_each_required_field_is_required(name):
    fields = {key: value for key, value in BASE.items() if key != name}
    assert f"missing required field '{name}'" in errors_for(doc(fields))


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"kind": "side-project"}, "kind must be one of personal, freelance"),
        ({"links": {"live": "http://jobpilot.example.com"}}, "links.live must be an https://"),
        ({"links": {"live": "https://"}}, "links.live must be an https://"),
        ({"links": {"demo": "https://x.example.com"}}, "unknown link 'demo'"),
        ({"subtitle": "typo"}, "unknown fields: subtitle"),
        ({"visuals": [{"src": "jobpilot/a.png", "aspect": "16/10"}]}, "visuals[0].alt is missing"),
        (
            {"visuals": [{"src": "jobpilot/a.png", "alt": "A", "aspect": "21/9"}]},
            "visuals[0].aspect must be one of 16/10, 4/3, 9/19.5",
        ),
        (
            {"visuals": [{"src": "../secrets.png", "alt": "A", "aspect": "4/3"}]},
            "visuals[0].src must be a path like jobpilot/picture.png",
        ),
        (
            {"visuals": [{"src": "other/a.png", "alt": "A", "aspect": "4/3"}]},
            "visuals[0].src must be a path like jobpilot/picture.png",
        ),
        (
            {"visuals": [{"src": "/jobpilot/a.png", "alt": "A", "aspect": "4/3"}]},
            "visuals[0].src must be a path like jobpilot/picture.png",
        ),
        ({"year": 1850}, "year must be a whole number like 2026"),
        ({"order": "first"}, "order must be a whole number"),
    ],
)
def test_schema_errors(change, message):
    assert message in errors_for(doc({**BASE, **change}))


def test_yaml_type_gotchas_are_errors():
    # Review Focus 2: hand-written YAML that parses, but not as the owner meant.
    text = (
        '---\ntitle: 2048\none_liner: Puzzle.\nkind: personal\nyear: "2026"\n'
        "stack: React\nvisuals:\n  - src: jobpilot/a.png\n    alt: A\n    aspect: 16:10\n"
        'draft: "false"\n---\nBody.\n'
    )
    message = errors_for(text)
    assert "title must be text" in message
    assert "year must be a whole number like 2026 (got '2026')" in message
    assert "stack must be a list like [TypeScript, React]" in message
    assert "visuals[0].aspect must be one of 16/10, 4/3, 9/19.5 (got 970)" in message
    assert "draft must be true or false (got 'false')" in message


RAW = (
    "---\ntitle: JobPilot\none_liner: Copilot.\nkind: personal\nyear: 2026\n"
    "stack: [React]\n{extra}---\n\nBody.\n"
)


@pytest.mark.parametrize("value", ["yes", "on", "On", "no", "off", "True", "FALSE"])
def test_only_literal_true_and_false_are_booleans(value):
    # YAML 1.1 (PyYAML's default) reads yes/on/On as true; the frontend's YAML 1.2
    # parser reads them as text. Both must agree, so only true/false are booleans.
    text = RAW.format(extra=f"draft: {value}\n")
    assert f"draft must be true or false (got '{value}')" in errors_for(text)
    assert not is_draft(text)


def test_literal_true_and_false_still_work():
    assert parse_project(RAW.format(extra="draft: true\n"), "jobpilot").draft is True
    assert parse_project(RAW.format(extra="draft: false\n"), "jobpilot").draft is False


@pytest.mark.parametrize(
    ("extra", "name"),
    [
        ("draft: true\ndraft: false\n", "draft"),
        ("links:\n  live: https://a.example.com\n  live: https://b.example.com\n", "live"),
    ],
)
def test_a_repeated_field_is_an_error_not_last_one_wins(extra, name):
    text = RAW.format(extra=extra)
    assert f"front matter has the field '{name}' more than once" in errors_for(text)
    assert not is_draft(text)


def test_an_unquoted_aspect_gets_a_quoting_hint():
    visual = "visuals:\n  - src: jobpilot/a.png\n    alt: A\n    aspect: {aspect}\n"
    message = errors_for(RAW.format(extra=visual.format(aspect="16:10")))
    assert 'got 970); put it in quotes, like aspect: "16/10"' in message
    message = errors_for(RAW.format(extra=visual.format(aspect="21/9")))
    assert "(got '21/9')" in message and "put it in quotes" not in message


def test_every_problem_is_listed_at_once():
    message = errors_for(doc({"title": "Only a title"}))
    assert message.count("missing required field") == 4


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("# No front matter\n", "missing front matter"),
        ("---\ntitle: Unclosed\n", "front matter is not closed"),
        ("---\ntitle: [broken\n---\nBody\n", "front matter is not valid YAML"),
        ("---\n- a list\n---\nBody\n", "front matter must be a list of 'field: value' lines"),
    ],
)
def test_broken_front_matter(text, message):
    assert message in errors_for(text)


@pytest.mark.parametrize("slug", ["JobPilot", "job_pilot", "job pilot", "-jobpilot"])
def test_file_names_are_lowercase_slugs(slug):
    assert f"file name {slug}.md must be" in errors_for(doc(BASE), slug)


def test_draft_detection_never_raises():
    assert is_draft(doc({**BASE, "draft": True}))
    assert not is_draft(doc(BASE))
    assert not is_draft(doc({**BASE, "draft": "true"}))  # a string is not a draft
    assert not is_draft("---\ntitle: [broken\n---\n")
    assert not is_draft("no front matter")


# --- what the chatbot indexes -----------------------------------------------------


def test_index_text_is_a_preface_then_the_body():
    text = index_text(parse_project(VALID, "jobpilot"))
    assert text == (
        "# JobPilot\n\n"
        "Job-search copilot that tailors applications and tracks every lead.\n\n"
        "- Kind: Personal project\n"
        "- Year: 2026\n"
        "- Stack: TypeScript, React, FastAPI, Postgres\n"
        "- Live site: https://jobpilot.example.com\n"
        "- Code: https://github.com/hacka-tron/jobpilot\n\n"
        "## The problem\n\nTailoring each application took an hour.\n"
    )


def test_index_text_without_links_or_body():
    text = index_text(parse_project(doc({**BASE, "kind": "freelance"}, body=""), "jobpilot"))
    assert text.endswith("- Kind: Freelance project\n- Year: 2026\n- Stack: React\n")


def test_index_content_hash_folds_in_the_format_version():
    raw = "a" * 64
    assert index_content_hash(raw) != raw
    assert index_content_hash(raw) == index_content_hash(raw)
    assert len(index_content_hash(raw)) == 64


# --- the tree check ---------------------------------------------------------------


def _tree(root: Path, files: dict[str, str]) -> None:
    for relative, text in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)


def test_missing_screenshot_fails_published_projects_only(tmp_path):
    visual = {"visuals": [{"src": "jobpilot/board.png", "alt": "Board", "aspect": "16/10"}]}
    _tree(
        tmp_path,
        {
            "corpus/portfolio/jobpilot.md": doc({**BASE, **visual}),
            "corpus/portfolio/wip.md": doc(
                {
                    **BASE,
                    "draft": True,
                    "visuals": [{"src": "wip/a.png", "alt": "A", "aspect": "4/3"}],
                }
            ),
        },
    )
    problems = validate_tree(tmp_path)
    assert problems == {
        "corpus/portfolio/jobpilot.md": [
            "visual jobpilot/board.png is not in frontend/public/portfolio/"
        ]
    }
    _tree(tmp_path, {"frontend/public/portfolio/jobpilot/board.png": "png bytes"})
    assert validate_tree(tmp_path) == {}


def test_symlinked_screenshots_are_rejected(tmp_path):
    # Same rule as the scanner: symlinks are never read, files or folders.
    visual = {
        "visuals": [
            {"src": "jobpilot/linked.png", "alt": "A", "aspect": "16/10"},
            {"src": "linkdir/b.png", "alt": "B", "aspect": "16/10"},
        ]
    }
    _tree(tmp_path, {"outside/b.png": "png", "outside/linked.png": "png"})
    # The second visual's folder must start with the slug, so use a second project.
    _tree(
        tmp_path,
        {
            "corpus/portfolio/jobpilot.md": doc({**BASE, "visuals": visual["visuals"][:1]}),
            "corpus/portfolio/linkdir.md": doc({**BASE, "visuals": visual["visuals"][1:]}),
        },
    )
    public = tmp_path / "frontend/public/portfolio"
    (public / "jobpilot").mkdir(parents=True)
    (public / "jobpilot/linked.png").symlink_to(tmp_path / "outside/linked.png")
    (public / "linkdir").symlink_to(tmp_path / "outside")
    assert validate_tree(tmp_path) == {
        "corpus/portfolio/jobpilot.md": [
            "visual jobpilot/linked.png is a symlink; commit the image itself "
            "(symlinks are never read)"
        ],
        "corpus/portfolio/linkdir.md": [
            "visual linkdir/b.png is a symlink; commit the image itself (symlinks are never read)"
        ],
    }


def test_personal_data_in_a_write_up_fails_the_check(tmp_path):
    body = "The client, Jane (jane.doe@clientco.example, 614-555-0100), signed off.\n"
    _tree(tmp_path, {"corpus/portfolio/clientco.md": doc(BASE, body=body)})
    problems = validate_tree(tmp_path)["corpus/portfolio/clientco.md"]
    assert sorted(problems) == [
        "possible personal data (email) at line 10",
        "possible personal data (phone) at line 10",
    ]
    assert not any("jane.doe" in problem or "555" in problem for problem in problems)


def test_subfolders_and_invalid_files_are_reported_hidden_files_ignored(tmp_path):
    _tree(
        tmp_path,
        {
            "corpus/portfolio/clients/acme.md": doc(BASE),
            "corpus/portfolio/broken.md": "# no front matter\n",
            "corpus/portfolio/.scratch.md": "# ignored\n",
        },
    )
    problems = validate_tree(tmp_path)
    assert set(problems) == {"corpus/portfolio/clients/acme.md", "corpus/portfolio/broken.md"}
    assert "not in a subfolder" in problems["corpus/portfolio/clients/acme.md"][0]


def test_no_portfolio_folder_is_valid(tmp_path):
    assert validate_tree(tmp_path) == {}


def test_cli_exit_codes(tmp_path, capsys):
    _tree(tmp_path, {"corpus/portfolio/jobpilot.md": doc(BASE)})
    assert portfolio.main(["--root", str(tmp_path)]) == 0
    assert "1 portfolio files OK" in capsys.readouterr().out
    _tree(tmp_path, {"corpus/portfolio/broken.md": "# no front matter\n"})
    assert portfolio.main(["--root", str(tmp_path)]) == 1
    assert "corpus/portfolio/broken.md: missing front matter" in capsys.readouterr().out
