"""Personal-data guard: detection, ingest-time redaction, answer-time stream masking.

Every phone number, address and ID here is a reserved or fictional example
(555-01xx, Ofcom drama numbers, 123-45-6789 style).
"""

import logging
import random
from pathlib import Path

import pytest

from services.glassbox.ingest.chunkers.markdown import chunk_markdown
from services.glassbox.ingest.scanner import strip_front_matter
from services.glassbox.privacy import (
    ADDRESS,
    ANSWER_CATEGORIES,
    DOB,
    EMAIL,
    GOV_ID,
    PHONE,
    REDACTION,
    StreamMasker,
    find_pii,
    guard_document,
    guarded_content_hash,
    mask_answer,
    quarantine_categories,
    redact,
)

REPO = Path(__file__).resolve().parents[2]


def categories(text, wanted=None):
    found = find_pii(text) if wanted is None else find_pii(text, wanted)
    return [finding.category for finding in found]


# --- Phone numbers ------------------------------------------------------------

PHONES = [
    "+1 (614) 555-0100",
    "614.555.0100",
    "614-555-0100",
    "(614) 555-0100",
    "614 555 0100",
    "6145550100",
    "16145550100",
    "+1 614 555 0100",
    "+1-614-555-0100",
    "1-800-555-0199",
    "+16145550100",
    "+44 20 7946 0958",
    "+44 7700 900123",
    "+447700900123",
    "+20 10 1234 5678",
    "+971-50-123-4567",
    "+49 30 1234567",
    "(+49) 30 1234567",
    "+33 1 23 45 67 89",
    "+91 98765 43210",
    "020 7946 0958",
    "07700 900123",
    "010 1234 5678",
    # Unicode and slash separators (folded before matching).
    "614\u2013555\u20130100",
    "614\u2014555\u20140100",
    "614\u2212555\u22120100",
    "614/555/0100",
    "\uff16\uff11\uff14\uff15\uff15\uff15\uff10\uff11\uff10\uff10",
    "\uff0b\uff11 (614) 555\uff0d0100",
    "+44\u00a020\u00a07946\u00a00958",
]


@pytest.mark.parametrize("phone", PHONES)
def test_phone_numbers_are_detected_alone_and_in_prose(phone):
    assert categories(phone) == [PHONE]
    sentence = f"You can reach him on {phone}, most days."
    redacted, counts = redact(sentence)
    assert counts == {PHONE: 1}
    assert redacted.startswith("You can reach him on ")
    assert redacted.endswith(", most days.")
    # Nothing of the number survives, not even a "(" before "+49".
    assert redacted == f"You can reach him on {REDACTION}, most days."


@pytest.mark.parametrize(
    "text",
    [
        "call me at 614 555 0100",
        "Call me at 614-555-0100 after 5.",
        "text me 555-0100",
        "phone: 46 70 123 45 67",
        "tel:+16145550100",
        "WhatsApp +20 10 1234 5678",
    ],
)
def test_phone_numbers_after_phone_words(text):
    assert categories(text) == [PHONE]


@pytest.mark.parametrize(
    "text",
    [
        "2026-10-01",
        "on 2026-10-01 at 14:05",
        "01-10-2026",
        "01.10.2026",
        "10/01/2026",
        "v1.2.3",
        "version 2.21.0 of KEDA",
        "1.2.3.4",
        "10.42.0.0/16",
        "53%",
        "99.5% reliability",
        "from 40% to 85%",
        "$1 million",
        "35 million virtual containers",
        "10,000+ daily executions",
        "2015–2019",
        "2015-2019",
        "July 2023–July 2024",
        "Sep 2019–July 2023",
        "in 2019 and 2020 we",
        "ports 6379 8000 3306",
        "1.84 GiB allocatable",
        "0.5 GiB",
        "t4g.small",
        "+1 to 3 workers",
        "+12",
        "+5% growth",
        "build-1234567890",
        "404379474987",
        "sha 3842459",
        "0 2 4 6 8",
        "cron 17 */2 * * *",
        "a 512-dim vector and 30 shared job runners",
        "https://www.linkedin.com/in/basel-abdel-rahman-198893166/",
        "call it 3 times",
        "text 2015-2019 resume",
        "Text me in 2026-10-01 style dates",
        "10/01/2026",
        "2015/2016 season",
        "1/2/3",
        "2015\u20132019 and 2019\u20142023",
        "\uff12\uff10\uff12\uff16-10-01",
        "page 12/345",
    ],
)
def test_non_phone_numbers_are_not_flagged(text):
    assert categories(text, [PHONE]) == []


# --- Government IDs, emails, addresses, dates of birth ------------------------


@pytest.mark.parametrize(
    "text",
    ["SSN 123-45-6789", "his SSN: 123 45 6789", "social security number 123456789", "123-45-6789"],
)
def test_ssn_like_ids_are_detected(text):
    assert categories(text) == [GOV_ID]


@pytest.mark.parametrize(
    "text",
    [
        "000-12-3456",
        "666-12-3456",
        "900-12-3456",
        "123-00-4567",
        "123-45-0000",
        "SSN required",
        "passport photo",
        "build-123-45-6789x",
    ],
)
def test_ssn_lookalikes_are_not_flagged(text):
    assert categories(text, [GOV_ID]) == []


def test_named_id_numbers_are_detected():
    assert categories("Passport number: X12345678", [GOV_ID]) == [GOV_ID]
    assert categories("driver's license D1234-5678", [GOV_ID]) == [GOV_ID]


def test_personal_emails_are_detected_but_the_public_one_is_allowed(monkeypatch):
    assert categories("mail me at someone.private@example.com") == [EMAIL]
    assert categories("Reach Basel via email (baselmabdelrahman@gmail.com).") == []
    assert categories("BaselMAbdelRahman@Gmail.com") == []
    monkeypatch.setenv("GLASSBOX_PII_ALLOWED_EMAILS", "work@example.com")
    assert categories("work@example.com") == []


@pytest.mark.parametrize(
    "text",
    [
        "He lives at 123 Main St, Apt 4 in town.",
        "1600 Pennsylvania Avenue NW",
        "42 North High Street",
        "PO Box 1234",
        "Columbus, OH 43210",
        "London SW1A 1AA",
    ],
)
def test_street_addresses_are_detected(text):
    assert ADDRESS in categories(text)


@pytest.mark.parametrize(
    "text",
    [
        "The Ohio State University (Columbus, OH), 2015–2019.",
        "a group of 3 engineers",
        "over 35 million virtual containers",
        "led 2 junior engineers",
        "a 3 Way handshake",
        "M3 (DD2) remainder",
    ],
)
def test_ordinary_prose_is_not_an_address(text):
    assert categories(text, [ADDRESS]) == []


@pytest.mark.parametrize(
    "text",
    [
        "born on March 3, 1995",
        "Born 3 March 1995 in Cairo",
        "DOB: 03/04/1995",
        "date of birth 1995-03-04",
        "He was born in 1995.",
        "birthday: Mar. 3, 1995",
    ],
)
def test_dates_of_birth_are_detected_but_not_other_dates(text):
    assert categories(text) == [DOB]
    assert categories("Microsoft (July 2024–present), born in Cairo") == []


def test_links_already_on_the_site_are_allowed():
    text = (
        "[LinkedIn](https://www.linkedin.com/in/basel-abdel-rahman-198893166/), "
        "[GitHub](https://github.com/hacka-tron), "
        "https://github.com/hacka-tron/goalbuddy/"
    )
    assert find_pii(text) == []


# --- A local private checkout --------------------------------------------------


def test_a_local_private_about_me_checkout_passes_the_guard_unchanged():
    """The About Basel files now live in the private repo; check a local checkout if present.

    CI and fresh clones have no checkout (it is git-ignored), so this skips there. The
    ingest-time guard still redacts on every release regardless.
    """
    files = sorted((REPO / "corpus" / "about-me-private" / "about-me").glob("*.md"))
    if not files:
        pytest.skip("no local private About Basel checkout")
    for path in files:
        body = strip_front_matter(path.read_text())
        assert find_pii(body) == [], path.name
        assert redact(body) == (body, {})


# --- Ingest-time redaction -------------------------------------------------------


DOC = """# About

Basel lives at 123 Main St, Apt 4, Columbus, OH 43210.
Call 614-555-0100 or email him at private.person@example.com.
Public contact: baselmabdelrahman@gmail.com.

## Work

Built release gates running 10,000+ tests at 99.5% reliability (2019-2023).
"""


def test_guard_redacts_spans_in_every_stored_chunk_and_logs_no_values(caplog):
    caplog.set_level(logging.WARNING, logger="services.glassbox.privacy")
    result = guard_document(DOC, "private/bio-notes.md", quarantine=frozenset())
    assert result.quarantined is None
    assert result.counts == {ADDRESS: 2, PHONE: 1, EMAIL: 1}
    chunks = chunk_markdown(result.text, "private/bio-notes.md")
    stored = "".join(chunk.text for chunk in chunks)
    for secret in ("123 Main", "OH 43210", "555-0100", "private.person"):
        assert secret not in stored
    assert REDACTION in stored
    assert "baselmabdelrahman@gmail.com" in stored
    assert "10,000+ tests at 99.5% reliability (2019-2023)" in stored
    log = caplog.text
    assert "private/bio-notes.md" in log and "phone=1" in log
    for secret in ("123 Main", "43210", "555-0100", "private.person"):
        assert secret not in log


def test_guard_quarantines_a_whole_document_for_configured_categories(caplog):
    caplog.set_level(logging.WARNING, logger="services.glassbox.privacy")
    text = DOC + "\nSSN 123-45-6789\n"
    result = guard_document(text, "corpus/about-me/x.md", quarantine=frozenset({GOV_ID}))
    assert result.quarantined and "gov_id" in result.quarantined
    assert result.text == ""
    assert "123-45-6789" not in caplog.text
    # Without the flag the same document is redacted instead.
    redacted = guard_document(text, "corpus/about-me/x.md", quarantine=frozenset())
    assert redacted.quarantined is None and "123-45-6789" not in redacted.text


def test_clean_documents_pass_through_untouched():
    text = "# Skills\n\nPython, Java, C#. 53% less CPU in 2024.\n"
    result = guard_document(text, "corpus/about-me/skills.md", quarantine=frozenset())
    assert result.text == text and not result.counts and result.quarantined is None


def test_quarantine_categories_come_from_the_environment(monkeypatch):
    monkeypatch.delenv("GLASSBOX_PII_QUARANTINE", raising=False)
    assert quarantine_categories() == frozenset()
    monkeypatch.setenv("GLASSBOX_PII_QUARANTINE", "gov_id, phone")
    assert quarantine_categories() == {GOV_ID, PHONE}
    monkeypatch.setenv("GLASSBOX_PII_QUARANTINE", "ssn")
    with pytest.raises(ValueError):
        quarantine_categories()


def test_guard_version_changes_the_stored_hash():
    raw = "a" * 64
    assert guarded_content_hash(raw) != raw
    assert guarded_content_hash(raw) == guarded_content_hash(raw)


# --- Answer-time masking -----------------------------------------------------------


ANSWERS = [
    "You can call Basel at +1 (614) 555-0100 or on 614.555.0100.",
    "His number is 614 555 0100.",
    "Reach him on +44 20 7946 0958 or +20 10 1234 5678 today.",
    "The SSN 123-45-6789 should never appear.",
    "SSN: 123 45 6789",
    "Basel worked at Google from 2019 to 2023 and cut CPU by 53% (v1.2.3, 2026-10-01).",
    "Numbers only 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 end",
    "Ends with a number 614-555-0100",
    "Ring (+49) 30 1234567 now, or (614) 555-0100.",
    "Version 1.2.3 shipped; call 07700 900123.",
    "Dashes too: 614\u2013555\u20130100 and 614/555/0100, years 2015\u20132019.",
    "Fullwidth \uff16\uff11\uff14\uff15\uff15\uff15\uff10\uff11\uff10\uff10 end",
    "",
]


def _stream(parts):
    masker = StreamMasker()
    out = [masker.push(part) for part in parts]
    out.append(masker.flush())
    return "".join(out), masker


@pytest.mark.parametrize("answer", ANSWERS)
def test_streamed_masking_equals_whole_answer_masking_for_every_split(answer):
    expected, count = mask_answer(answer)
    # Every single cut point, then many random multi-way splits.
    for cut in range(len(answer) + 1):
        streamed, masker = _stream([answer[:cut], answer[cut:]])
        assert streamed == expected, (cut, streamed)
        assert masker.masked == count
    rng = random.Random(1234)
    for _ in range(200):
        cuts = sorted(rng.sample(range(len(answer) + 1), k=min(len(answer) + 1, 6)))
        parts = [answer[a:b] for a, b in zip([0, *cuts], [*cuts, len(answer)], strict=True)]
        streamed, _ = _stream(parts)
        assert streamed == expected, parts


def test_a_phone_number_split_across_tokens_never_reaches_the_client():
    parts = ["Call me at ", "614", " 555", "-01", "00", " today."]
    masker = StreamMasker()
    sent = []
    for part in parts:
        sent.append(masker.push(part))
        # No released piece may hold any digit of the number.
        assert not any(char.isdigit() for char in "".join(sent))
    sent.append(masker.flush())
    assert "".join(sent) == f"Call me at {REDACTION} today."
    assert masker.masked == 1


def test_prose_streams_with_at_most_one_token_of_delay():
    masker = StreamMasker()
    assert masker.push("Basel ") == "Basel "
    assert masker.push("worked at ") == "worked at "
    # A trailing number is held until the next token shows it isn't a phone.
    assert masker.push("Google in 2019") == "Google in "
    assert masker.push(" and Microsoft") == "2019 and Microsoft"
    assert masker.flush() == ""


def test_a_long_run_of_numbers_is_released_in_bounded_pieces():
    masker = StreamMasker()
    released = masker.push("1 " * 100)
    assert len(released) >= 200 - StreamMasker.HOLD_MAX
    assert released + masker.flush() == "1 " * 100


def test_answer_masking_only_covers_phones_and_ids():
    assert ANSWER_CATEGORIES == {PHONE, GOV_ID}
    text = "Email private.person@example.com, born 1995, 123 Main St."
    assert mask_answer(text) == (text, 0)
