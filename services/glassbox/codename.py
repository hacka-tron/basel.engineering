"""Replace the project codename "Glassbox" in answer text with "this site".

The design docs the chatbot answers from call the site by its internal codename
("Glassbox's retrieval...", "the Glassbox system"), and Nova Lite copies that
wording, so visitors read "the Glassbox system" instead of the site they are on
(owner, 2026-10-08). Prompt rules don't stop Nova Lite copying source wording
(rag-plan-brief Gotchas), so this is a deterministic rewrite, like
``sources_line``:

- "the Glassbox system" -> "this site"
- "the Glassbox <word>" -> "this site's <word>" ("the Glassbox API")
- "Glassbox's" -> "this site's"
- "Glassbox" -> "this site"

Only the capitalized word is rewritten, and never inside an identifier such as
``services/glassbox``, ``glassbox-api`` or ``GLASSBOX_PROVIDER`` (lowercase, upper
case, or joined to ``/ . _ -``). "This" is capitalized at the start of a sentence.

It works on a token stream: a tail that could still grow into a match ("the
Glass", "the Glassbox s") is held back until the next token decides it.
"""

from __future__ import annotations

import re

_WORD = "Glassbox"
# Joined to an identifier on either side: services/glassbox, Glassbox-api, x.Glassbox.
_ID_BEFORE = r"(?<![\w/.\-`@#])"
_ID_AFTER = r"(?![\w/\-`#]|\.\w)"
_PATTERN = re.compile(
    _ID_BEFORE
    + r"(?:(?P<the>[Tt]he) )?"
    + _WORD
    + r"(?:(?P<poss>'s)|(?P<system> system\b))?"
    + _ID_AFTER
    + r"(?P<noun> (?=[A-Za-z0-9]))?"
)
# What a held tail may still become, plus one more character to decide it: "the
# Glassbox system", "the Glassbox's" (review #205: without it "the" was released
# alone and the stream said "the this site's"), and "Glassbox.com" (an identifier).
_CANDIDATES = tuple(
    article + rest
    for article in ("the ", "The ", "")
    for rest in ("Glassbox system ", "Glassbox's ", "Glassbox. ")
)
_HOLD_MAX = max(len(candidate) for candidate in _CANDIDATES)
_SENTENCE_END = re.compile(
    # Start of text, a sentence end, or a line start with list or heading markers.
    r"(?:(?<!\be\.g)(?<!\bi\.e)[.!?:]\s+|(?:^|\n)[ \t]*(?:(?:[#>*-]+|\d+[.)])[ \t]+)?)$"
)


def _replacement(match: re.Match[str], before: str) -> str:
    if match.group("system"):
        phrase = "this site"
    elif match.group("poss") or (match.group("the") and match.group("noun")):
        phrase = "this site's"
    else:
        phrase = "this site"
    if match.group("noun"):
        phrase += " "
    if _SENTENCE_END.search(before) or (match.group("the") or "").startswith("T"):
        phrase = phrase[0].upper() + phrase[1:]
    return phrase


def _rewrite_span(full: str, start: int, end: int, out_before: str) -> tuple[str, int]:
    """``full[start:end]`` rewritten, matching over all of ``full``.

    ``full[:start]`` is context only (the lookbehind keeps "Glassbox" in "MyGlassbox"
    from matching) and ``full[end:]`` lets the lookaheads see the next characters.
    ``out_before`` is the rewritten text before ``start``, for capitalization.
    """
    out: list[str] = []
    last = start
    count = 0
    for match in _PATTERN.finditer(full, start):
        if match.start() >= end:
            break
        out.append(full[last : match.start()])
        out.append(_replacement(match, out_before + "".join(out)))
        last = match.end()
        count += 1
    out.append(full[last:end])
    return "".join(out), count


def rewrite(text: str) -> str:
    """``text`` with the codename replaced (the non-streaming form)."""
    return _rewrite_span(text, 0, len(text), "")[0]


def _hold_start(buf: str, before: str) -> int:
    """Where a tail that may still grow into a match begins (len(buf) if none)."""
    for start in range(max(0, len(buf) - _HOLD_MAX), len(buf)):
        previous = buf[start - 1] if start > 0 else before[-1:]
        if previous and re.match(r"[\w/.\-`@#]", previous):
            continue  # not at a word start
        tail = buf[start:]
        if any(candidate.startswith(tail) for candidate in _CANDIDATES):
            return start
    return len(buf)


class CodenameFilter:
    """Rewrite the codename in text that arrives in arbitrary chunks.

    ``push`` returns the text that is safe to send now; ``flush`` returns the rest.
    ``rewritten`` counts replacements.
    """

    def __init__(self) -> None:
        self._buf = ""
        self._src = ""  # the last released characters as received (match context)
        self._out = ""  # the last released characters as sent (capitalization)
        self.rewritten = 0

    def _release(self, cut: int) -> str:
        full = self._src + self._buf
        base = len(self._src)
        out, count = _rewrite_span(full, base, base + cut, self._out)
        self.rewritten += count
        self._src = full[: base + cut][-8:]
        self._out = (self._out + out)[-8:]
        self._buf = self._buf[cut:]
        return out

    def push(self, part: str) -> str:
        self._buf += part
        cut = _hold_start(self._buf, self._src)
        # Never cut inside a match: it would be rewritten in two pieces.
        base = len(self._src)
        for match in _PATTERN.finditer(self._src + self._buf, base):
            if match.start() >= base + cut:
                break
            if match.end() > base + cut:
                cut = match.start() - base
                break
        return self._release(cut) if cut else ""

    def flush(self) -> str:
        return self._release(len(self._buf)) if self._buf else ""
