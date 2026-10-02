"""One corpus list for the API, retrieval, ingest and the warm-up (portfolio spec §6.2)."""

import pytest

from services.glassbox import corpora
from services.glassbox.api.ask import AskRequest
from services.glassbox.ingest import sweep
from services.glassbox.retrieval.search import search_chunks


def test_portfolio_is_the_third_corpus():
    assert corpora.CORPORA == ("about_me", "about_system", "portfolio")
    assert sweep.CORPORA is corpora.CORPORA


def test_ask_request_accepts_every_corpus_and_nothing_else():
    for corpus in corpora.CORPORA:
        assert AskRequest(question="What did Basel build?", corpus=corpus).corpus == corpus
    with pytest.raises(ValueError):
        AskRequest(question="Hi?", corpus="about_you")


class _CapturingRedis:
    def __init__(self):
        self.args = None

    async def execute_command(self, *args):
        self.args = args
        return [0]  # FT.SEARCH reply with zero matches


@pytest.mark.asyncio
async def test_search_filters_portfolio_by_its_own_tag():
    client = _CapturingRedis()
    assert await search_chunks(client, [0.0] * 512, "portfolio", "fake-v1") == []
    assert "@corpus:{portfolio}" in client.args[2]


@pytest.mark.asyncio
async def test_search_rejects_an_unknown_corpus():
    with pytest.raises(ValueError, match="unknown corpus"):
        await search_chunks(_CapturingRedis(), [0.0] * 512, "about_you", "fake-v1")
