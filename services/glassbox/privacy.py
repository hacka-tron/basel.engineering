"""Personal-data guard shared by ingestion and answering (DESIGN.md §11, "Privacy").

Two layers use the same detector:

* **Ingest time** (``guard_document``): every ``about_me`` document, from
  ``corpus/about-me/`` or the owner's private About Basel repo (``private/...``),
  is scanned before chunking. Detected spans are replaced
  with ``[redacted]``, so neither the stored chunk text, the embeddings, the
  title nor the public retrieval snippets ever hold them. Categories listed in
  ``GLASSBOX_PII_QUARANTINE`` (for example ``gov_id``) skip the whole document
  instead. Logs name the document and the category counts, never the value.
* **Answer time** (``mask_answer`` and ``StreamMasker``): generated text is
  scanned for phone numbers and government IDs before it is streamed, and an
  answer that needed masking is never cached.

The detector is regex-based and deliberately biased toward catching personal
data: a false positive costs a ``[redacted]`` in an answer, a false negative
leaks a phone number. It is not a general PII classifier; names, for example,
are not detected (the corpus is about a named person on purpose).
"""

import hashlib
import logging
import os
import re
from collections import Counter
from dataclasses import dataclass, field

LOGGER = logging.getLogger(__name__)

# Bump when a pattern changes: it is folded into about_me content hashes, so every
# about_me document is re-scanned (and re-embedded) once under the new rules.
PII_GUARD_VERSION = "1"
REDACTION = "[redacted]"

PHONE = "phone"
EMAIL = "email"
ADDRESS = "address"
DOB = "dob"
GOV_ID = "gov_id"
CATEGORIES = (PHONE, EMAIL, ADDRESS, DOB, GOV_ID)
# Answer time scans only the patterns that need no surrounding prose to be sure
# about and that matter most if leaked (owner decision, 2026-10-01).
ANSWER_CATEGORIES = frozenset({PHONE, GOV_ID})

# Public contact details already on the site (corpus/about-me/bio.md and the
# frontend's ContactReveal); extend with GLASSBOX_PII_ALLOWED_EMAILS (comma list).
ALLOWED_EMAILS = frozenset({"baselmabdelrahman@gmail.com"})
ALLOWED_URL_PREFIXES = (
    "https://www.linkedin.com/in/basel-abdel-rahman-198893166",
    "https://linkedin.com/in/basel-abdel-rahman-198893166",
    "https://github.com/hacka-tron",
)

# Separators allowed inside a phone number: space, dot, hyphen (and NBSP).
_SEP = r"[ .\-\u00a0]"
_NOT_BEFORE = r"(?<![\w+])(?<!\d[.\-])"
_NOT_AFTER = r"(?!\w|[.\-]\d)"

# North American numbers: +1 (614) 555-0100, 614.555.0100, 614 555 0100,
# 6145550100. Area code and exchange start with 2-9 (NANP rules), which keeps
# dates (2026-10-01), versions and most IDs out.
_NANP = re.compile(
    _NOT_BEFORE
    + r"(?:\+?1"
    + _SEP
    + r"?)?"
    + r"(?:\(\s?[2-9]\d{2}\s?\)|[2-9]\d{2})"
    + _SEP
    + r"?"
    + r"[2-9]\d{2}"
    + _SEP
    + r"?"
    + r"\d{4}"
    + _NOT_AFTER
)
# International with a leading +: +44 20 7946 0958, +20 10 1234 5678,
# +971-50-123-4567, (+49) 30 1234567. 8 to 15 digits (E.164 allows 15).
_INTERNATIONAL = re.compile(
    r"(?<![\w+])\+\(?\d{1,3}\)?(?:" + _SEP + r"?\(?\d{1,12}\)?){1,6}" + _NOT_AFTER
)
# National numbers with a trunk 0: 020 7946 0958, 07700 900123, 010 1234 5678.
_TRUNK_ZERO = re.compile(r"(?<![\w+.\-])0\d{1,4}(?:" + _SEP + r"\d{2,6}){1,3}" + _NOT_AFTER)
# A number right after a phone word: "call me at 555-0100", "tel: 46 70 123 45 67".
_PHONE_WORD = re.compile(
    r"\b(?:phone|tel|telephone|mobile|cell|cellphone|call|text|whatsapp|fax)\b"
    r"[^\n\d]{0,20}?(?P<value>\+?\(?\d[\d ().\-\u00a0]{5,22}\d)",
    re.IGNORECASE,
)
_DATE_SHAPED = re.compile(
    r"^(?:\d{4}\s*[-/.]\s*\d{1,2}\s*[-/.]\s*\d{1,2}|\d{1,2}\s*[-/.]\s*\d{1,2}\s*[-/.]\s*\d{2,4}"
    r"|\d{4}\s*-\s*\d{4})$"
)

# US SSN: 123-45-6789 (never 000/666/9xx area, 00 group, 0000 serial).
_SSN = re.compile(r"(?<![\w\-])(?!000|666|9\d\d)\d{3}-(?!00)\d{2}-(?!0000)\d{4}(?![\w\-]|\.\d)")
# An SSN written with spaces or no separators, only when named.
_SSN_WORD = re.compile(
    r"(?i:\b(?:ssn|social\s+security(?:\s+(?:number|no\.?|#))?|tax\s+id|itin)\b)"
    r"\s*[:#]?\s*(?P<value>\d{3}[ \-]?\d{2}[ \-]?\d{4})(?![\w\-])"
)
# Other ID numbers, only when named. The value is case-sensitive (upper-case
# letters and digits), so the words after a label are never swallowed.
_ID_WORD = re.compile(
    r"(?i:\b(?:passport|driver'?s?\s+licen[cs]e|national\s+id)(?:\s+(?:number|no\.?|#))?)"
    r"\s*[:#]?\s*(?P<value>[A-Z0-9][A-Z0-9\-]{4,18}[A-Z0-9])(?![\w\-])"
)

_EMAIL = re.compile(
    r"(?<![\w.+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}"
)

_STREET_SUFFIX = (
    r"(?:Street|St|Avenue|Ave|Road|Rd|Boulevard|Blvd|Lane|Ln|Drive|Dr|Court|Ct|Way|Place|Pl"
    r"|Terrace|Ter|Circle|Cir|Parkway|Pkwy|Highway|Hwy|Square|Sq|Trail|Trl|Plaza)"
)
_STREET = re.compile(
    r"\b\d{1,6}[A-Za-z]?\s+(?:[NSEW]\.?\s+)?(?:[A-Z][A-Za-z'\-]*\.?\s+){1,4}"
    + _STREET_SUFFIX
    + r"\b\.?(?:\s+(?:NW|NE|SW|SE|N|S|E|W)\b)?"
    r"(?:,?\s+(?:Apt|Apartment|Suite|Ste|Unit|Floor|Fl|#)\.?\s*[A-Za-z0-9\-]+)?"
)
_PO_BOX = re.compile(r"\bP\.?\s?O\.?\s+Box\s+\d+\b", re.IGNORECASE)
_US_STATES = (
    "AL|AK|AZ|AR|CA|CO|CT|DE|DC|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|MI|MN|MS|MO|MT|NE|NV|NH"
    "|NJ|NM|NY|NC|ND|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VT|VA|WA|WV|WI|WY"
)
_STATE_ZIP = re.compile(r"(?<=,\s)(?:" + _US_STATES + r")\s+\d{5}(?:-\d{4})?\b")
_UK_POSTCODE = re.compile(r"\b[A-Z]{1,2}\d[A-Z\d]?\s\d[A-Z]{2}\b")

_MONTH = (
    r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?"
    r"|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
)
_DATE = (
    r"(?:\d{4}-\d{1,2}-\d{1,2}|\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4}"
    r"|" + _MONTH + r"\.?\s+\d{1,2}(?:st|nd|rd|th)?,?\s+\d{4}"
    r"|\d{1,2}(?:st|nd|rd|th)?\s+(?:of\s+)?" + _MONTH + r"\.?,?\s+\d{4}"
    r"|" + _MONTH + r"\.?\s+\d{4}|(?:19|20)\d{2})"
)
_DOB = re.compile(
    r"\b(?:born(?:\s+on|\s+in)?|date\s+of\s+birth|d\.o\.b\.?|dob|birth\s*date|birthday)\b"
    r"\s*(?:is|was)?\s*[:\-–]?\s*(?P<value>" + _DATE + r")\b",
    re.IGNORECASE,
)
_URL = re.compile(r"https?://[^\s)\]>\"']+")


@dataclass(frozen=True)
class Finding:
    category: str
    start: int
    end: int


@dataclass
class GuardResult:
    """What ``guard_document`` decided for one document."""

    text: str
    counts: Counter = field(default_factory=Counter)
    quarantined: str | None = None


def _digits(value: str) -> str:
    return re.sub(r"\D", "", value)


def _env_set(name: str) -> frozenset[str]:
    raw = os.environ.get(name, "")
    return frozenset(item.strip().lower() for item in raw.split(",") if item.strip())


def allowed_emails() -> frozenset[str]:
    return ALLOWED_EMAILS | _env_set("GLASSBOX_PII_ALLOWED_EMAILS")


def allowed_phones() -> frozenset[str]:
    """Digits of phone numbers that may appear (none by default)."""
    return frozenset(_digits(value) for value in _env_set("GLASSBOX_PII_ALLOWED_PHONES"))


def quarantine_categories() -> frozenset[str]:
    """Categories that skip a whole document instead of redacting it.

    Set with ``GLASSBOX_PII_QUARANTINE`` (comma list, e.g. ``gov_id``); empty by default.
    """
    categories = _env_set("GLASSBOX_PII_QUARANTINE")
    unknown = categories - set(CATEGORIES)
    if unknown:
        raise ValueError(f"GLASSBOX_PII_QUARANTINE has unknown categories: {sorted(unknown)}")
    return categories


def _allowlisted_spans(text: str) -> list[tuple[int, int]]:
    spans = []
    for match in _URL.finditer(text):
        if match.group(0).lower().startswith(ALLOWED_URL_PREFIXES):
            spans.append(match.span())
    return spans


def _phone_candidate_ok(value: str, *, min_digits: int) -> bool:
    if _DATE_SHAPED.match(value.strip()):
        return False
    return min_digits <= len(_digits(value)) <= 15


def find_pii(text: str, categories=CATEGORIES) -> list[Finding]:
    """Non-overlapping findings in ``text`` for ``categories``, sorted by position."""
    categories = set(categories)
    raw: list[Finding] = []

    def add(category: str, start: int, end: int) -> None:
        raw.append(Finding(category, start, end))

    if PHONE in categories:
        phones = allowed_phones()
        # NANP's shape is strict enough on its own; the others need enough digits
        # (a trunk-0 number has at least 9: "01-10-2026" is a date, not a phone).
        for pattern, min_digits in ((_NANP, 10), (_INTERNATIONAL, 8), (_TRUNK_ZERO, 9)):
            for match in pattern.finditer(text):
                value = match.group(0)
                if not _phone_candidate_ok(value, min_digits=min_digits):
                    continue
                if _digits(value) in phones:
                    continue
                add(PHONE, *match.span())
        for match in _PHONE_WORD.finditer(text):
            value = match.group("value").rstrip(" .-)")
            if _phone_candidate_ok(value, min_digits=7) and _digits(value) not in phones:
                start = match.start("value")
                add(PHONE, start, start + len(value))
    if GOV_ID in categories:
        for match in _SSN.finditer(text):
            add(GOV_ID, *match.span())
        for match in _SSN_WORD.finditer(text):
            add(GOV_ID, *match.span("value"))
        for match in _ID_WORD.finditer(text):
            # Needs digits: "passport photo" or "Passport NUMBER" are not IDs.
            if sum(character.isdigit() for character in match.group("value")) >= 4:
                add(GOV_ID, *match.span("value"))
    if EMAIL in categories:
        emails = allowed_emails()
        for match in _EMAIL.finditer(text):
            if match.group(0).lower() not in emails:
                add(EMAIL, *match.span())
    if ADDRESS in categories:
        for pattern in (_STREET, _PO_BOX, _STATE_ZIP, _UK_POSTCODE):
            for match in pattern.finditer(text):
                add(ADDRESS, *match.span())
    if DOB in categories:
        for match in _DOB.finditer(text):
            add(DOB, *match.span("value"))

    allowed = _allowlisted_spans(text)
    findings = [
        finding
        for finding in raw
        if not any(start <= finding.start and finding.end <= end for start, end in allowed)
    ]
    # Merge overlaps (e.g. "+1 (614) 555-0100" matches both phone patterns); the
    # first-starting, then longest, finding names the merged span.
    findings.sort(key=lambda finding: (finding.start, -finding.end))
    merged: list[Finding] = []
    for finding in findings:
        if merged and finding.start < merged[-1].end:
            last = merged[-1]
            merged[-1] = Finding(last.category, last.start, max(last.end, finding.end))
        else:
            merged.append(finding)
    return merged


def redact(text: str, categories=CATEGORIES) -> tuple[str, Counter]:
    """Replace every finding with ``[redacted]``; returns the text and per-category counts."""
    findings = find_pii(text, categories)
    if not findings:
        return text, Counter()
    parts = []
    position = 0
    for finding in findings:
        parts.append(text[position : finding.start])
        parts.append(REDACTION)
        position = finding.end
    parts.append(text[position:])
    return "".join(parts), Counter(finding.category for finding in findings)


def guarded_content_hash(raw_hash: str) -> str:
    """The stored hash of a guarded document: its raw hash plus the guard version.

    A new guard version changes every guarded document's hash, so the next ingest
    re-scans them all under the new rules instead of skipping them as unchanged.
    """
    return hashlib.sha256(f"{raw_hash}:pii-guard-v{PII_GUARD_VERSION}".encode()).hexdigest()


def guard_document(
    text: str, document_id: str, *, quarantine: frozenset[str] | None = None
) -> GuardResult:
    """Redact personal data from one ``about_me`` document, or quarantine it.

    Logs ``document_id`` and per-category counts only, never a matched value.
    """
    quarantine = quarantine_categories() if quarantine is None else quarantine
    redacted, counts = redact(text)
    if not counts:
        return GuardResult(text)
    summary = ", ".join(f"{category}={count}" for category, count in sorted(counts.items()))
    hit = sorted(set(counts) & set(quarantine))
    if hit:
        reason = f"personal data guard quarantined the document ({', '.join(hit)})"
        LOGGER.warning("PII guard QUARANTINED %s: %s", document_id, summary)
        return GuardResult(text="", counts=counts, quarantined=reason)
    LOGGER.warning("PII guard redacted %s: %s", document_id, summary)
    return GuardResult(redacted, counts)


def mask_answer(text: str) -> tuple[str, int]:
    """Mask phone numbers and government IDs in a complete answer; returns (text, count)."""
    masked, counts = redact(text, ANSWER_CATEGORIES)
    return masked, sum(counts.values())


# Characters a phone number or SSN is made of. Matches never span anything else,
# so text before the trailing run of these characters is final.
_NUMBERISH = frozenset("0123456789+()-. \t\u00a0")
_RUN_START = frozenset("0123456789+(")


class StreamMasker:
    """Mask phone numbers and SSNs in streamed text, across token boundaries.

    ``push`` returns the text that is safe to send now and holds back the trailing
    run of number-like characters (digits, spaces, ``+()-.``), because the next
    token may extend it into a phone number: "call 614" + " 555" + "-0100". Held
    text is released as soon as a token ends the run (any letter or other
    character), so prose streams with at most one token of delay, and ``flush``
    releases the rest at the end. Pattern matching sees the last ``CONTEXT``
    characters already sent, so "SSN: 123 45 6789" is still recognised by its
    label. A run longer than ``HOLD_MAX`` (a long table of numbers) is released
    from its head, keeping ``HOLD_MAX`` characters, which is longer than any
    phone number the patterns accept.
    """

    HOLD_MAX = 64
    CONTEXT = 40

    def __init__(self, categories=ANSWER_CATEGORIES):
        self._categories = frozenset(categories)
        self._held = ""
        self._sent_tail = ""
        self.masked = 0

    def push(self, part: str) -> str:
        self._held += part
        cut = len(self._held)
        while cut > 0 and self._held[cut - 1] in _NUMBERISH:
            cut -= 1
        # Spaces and punctuation before the run's first digit, "+" or "(" can't
        # start a match, so they go out now ("Basel " is not held).
        while cut < len(self._held) and self._held[cut] not in _RUN_START:
            cut += 1
        cut = max(cut, len(self._held) - self.HOLD_MAX)
        return self._release(cut)

    def flush(self) -> str:
        return self._release(len(self._held))

    def _release(self, cut: int) -> str:
        if cut <= 0:
            return ""
        window = self._sent_tail + self._held
        offset = len(self._sent_tail)
        findings = find_pii(window, self._categories)
        # A finding still touching the held tail may grow with the next token.
        for finding in findings:
            if finding.end > offset + cut and finding.start < offset + cut:
                cut = max(0, finding.start - offset)
        if cut <= 0:
            return ""
        parts = []
        position = offset
        for finding in findings:
            if finding.end <= offset or finding.start >= offset + cut:
                continue
            start = max(finding.start, offset)
            parts.append(window[position:start])
            parts.append(REDACTION)
            position = finding.end
            self.masked += 1
        parts.append(window[position : offset + cut])
        released = "".join(parts)
        self._held = self._held[cut:]
        self._sent_tail = (self._sent_tail + released)[-self.CONTEXT :]
        return released
