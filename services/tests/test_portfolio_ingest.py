"""What ingest embeds for each corpus: portfolio preface, front matter, the guard, the hash."""

from pathlib import Path

from services.glassbox.ingest.run import content_hash_for, prepare_content
from services.glassbox.ingest.scanner import SourceFile
from services.glassbox.portfolio import index_content_hash
from services.glassbox.privacy import GUARDED_CORPORA, REDACTION, guarded_content_hash

PROJECT = """---
title: ClientCo booking site
one_liner: Booking and payments for a small studio.
kind: freelance
year: 2025
stack: [Next.js, Stripe, Postgres]
links:
  live: https://booking.example.com
---

## What I built

Online booking with deposits. The owner, jane.doe@clientco.example, signs off releases.
"""


def _source(corpus: str, source_path: str) -> SourceFile:
    return SourceFile(corpus, source_path, Path(source_path))


def test_portfolio_and_about_me_are_the_guarded_corpora():
    assert GUARDED_CORPORA == frozenset({"about_me", "portfolio"})


def test_portfolio_documents_are_indexed_with_a_preface_and_guarded():
    prepared = prepare_content(
        _source("portfolio", "corpus/portfolio/clientco.md"), PROJECT, frozenset()
    )
    assert prepared.error is None
    assert prepared.text.startswith(
        "# ClientCo booking site\n\nBooking and payments for a small studio.\n\n"
        "- Kind: Freelance project\n- Year: 2025\n- Stack: Next.js, Stripe, Postgres\n"
        "- Live site: https://booking.example.com\n\n## What I built\n"
    )
    assert "title:" not in prepared.text and "---" not in prepared.text
    assert "jane.doe@clientco.example" not in prepared.text
    assert REDACTION in prepared.text
    assert prepared.redacted == {"email": 1}


def test_a_quarantine_category_skips_the_whole_portfolio_document():
    prepared = prepare_content(
        _source("portfolio", "corpus/portfolio/clientco.md"), PROJECT, frozenset({"email"})
    )
    assert prepared.text is None
    assert "quarantined" in prepared.error
    assert prepared.redacted == {"email": 1}


def test_an_invalid_portfolio_file_is_an_error_not_an_exception():
    # Review Focus 3: ingest skips and reports it; the run carries on.
    broken = "---\ntitle: X\n---\nBody\n"
    prepared = prepare_content(
        _source("portfolio", "corpus/portfolio/broken.md"), broken, frozenset()
    )
    assert prepared.text is None
    assert prepared.error.startswith("invalid portfolio file: ")
    assert "missing required field 'kind'" in prepared.error


def test_about_me_still_strips_front_matter_and_is_guarded():
    prepared = prepare_content(
        _source("about_me", "private/bio.md"),
        "---\ntype: bio\n---\n# Bio\n\nCall 614-555-0100.\n",
        frozenset(),
    )
    assert prepared.text == f"# Bio\n\nCall {REDACTION}.\n"
    assert prepared.redacted == {"phone": 1}


def test_about_system_is_indexed_as_written():
    text = "---\nname: x\n---\n# Runbook\n\nCall 614-555-0100.\n"
    prepared = prepare_content(_source("about_system", "docs/runbook.md"), text, frozenset())
    assert prepared.text == text and prepared.error is None and prepared.redacted is None


def test_content_hashes():
    raw = "a" * 64
    assert content_hash_for("about_system", raw) == raw
    # Unchanged for About Basel, so this PR re-embeds nothing there.
    assert content_hash_for("about_me", raw) == guarded_content_hash(raw)
    assert content_hash_for("portfolio", raw) == guarded_content_hash(index_content_hash(raw))
    assert content_hash_for("portfolio", raw) not in {raw, guarded_content_hash(raw)}
