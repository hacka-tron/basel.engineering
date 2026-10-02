"""Which generated answers may enter the semantic answer cache."""

from services.glassbox.privacy import ANSWER_CATEGORIES, find_pii
from services.glassbox.providers.base import is_abstention


def uncacheable_reason(answer: object, chunks: list | None) -> str | None:
    """Why an answer must not be cached, or None when it may be.

    Only a real answer grounded in retrieved sources is worth replaying for 24h.
    An abstention ("I don't know from what I have.") is never cached: a one-off
    refusal would otherwise be served to every similar question until the TTL.
    The answer prompt asks for plain prose without citation markers, so a missing
    [n] marker is not a signal here. An answer holding a phone number or SSN is
    never cached either (the streaming path masks those before this point and
    skips the write itself; this is the backstop for any other writer).
    """
    if not isinstance(answer, str) or not answer.strip():
        return "empty"
    if not chunks:
        return "no_sources"
    if is_abstention(answer):
        return "abstention"
    if find_pii(answer, ANSWER_CATEGORIES):
        return "pii"
    return None
