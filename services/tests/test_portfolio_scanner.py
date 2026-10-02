"""corpus/portfolio as the portfolio corpus: drafts, hidden files and symlinks are skipped."""

import os

from services.glassbox.ingest import run as ingest_run
from services.glassbox.ingest.scanner import scan_file, scan_sources
from services.glassbox.ingest.sweep import ScopedDocument, plan_sweep, source_root

MODEL = "fake-v1"
PROJECT = (
    "---\ntitle: JobPilot\none_liner: Copilot.\nkind: personal\nyear: 2026\n"
    "stack: [React]\n{extra}---\n\nBody.\n"
)
FAKE_KEY = "key = AKIA1234567890ABCDEF\n"  # pragma: allowlist secret
FAKE_KEY_PROJECT = PROJECT.format(extra="") + FAKE_KEY


def _tree(root, files):
    for relative, text in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)


def _portfolio(root):
    return {s.source_path for s in scan_sources(root) if s.corpus == "portfolio"}


def test_published_projects_are_portfolio_sources_and_drafts_are_not(tmp_path):
    _tree(
        tmp_path,
        {
            "corpus/portfolio/jobpilot.md": PROJECT.format(extra=""),
            "corpus/portfolio/shop.md": PROJECT.format(extra="draft: false\n"),
            "corpus/portfolio/_example.md": PROJECT.format(extra="draft: true\n"),
            "corpus/portfolio/.wip.md": PROJECT.format(extra=""),
            "corpus/portfolio/.drafts/old.md": PROJECT.format(extra=""),
            "corpus/portfolio/notes.txt": "not markdown",
        },
    )
    assert _portfolio(tmp_path) == {"corpus/portfolio/jobpilot.md", "corpus/portfolio/shop.md"}


def test_a_file_with_broken_front_matter_is_still_scanned_so_ingest_reports_it(tmp_path):
    _tree(tmp_path, {"corpus/portfolio/broken.md": "---\ntitle: [unclosed\n---\nBody\n"})
    assert _portfolio(tmp_path) == {"corpus/portfolio/broken.md"}


def test_symlinked_files_and_folders_are_not_followed(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "leak.md").write_text(PROJECT.format(extra=""))
    base = tmp_path / "corpus" / "portfolio"
    base.mkdir(parents=True)
    os.symlink(outside / "leak.md", base / "link.md")
    os.symlink(outside, base / "linked-folder")
    assert _portfolio(tmp_path) == set()


def test_portfolio_files_belong_to_no_other_corpus(tmp_path):
    _tree(tmp_path, {"corpus/portfolio/jobpilot.md": PROJECT.format(extra="")})
    sources = list(scan_sources(tmp_path))
    assert [(s.corpus, s.source_path) for s in sources] == [
        ("portfolio", "corpus/portfolio/jobpilot.md")
    ]
    assert scan_file(sources[0]).error is None


def test_secrets_in_a_project_file_are_quarantined_like_any_source(tmp_path):
    _tree(tmp_path, {"corpus/portfolio/keys.md": FAKE_KEY_PROJECT})
    (source,) = list(scan_sources(tmp_path))
    assert scan_file(source).error.startswith("possible AWS access key")


def test_seen_paths_include_portfolio(tmp_path):
    _tree(tmp_path, {"corpus/portfolio/jobpilot.md": PROJECT.format(extra="")})
    assert ingest_run.seen_source_paths(tmp_path)["portfolio"] == {"corpus/portfolio/jobpilot.md"}
    assert source_root("corpus/portfolio/jobpilot.md") == "corpus"


def test_a_project_switched_to_draft_becomes_stale(tmp_path):
    # Review Focus 1: the sweep sees it as gone (report mode in production logs it).
    _tree(
        tmp_path,
        {
            "corpus/portfolio/jobpilot.md": PROJECT.format(extra="draft: true\n"),
            "corpus/portfolio/shop.md": PROJECT.format(extra=""),
        },
    )
    known = [
        ScopedDocument(1, "corpus/portfolio/jobpilot.md", (1,)),
        ScopedDocument(2, "corpus/portfolio/shop.md", (2,)),
    ]
    seen = ingest_run.seen_source_paths(tmp_path)["portfolio"]
    plan = plan_sweep("portfolio", MODEL, known, seen)
    assert [document.source_path for document in plan.stale] == ["corpus/portfolio/jobpilot.md"]
    assert plan.refused is None


def test_drafting_the_last_project_is_refused_by_the_zero_file_guard(tmp_path):
    _tree(tmp_path, {"corpus/portfolio/jobpilot.md": PROJECT.format(extra="draft: true\n")})
    known = [ScopedDocument(1, "corpus/portfolio/jobpilot.md", (1,))]
    seen = ingest_run.seen_source_paths(tmp_path)["portfolio"]
    assert seen == set()
    plan = plan_sweep("portfolio", MODEL, known, seen, force=True)
    assert plan.refused and "zero files for portfolio" in plan.refused
