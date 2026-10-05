"""Deterministic checks on a finished answer (prompt v17).

The persona answers as Basel, in the first person, in one or two sentences. Two
cheap regex checks catch the failures Nova Lite still produces: a third-person
reference to Basel in an About Basel answer, and an over-long answer (usually a
copied source). The API logs the result in the query log; the eval graders reuse
the same functions so production and the golden set measure the same thing.

The API does not regenerate a failed answer: it is streamed token by token, so the
visitor has already seen it when the check runs. See DESIGN.md §6.7 for the
trade-off and the proposed buffered retry.
"""

import re

# Above this many words an answer is not the one or two sentences the prompt asks
# for. Lists (languages, SSE events) stay well under it; copied sources run to
# 200-460 words.
ANSWER_WORD_CAP = 90
# Owner, 2026-10-05 (prompt v19): About This System answers ("how does X work?") may
# use up to three sentences; About Basel stays at one or two.
ANSWER_WORD_CAPS = {"about_system": 130}


def word_cap(corpus: str | None) -> int:
    """The post-generation word cap for a corpus."""
    return ANSWER_WORD_CAPS.get(corpus or "", ANSWER_WORD_CAP)


# "Basel" as a name, not inside basel.engineering or an email address; "he/his/him"
# (in an About Basel answer they almost always mean Basel); and the assistant
# framings the persona replaces.
_THIRD_PERSON = re.compile(
    r"(?<![\w.@/])Basel(?:'s|’s)?(?![\w.@-]*\.(?:engineering|com))\b"
    r"|\b(?:he|his|him|himself)\b"
    r"|\b(?:the|an|this|your) (?:AI )?assistant\b|\b(?:I am|I'm|I’m|as) an AI\b",
    re.IGNORECASE,
)


def third_person_hits(answer: str) -> list[str]:
    """Phrases that refer to Basel in the third person, or to an assistant or AI."""
    return [match.group(0) for match in _THIRD_PERSON.finditer(answer)]


def word_count(answer: str) -> int:
    return len(answer.split())


def answer_check_failures(answer: str, corpus: str) -> list[str]:
    """Names of the checks a finished answer fails: ``third_person``, ``too_long``.

    The third-person check applies to About Basel answers only: an About This
    System answer may legitimately quote a name or a "he" from a design document.
    """
    failures = []
    if corpus == "about_me" and third_person_hits(answer):
        failures.append("third_person")
    if word_count(answer) > word_cap(corpus):
        failures.append("too_long")
    return failures
