"""Prompt v22: About Basel also answers from the portfolio files (owner, 2026-10-09).

About Basel retrieval searches ``about_me`` and ``portfolio`` together; each
portfolio source names its project, is never planned-marked, and keeps its public
path in the browser; a rule keeps a project's facts with that project.
"""

import pytest

from services.glassbox import corpora
from services.glassbox.api.ask import (
    PLANNED_MARK,
    WorkerChunk,
    _prompt,
    _public_chunk,
)
from services.glassbox.cache.answer import _model_tag
from services.glassbox.retrieval.search import knn_query, lexical_query
from services.glassbox.worker.main import search_version


def _chunk(n: int, path: str, text: str, title: str = "GoalBuddy") -> WorkerChunk:
    return WorkerChunk(n=n, chunk_id=n, text=text, source_path=path, title=title, score=0.8)


def test_about_basel_searches_the_portfolio_too():
    assert corpora.search_corpora("about_me") == ("about_me", "portfolio")
    assert corpora.search_corpora("about_system") == ("about_system",)
    assert corpora.search_corpora("portfolio") == ("portfolio",)
    assert set(corpora.SEARCH_CORPORA) == set(corpora.CORPORA)
    tag = _model_tag("m")
    assert knn_query("about_me", "m", 8).startswith(
        f"(@corpus:{{about_me|portfolio}} @model:{{{tag}}})"
    )
    assert lexical_query("about_me", "m", ["goalbuddy"]).startswith("@corpus:{about_me|portfolio} ")
    # About This System never sees project write-ups.
    assert "portfolio" not in knn_query("about_system", "m", 8)


class _Versions:
    def __init__(self, versions: dict[str, int]):
        self.versions = versions

    async def version(self, corpus: str) -> int:
        return self.versions.get(corpus, 0)


@pytest.mark.asyncio
async def test_retrieval_cache_version_follows_every_searched_corpus():
    cache = _Versions({"about_me": 3, "about_system": 7, "portfolio": 2})
    assert await search_version(cache, "about_me") == 5
    assert await search_version(cache, "about_system") == 7
    # A portfolio ingest retires About Basel's cached results, not About This System's.
    cache.versions["portfolio"] += 1
    assert await search_version(cache, "about_me") == 6
    assert await search_version(cache, "about_system") == 7


def test_portfolio_sources_name_their_project_and_are_never_planned_marked():
    prompt = _prompt(
        "What is GoalBuddy?",
        [
            _chunk(
                1,
                "corpus/portfolio/goalbuddy.md",
                "## GoalBuddy: what I built\nQueues on SQS, deferred.",
            ),
            _chunk(2, "corpus/portfolio/goalbuddy.md", "# GoalBuddy\nA mobile app."),
        ],
        corpus="about_me",
    )
    assert "[1] portfolio project (GoalBuddy · GoalBuddy: what I built): " in prompt
    assert "[2] portfolio project (GoalBuddy): # GoalBuddy" in prompt
    assert f"{PLANNED_MARK} Queues" not in prompt
    assert "Queues on SQS, deferred." in prompt
    assert "corpus/portfolio/" not in prompt


def test_project_scope_rule_only_with_a_portfolio_source():
    portfolio = _chunk(1, "corpus/portfolio/goalbuddy.md", "# GoalBuddy\nA mobile app.")
    private = _chunk(2, "private/projects.md", "## This site\nA RAG chatbot.", title="Projects")
    with_project = _prompt("What have you built?", [portfolio, private], corpus="about_me")
    assert "never give it to another project or to this site" in with_project
    assert "never to a portfolio project" in with_project
    without = _prompt("What have you built?", [private], corpus="about_me")
    assert "never to a portfolio project" not in without


def test_project_name_falls_back_to_the_slug_when_the_title_is_a_path():
    prompt = _prompt(
        "q?", [_chunk(1, "corpus/portfolio/goal-buddy.md", "body", title="goal-buddy.md")]
    )
    assert "[1] portfolio project (goal-buddy): body" in prompt


def test_about_basel_keeps_public_portfolio_paths_and_hides_private_ones():
    portfolio = _public_chunk(
        _chunk(1, "corpus/portfolio/goalbuddy.md", "## GoalBuddy: what I built\nx"), "about_me"
    )
    assert portfolio["source_path"] == "corpus/portfolio/goalbuddy.md"
    assert portfolio["title"] == "GoalBuddy"
    private = _public_chunk(
        _chunk(2, "private/projects.md", "## This site\nx", title="Projects"), "about_me"
    )
    assert private["source_path"] == "This site"
    assert "private/" not in str(private)
