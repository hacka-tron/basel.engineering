import pytest

from services.glassbox.providers.fake import FakeEmbeddingProvider, FakeLLMProvider


@pytest.mark.asyncio
async def test_fake_embedding_returns_one_512_float_vector_per_text_in_order():
    vectors = await FakeEmbeddingProvider().embed(["alpha", "beta", "alpha"])

    assert len(vectors) == 3
    assert all(len(vector) == 512 for vector in vectors)
    assert all(isinstance(value, float) for vector in vectors for value in vector)
    assert vectors[0] == vectors[2]


@pytest.mark.asyncio
async def test_fake_embedding_is_identical_across_separate_calls():
    provider = FakeEmbeddingProvider()

    first = await provider.embed(["same text"])
    second = await provider.embed(["same text"])

    assert first == second


@pytest.mark.asyncio
async def test_fake_embedding_differs_for_different_texts():
    first, second = await FakeEmbeddingProvider().embed(["alpha", "beta"])

    assert first != second


@pytest.mark.asyncio
async def test_fake_llm_streams_multiple_chunks_of_nonempty_text():
    chunks = [chunk async for chunk in FakeLLMProvider().generate("Any prompt", max_tokens=50)]

    assert len(chunks) > 1
    assert "".join(chunks).strip()


@pytest.mark.asyncio
async def test_fake_llm_echoes_follow_up_for_rewrite_prompts():
    prompt = (
        "Rewrite ...\n\nConversation:\nUser: What did Basel do at YouTube?\n\n"
        "Follow-up question: tell me more about that\nStandalone question:"
    )
    chunks = [chunk async for chunk in FakeLLMProvider().generate(prompt, max_tokens=60)]
    assert "".join(chunks) == "tell me more about that"


@pytest.mark.asyncio
async def test_fake_llm_accepts_system_override():
    chunks = [
        chunk async for chunk in FakeLLMProvider().generate("Any prompt", max_tokens=50, system="x")
    ]
    assert "".join(chunks).strip()
