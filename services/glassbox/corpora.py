"""The chat corpora, in one place (portfolio spec §6.2).

``Corpus`` is what ``POST /api/ask`` accepts; ``CORPORA`` holds the same names as a
tuple for loops (ingest, the stale sweep, the Redis reconcile, the warm-up). The
MySQL ``corpus`` ENUM columns hold the same list (``db/models.py``; migration
``0007`` appended ``portfolio``). The eval tooling under ``eval/`` keeps its own
list (``eval/schema.py``, ``eval/run_eval.py``); portfolio golden cases ask on About
Basel, which searches the portfolio files too (``SEARCH_CORPORA``).
"""

from typing import Literal, get_args

Corpus = Literal["about_me", "about_system", "portfolio"]
CORPORA: tuple[str, ...] = get_args(Corpus)

# The indexed corpora a topic's retrieval searches (owner, 2026-10-09: About Basel
# answers about the owner, the portfolio and freelance work, so it also searches
# the portfolio files). Ingest, the stale sweep and the Redis reconcile still work
# per indexed corpus; only retrieval widens.
SEARCH_CORPORA: dict[str, tuple[str, ...]] = {
    "about_me": ("about_me", "portfolio"),
    "about_system": ("about_system",),
    "portfolio": ("portfolio",),
}


def search_corpora(corpus: str) -> tuple[str, ...]:
    """The indexed corpora that ``corpus`` retrieves from (itself, for an unknown name)."""
    return SEARCH_CORPORA.get(corpus, (corpus,))
