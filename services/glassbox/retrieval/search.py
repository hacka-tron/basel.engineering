"""Hybrid retrieval over the Redis Search chunk index (RAG plan phase 8).

Two legs run against ``idx:chunks`` with the same corpus and model filters:

* **vector**: HNSW KNN on the question embedding (cosine);
* **lexical**: BM25 full-text search on the ``text`` field (the chunk text,
  headings included), with the question's terms OR-joined.

Their ranked lists are fused with reciprocal rank fusion (RRF), then a
per-document cap and the final size are applied. For About Basel technology
questions ("have you used Redis?") one slot goes to the best professional chunk
that names the technology and one to the best personal-project chunk that does
(:func:`select_chunks`), so an answer can name both sides when the data has both.
"""

import logging
import math
import re
import struct
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from redis.exceptions import ResponseError

from services.glassbox.cache.answer import _model_tag
from services.glassbox.corpora import CORPORA
from services.glassbox.ingest.redis_index import INDEX_NAME, KINDS

LOGGER = logging.getLogger(__name__)
VECTOR_DIMENSIONS = 512


@dataclass(frozen=True)
class RetrievalConfig:
    """Production retrieval settings for one corpus (measured in the phase 8 report)."""

    top_k: int
    per_document_cap: int
    candidates: int = 20
    rrf_k: int = 60
    lexical_weight: float = 1.0
    dual_experience: bool = False


# About Basel is small (about 15 chunks): 6 chunks, at most 2 per file, and the
# dual-experience slots. About This System keeps 8 chunks, at most 3 per file.
RETRIEVAL_CONFIGS: dict[str, RetrievalConfig] = {
    "about_me": RetrievalConfig(top_k=6, per_document_cap=2, dual_experience=True),
    "about_system": RetrievalConfig(top_k=8, per_document_cap=3),
    "portfolio": RetrievalConfig(top_k=8, per_document_cap=3),
}
# Part of the retrieval-cache key: entries computed by another retrieval mode
# (vector-only before phase 8) are never reused.
RETRIEVAL_MODE = "hybrid-v1"

# Where an About Basel chunk's experience comes from: the private file it is in.
PROFESSIONAL = "professional"
PERSONAL_PROJECT = "personal_project"
EXPERIENCE_BY_SOURCE = {
    "private/microsoft.md": PROFESSIONAL,
    "private/google.md": PROFESSIONAL,
    "private/projects.md": PERSONAL_PROJECT,
}


def experience_of(source_path: str) -> str | None:
    """``professional``, ``personal_project`` or None (bio, skills, personal, other corpora)."""
    return EXPERIENCE_BY_SOURCE.get(source_path)


# ------------------------------------------------------------------ lexical query

# English stopwords plus question words: BM25 would down-weight them anyway, but
# leaving them out keeps the OR query short and stops "what"/"how" matching.
STOPWORDS = frozenset(
    """
    a about above after again against all am an and any are as at be because been
    before being below between both but by can could did do does doing done down
    during each few for from further had has have having he her here hers herself him
    himself his how i if in into is it its itself just me more most my myself no nor
    not now of off on once only or other our ours ourselves out over own same she
    should so some such than that the their theirs them themselves then there these
    they this those through to too under until up very was we were what when where
    which while who whom why will with would you your yours yourself yourselves
    tell explain describe please ever also much many us let get got
    """.split()
)
# RediSearch's indexer splits TEXT on punctuation (``demo:load:lock`` is indexed as
# demo, load, lock; ``k8s/base`` as k8s, base) and keeps underscores, so query terms
# are word-character runs: an escaped ``demo\:load\:lock`` would be one token that
# was never indexed and would match nothing. Word characters need no escaping.
_TERM = re.compile(r"\w+")
MAX_LEXICAL_TERMS = 24


def lexical_terms(question: str) -> list[str]:
    """The question's search terms: lowercase, no stopwords or 1-character tokens, deduped."""
    terms: list[str] = []
    for term in _TERM.findall(question.casefold()):
        if len(term) < 2 or term in STOPWORDS or term in terms:
            continue
        terms.append(term)
    return terms[:MAX_LEXICAL_TERMS]


def _filters(corpus: str, model_id: str, kinds: Iterable[str] | None) -> str:
    kind_filter = ""
    if kinds is not None:
        kinds = sorted(set(kinds))
        if not kinds or any(kind not in KINDS for kind in kinds):
            raise ValueError(f"kinds must be a non-empty subset of {KINDS}")
        kind_filter = f" @kind:{{{'|'.join(kinds)}}}"
    return f"@corpus:{{{corpus}}} @model:{{{_model_tag(model_id)}}}{kind_filter}"


def knn_query(corpus: str, model_id: str, top_k: int, kinds: Iterable[str] | None = None) -> str:
    """The FT.SEARCH KNN query: corpus and model tags, plus an optional ``kind`` filter."""
    return f"({_filters(corpus, model_id, kinds)})=>[KNN {top_k} @vector $vec AS distance]"


def lexical_query(
    corpus: str, model_id: str, terms: Sequence[str], kinds: Iterable[str] | None = None
) -> str:
    """The FT.SEARCH BM25 query: the same filters, the terms OR-joined on ``text``.

    ``FT.SEARCH`` ANDs bare terms, so a whole question would match nothing.
    """
    if not terms:
        raise ValueError("terms must not be empty")
    return f"{_filters(corpus, model_id, kinds)} @text:({'|'.join(terms)})"


# ------------------------------------------------------------------ the two legs


def _check(corpus: str, top_k: int) -> None:
    if top_k < 1:
        raise ValueError("top_k must be positive")
    if corpus not in CORPORA:
        raise ValueError("unknown corpus")


def _decode(value) -> str:
    return value.decode() if isinstance(value, bytes) else str(value)


def _rows(response) -> list[tuple[int, dict[str, str]]]:
    rows = []
    for key, fields in zip(response[1::2], response[2::2], strict=True):
        values = {_decode(k): v for k, v in zip(fields[::2], fields[1::2], strict=True)}
        rows.append((int(_decode(key).removeprefix("chunk:")), values))
    return rows


async def search_chunks(
    redis_client,
    embedding: list[float],
    corpus: str,
    model_id: str,
    top_k: int = 8,
    kinds: Iterable[str] | None = None,
) -> list[dict]:
    """The vector leg: chunk ids, cosine similarities and source paths, nearest first.

    ``kinds`` (optional) keeps only chunks whose ``kind`` tag is one of them
    (``redis_index.KINDS``); ``None`` means no kind filter. A key written before
    the ``kind`` field existed matches no kind filter until reconcile rewrites it.
    """
    if len(embedding) != VECTOR_DIMENSIONS:
        raise ValueError(f"embedding must have {VECTOR_DIMENSIONS} float32 values")
    _check(corpus, top_k)
    vector = struct.pack(f"{VECTOR_DIMENSIONS}f", *embedding)
    # Empty results after a provider switch flow to the API's no-sources answer.
    response = await redis_client.execute_command(
        "FT.SEARCH",
        INDEX_NAME,
        knn_query(corpus, model_id, top_k, kinds),
        "PARAMS",
        "2",
        "vec",
        vector,
        "SORTBY",
        "distance",
        "ASC",
        "RETURN",
        "2",
        "distance",
        "source_path",
        "LIMIT",
        "0",
        str(top_k),
        "DIALECT",
        "2",
    )
    return [
        {
            "chunk_id": chunk_id,
            "score": 1 - float(_decode(values["distance"])),
            "source_path": _decode(values.get("source_path", "")),
        }
        for chunk_id, values in _rows(response)
    ]


async def lexical_search(
    redis_client,
    terms: Sequence[str],
    corpus: str,
    model_id: str,
    top_k: int = 20,
    kinds: Iterable[str] | None = None,
) -> list[dict]:
    """The lexical leg: BM25 over ``text``, best first; ``[]`` for no terms.

    An index without the ``text`` field (a worker deployed before the ingest that
    migrates it) raises ``ResponseError``; :func:`hybrid_search` then serves the
    vector leg alone. Keys not yet backfilled simply don't match.
    """
    _check(corpus, top_k)
    if not terms:
        return []
    response = await redis_client.execute_command(
        "FT.SEARCH",
        INDEX_NAME,
        lexical_query(corpus, model_id, terms, kinds),
        "SCORER",
        "BM25",
        "RETURN",
        "1",
        "source_path",
        "LIMIT",
        "0",
        str(top_k),
        "DIALECT",
        "2",
    )
    return [
        {"chunk_id": chunk_id, "source_path": _decode(values.get("source_path", ""))}
        for chunk_id, values in _rows(response)
    ]


# ------------------------------------------------------------------ fusion


def rrf_fuse(
    legs: Sequence[Sequence[dict]], *, k: int = 60, weights: Sequence[float] | None = None
) -> list[dict]:
    """Reciprocal rank fusion: ``sum(weight / (k + rank))`` over the legs, best first.

    Each leg is a ranked list of dicts with ``chunk_id``. The first dict seen for a
    chunk is kept (so put the vector leg first to keep its cosine ``score``), plus
    ``rrf`` (the fused score) and ``ranks`` (1-based rank per leg, None if absent).
    Ties keep the order of first appearance.
    """
    weights = list(weights) if weights is not None else [1.0] * len(legs)
    if len(weights) != len(legs):
        raise ValueError("one weight per leg")
    fused: dict[int, dict] = {}
    for leg_index, leg in enumerate(legs):
        for rank, match in enumerate(leg, start=1):
            entry = fused.get(match["chunk_id"])
            if entry is None:
                entry = fused[match["chunk_id"]] = {
                    **match,
                    "rrf": 0.0,
                    "ranks": [None] * len(legs),
                }
            if entry["ranks"][leg_index] is None:
                entry["ranks"][leg_index] = rank
                entry["rrf"] += weights[leg_index] / (k + rank)
    return sorted(fused.values(), key=lambda entry: -entry["rrf"])


def select_chunks(
    fused: Sequence[dict],
    *,
    top_k: int,
    per_document_cap: int,
    slot_ids: Sequence[int] = (),
) -> list[dict]:
    """The final list: slotted chunks first, then fused order, at most ``per_document_cap``
    chunks per source file and ``top_k`` in all.

    ``slot_ids`` are chunk ids that must be included (the dual-experience slots);
    an id not in ``fused`` is ignored.
    """
    by_id = {entry["chunk_id"]: entry for entry in fused}
    selected: list[dict] = []
    per_document: dict[str, int] = {}

    def take(entry: dict) -> None:
        selected.append(entry)
        per_document[entry["source_path"]] = per_document.get(entry["source_path"], 0) + 1

    for chunk_id in slot_ids:
        if chunk_id in by_id and len(selected) < top_k:
            take(by_id[chunk_id])
    chosen = {entry["chunk_id"] for entry in selected}
    for entry in fused:
        if len(selected) >= top_k:
            break
        if entry["chunk_id"] in chosen:
            continue
        if per_document.get(entry["source_path"], 0) >= per_document_cap:
            continue
        take(entry)
    return selected


# ------------------------------------------------------------------ dual experience

# Technologies a "have you used X?" question can name: canonical name -> spellings
# matched in the question (word-bounded) and searched in chunk text. Each spelling
# is one search token (a multi-word name like "google cloud" would search for the
# generic words). Generic names only; extend when the corpus gains a technology.
TECH_ALIASES: dict[str, tuple[str, ...]] = {
    "redis": ("redis",),
    "python": ("python",),
    "react": ("react", "reactjs"),
    "kafka": ("kafka",),
    "grpc": ("grpc",),
    "kubernetes": ("kubernetes", "k8s", "k3s", "eks", "aks", "gke"),
    "terraform": ("terraform",),
    "docker": ("docker",),
    "typescript": ("typescript",),
    "javascript": ("javascript", "nodejs"),
    "java": ("java",),
    "csharp": ("csharp", "dotnet"),
    "golang": ("golang",),
    "rust": ("rust",),
    "sql": ("sql", "mysql", "postgres", "postgresql"),
    "aws": ("aws", "bedrock", "ec2"),
    "azure": ("azure",),
    "gcp": ("gcp",),
    "graphql": ("graphql",),
    "spark": ("spark",),
    "mongodb": ("mongodb",),
    "elasticsearch": ("elasticsearch",),
    "kusto": ("kusto",),
    "rag": ("rag",),
    "llm": ("llm", "llms"),
    "swift": ("swift",),
    "kotlin": ("kotlin",),
    "android": ("android",),
    "fastapi": ("fastapi",),
    "django": ("django",),
    "flask": ("flask",),
}
_ALIAS_PATTERNS = {
    name: re.compile(r"\b(?:" + "|".join(aliases) + r")\b")
    for name, aliases in TECH_ALIASES.items()
}
# A question about having used something: an experience verb or phrase, or a
# production/work framing ("Kubernetes in production?").
_EXPERIENCE_CUE = re.compile(
    r"\b(use[ds]?|using|worked|work with|experience[ds]?|familiar|know|knows|proficient|"
    r"skilled|comfortable|built|build|run|ran|deploy(?:ed)?|written|wrote|code[ds]? in|"
    r"program(?:med)? in|production|professionally|at work|on the job|expert(?:ise)?|"
    r"skills?)\b"
)
# Short bare questions ("Kafka?", "Redis experience") count without a cue.
_SHORT_QUESTION_WORDS = 3


def tech_question_terms(question: str) -> list[str]:
    """The lexical terms of the technologies a "have you used X?" question names, or [].

    Fires only when the question names a known technology (``TECH_ALIASES``) and
    either has an experience cue or is at most three words long; a question like
    "Where do you work?" or "What do you do for fun?" names none.
    """
    text = question.casefold()
    named = [name for name, pattern in _ALIAS_PATTERNS.items() if pattern.search(text)]
    if not named:
        return []
    if not _EXPERIENCE_CUE.search(text) and len(text.split()) > _SHORT_QUESTION_WORDS:
        return []
    return [alias for name in named for alias in TECH_ALIASES[name]]


def dual_experience_slots(fused: Sequence[dict], tech_hits: Sequence[dict]) -> list[int]:
    """The best professional and the best personal-project chunk naming the technology.

    ``tech_hits`` are the chunks whose text matches the technology terms (a lexical
    search for them alone). Each side takes its best chunk by fused rank (chunks
    outside the fused list rank after it, in lexical order). A side the data
    doesn't have gets no slot, so no unrelated chunk is forced in.
    """
    order = {entry["chunk_id"]: index for index, entry in enumerate(fused)}
    ranked = sorted(
        enumerate(tech_hits), key=lambda item: (order.get(item[1]["chunk_id"], math.inf), item[0])
    )
    slots: list[int] = []
    for side in (PROFESSIONAL, PERSONAL_PROJECT):
        for _, hit in ranked:
            if experience_of(hit["source_path"]) == side:
                slots.append(hit["chunk_id"])
                break
    return slots


# ------------------------------------------------------------------ hybrid


async def hybrid_search(
    redis_client,
    embedding: list[float],
    question: str,
    corpus: str,
    model_id: str,
    config: RetrievalConfig | None = None,
    *,
    legs: dict | None = None,
) -> list[dict]:
    """Production retrieval: vector and BM25 legs, RRF, slots, per-document cap.

    Returns ``chunk_id``, ``score`` (cosine similarity to the question, the number
    the diagram shows), ``source_path``, ``rrf`` and ``ranks``. A chunk found only
    by the lexical leg gets its cosine from its stored vector. If the lexical leg
    fails (no ``text`` field yet), the vector leg is served alone. ``legs``, when
    given, receives each leg's raw list (for the retrieval eval).
    """
    config = config or RETRIEVAL_CONFIGS.get(corpus) or RetrievalConfig(top_k=8, per_document_cap=3)
    vector_leg = await search_chunks(
        redis_client, embedding, corpus, model_id, top_k=config.candidates
    )
    terms = lexical_terms(question)
    tech_terms = tech_question_terms(question) if config.dual_experience else []
    try:
        lexical_leg = await lexical_search(
            redis_client, terms, corpus, model_id, top_k=config.candidates
        )
        tech_hits = (
            await lexical_search(
                redis_client, tech_terms, corpus, model_id, top_k=config.candidates
            )
            if tech_terms
            else []
        )
    except ResponseError:
        LOGGER.warning("lexical search failed; serving vector results only", exc_info=True)
        lexical_leg, tech_hits = [], []
    fused = rrf_fuse(
        [vector_leg, lexical_leg], k=config.rrf_k, weights=[1.0, config.lexical_weight]
    )
    slots = dual_experience_slots(fused, tech_hits)
    # A tech hit outside both legs can still fill its slot.
    known = {entry["chunk_id"] for entry in fused}
    fused += [
        {**hit, "rrf": 0.0, "ranks": [None, None]}
        for hit in tech_hits
        if hit["chunk_id"] in slots and hit["chunk_id"] not in known
    ]
    selected = select_chunks(
        fused, top_k=config.top_k, per_document_cap=config.per_document_cap, slot_ids=slots
    )
    await _fill_scores(redis_client, selected, embedding)
    if legs is not None:
        legs.update(vector=vector_leg, lexical=lexical_leg, tech_terms=tech_terms, slots=slots)
    return selected


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    norms = math.sqrt(sum(a * a for a in left)) * math.sqrt(sum(b * b for b in right))
    return dot / norms if norms else 0.0


async def _fill_scores(redis_client, selected: list[dict], embedding: list[float]) -> None:
    missing = [entry for entry in selected if "score" not in entry]
    if not missing:
        return
    async with redis_client.pipeline(transaction=False) as pipeline:
        for entry in missing:
            pipeline.hget(f"chunk:{entry['chunk_id']}", "vector")
        vectors = await pipeline.execute()
    for entry, packed in zip(missing, vectors, strict=True):
        if isinstance(packed, bytes) and len(packed) == VECTOR_DIMENSIONS * 4:
            entry["score"] = _cosine(embedding, struct.unpack(f"{VECTOR_DIMENSIONS}f", packed))
        else:
            entry["score"] = 0.0
