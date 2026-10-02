"""The chat corpora, in one place (portfolio spec §6.2).

``Corpus`` is what ``POST /api/ask`` accepts; ``CORPORA`` holds the same names as a
tuple for loops (ingest, the stale sweep, the Redis reconcile, the warm-up). The
MySQL ``corpus`` ENUM columns hold the same list (``db/models.py``; migration
``0007`` appended ``portfolio``). The eval tooling under ``eval/`` keeps its own
two-corpus list on purpose: no portfolio evaluations until the owner adds projects.
"""

from typing import Literal, get_args

Corpus = Literal["about_me", "about_system", "portfolio"]
CORPORA: tuple[str, ...] = get_args(Corpus)
