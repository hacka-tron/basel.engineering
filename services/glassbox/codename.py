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
_ID_BEFORE = r"(?<![\w/.\-])"
_ID_AFTER = r"(?![\w/\-]|\.\w)"
_PATTERN = re.compile(
    _ID_BEFORE
    + r"(?:(?P<the>[Tt]he) )?"
    + _WORD
    + r"(?:(?P<poss>'s)|(?P<system> system\b))?"
    + _ID_AFTER
    + r"(?P<noun> (?=[A-Za-z0-9]))?"
)
# What a held tail may still become: "the Glassbox system" or "Glassbox's", plus one
# more character to decide the next word.
_CANDIDATES = ("the Glassbox system ", "The Glassbox system ", "Glassbox's ", "Glassbox system ")
_HOLD_MAX = max(len(candidate) for candidate in _CANDIDATES)
_SENTENCE_END = re.compile(r"(?:^|[.!?:]\s+|\n\s*)$")


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


def rewrite(text: str, before: str = "") -> str:
    """``text`` with the codename replaced; ``before`` is the text already sent.

    Matching starts after ``before`` but its lookbehind sees it, so "My" sent
    earlier still keeps "Glassbox" in "MyGlassbox" from matching.
    """
    full = before + text
    out: list[str] = []
    last = len(before)
    for match in _PATTERN.finditer(full, len(before)):
        out.append(full[last : match.start()])
        out.append(_replacement(match, before + "".join(out)))
        last = match.end()
    out.append(full[last:])
    return "".join(out)


def _hold_start(buf: str, before: str) -> int:
    """Where a tail that may still grow into a match begins (len(buf) if none)."""
    for start in range(max(0, len(buf) - _HOLD_MAX), len(buf)):
        previous = buf[start - 1] if start > 0 else before[-1:]
        if previous and re.match(r"[\w/.\-]", previous):
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
        self._sent = ""
        self.rewritten = 0

    def _release(self, text: str) -> str:
        self.rewritten += sum(1 for _ in _PATTERN.finditer(self._sent + text, len(self._sent)))
        out = rewrite(text, self._sent)
        self._sent = (self._sent + out)[-8:]
        return out

    def push(self, part: str) -> str:
        self._buf += part
        hold = _hold_start(self._buf, self._sent)
        ready, self._buf = self._buf[:hold], self._buf[hold:]
        return self._release(ready) if ready else ""

    def flush(self) -> str:
        ready, self._buf = self._buf, ""
        return self._release(ready) if ready else ""
