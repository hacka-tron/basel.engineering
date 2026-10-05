"""Drop a "Sources: ..." line from streamed answer text (prompt v19).

The per-answer sources list under chat answers was removed on purpose (status
2026-10-03), and the prompt says never to cite source numbers, yet Nova Lite
sometimes ends an answer with its own "Sources: 1, 4, 5, 9" line (owner, live,
2026-10-05). This is the deterministic backstop: any line that starts with
"Source:" or "Sources:" (after optional Markdown decoration such as ``**``, ``#``,
``-`` or ``(``) is removed, together with the blank lines in front of it, so a
trailing sources line leaves no trailing newlines behind.

It works on a token stream: a line start that could still become "Sources:" is
held back until the next token decides it ("Sure" is released as soon as the
"u" arrives; "Sour" waits for one more token). Everything else streams through
with no delay. Apply it before ``privacy.StreamMasker``, so the masker sees only
text that will be sent.
"""

from __future__ import annotations

import re

# A whole sources label at a line start: decoration, "source(s)", optional
# emphasis, then a colon. "Source code lives in..." is not one (no colon).
_SOURCES_LABEL = re.compile(r"[ \t*_#>(\[-]*sources?[ \t*_]*:", re.IGNORECASE)
# A line start that may still grow into the label: decoration plus a prefix of
# "sources", optionally followed by emphasis or spaces (the colon not yet seen).
_LABEL_PREFIX = re.compile(
    r"[ \t*_#>(\[-]*(?:s(?:o(?:u(?:r(?:c(?:e(?:s)?)?)?)?)?)?)?[ \t*_]*",
    re.IGNORECASE,
)
# Longest held line start: decoration is short in practice; past this the line is
# released even if it still matches the prefix pattern (e.g. a row of dashes).
_HOLD_MAX = 32


def is_sources_line(line: str) -> bool:
    """True for a line that is a "Sources: ..." label (the whole line is dropped)."""
    return bool(_SOURCES_LABEL.match(line))


def strip_sources_lines(text: str) -> str:
    """The non-streaming form: the text without its "Sources:" lines."""
    lines_filter = SourcesLineFilter()
    return lines_filter.push(text) + lines_filter.flush()


class SourcesLineFilter:
    """Remove "Sources: ..." lines from text that arrives in arbitrary chunks.

    ``push`` returns the text that is safe to send now; ``flush`` returns what is
    still held at the end of the stream. ``dropped`` counts removed lines.
    """

    def __init__(self) -> None:
        self._buf = ""
        self._at_line_start = True
        self._dropping = False
        self.dropped = 0

    def push(self, part: str) -> str:
        self._buf += part
        out: list[str] = []
        while self._buf:
            if self._dropping:
                newline = self._buf.find("\n")
                if newline < 0:
                    self._buf = ""
                    break
                # Keep the newline that ended the dropped line: "A\nSources: 1\nB"
                # becomes "A\nB" (the newline before the label went with it).
                self._buf = self._buf[newline:]
                self._dropping = False
                self._at_line_start = True
                continue
            if not self._at_line_start:
                newline = self._buf.find("\n")
                if newline < 0:
                    out.append(self._buf)
                    self._buf = ""
                    break
                out.append(self._buf[:newline])
                self._buf = self._buf[newline:]
                self._at_line_start = True
                continue
            # At a line start: the buffer is blank lines (held, in case a sources
            # label follows them) and then the start of the next line.
            lead = len(self._buf) - len(self._buf.lstrip("\r\n"))
            line = self._buf[lead:]
            if not line:
                break  # only newlines so far: wait for the line they lead to
            if _SOURCES_LABEL.match(line):
                self.dropped += 1
                self._dropping = True
                self._buf = line
                continue
            newline = line.find("\n")
            head = line if newline < 0 else line[:newline]
            if newline < 0 and len(head) <= _HOLD_MAX and _LABEL_PREFIX.fullmatch(head):
                break  # could still become "Sources:": wait for the next token
            out.append(self._buf[:lead])
            self._buf = line
            self._at_line_start = False
        return "".join(out)

    def flush(self) -> str:
        """Release what is held at the end: an unfinished non-label line start."""
        if self._dropping:
            self._buf = ""
            return ""
        held, self._buf = self._buf, ""
        # Trailing blank lines are dropped too: they only ever preceded a label
        # candidate that never came.
        return held.rstrip("\r\n") if not held.strip() else held
