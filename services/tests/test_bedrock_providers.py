"""Bedrock provider contracts without paid model calls."""

import json

import pytest

from services.glassbox.providers.bedrock import BedrockEmbeddingProvider, BedrockLLMProvider
from services.glassbox.providers.factory import get_embedding_provider, get_llm_provider
from services.glassbox.providers.fake import FakeEmbeddingProvider, FakeLLMProvider


class Body:
    def __init__(self, value):
        self.value = value

    def read(self):
        return json.dumps(self.value).encode()


class StubClient:
    def __init__(self):
        self.embedding_requests = []
        self.generation_requests = []

    def invoke_model(self, **kwargs):
        self.embedding_requests.append(kwargs)
        text = json.loads(kwargs["body"])["inputText"]
        return {"body": Body({"embedding": [float(len(text))] * 512})}

    def converse_stream(self, **kwargs):
        self.generation_requests.append(kwargs)
        return {
            "stream": iter(
                [
                    {"messageStart": {"role": "assistant"}},
                    {"contentBlockDelta": {"delta": {"text": "Hello"}}},
                    {"contentBlockDelta": {"delta": {"text": " world"}}},
                    {"metadata": {"usage": {"inputTokens": 5, "outputTokens": 2}}},
                ]
            )
        }


@pytest.mark.asyncio
async def test_titan_v2_requests_512_normalized_dimensions_and_preserves_order():
    client = StubClient()
    provider = BedrockEmbeddingProvider(client=client, model_id="amazon.titan-embed-text-v2:0")

    assert await provider.embed([]) == []
    assert [vector[0] for vector in await provider.embed(["a", "alphabet"])] == [1.0, 8.0]
    assert len(client.embedding_requests) == 2
    for request in client.embedding_requests:
        assert request["modelId"] == "amazon.titan-embed-text-v2:0"
        assert json.loads(request["body"]) == {
            "inputText": json.loads(request["body"])["inputText"],
            "dimensions": 512,
            "normalize": True,
        }


@pytest.mark.asyncio
async def test_titan_rejects_malformed_vector():
    class BadClient(StubClient):
        def invoke_model(self, **kwargs):
            return {"body": Body({"embedding": [0.1]})}

    with pytest.raises(ValueError, match="512"):
        await BedrockEmbeddingProvider(client=BadClient()).embed(["question"])


@pytest.mark.asyncio
async def test_haiku_streams_deltas_with_bounded_tokens_and_grounding_instruction():
    client = StubClient()
    provider = BedrockLLMProvider(
        client=client, model_id="us.anthropic.claude-haiku-4-5-20251001-v1:0"
    )

    assert [part async for part in provider.generate("Source [1]: text", max_tokens=50)] == [
        "Hello",
        " world",
    ]
    request = client.generation_requests[0]
    assert request["modelId"] == provider.model_id
    assert request["inferenceConfig"]["maxTokens"] == 50
    assert request["messages"] == [{"role": "user", "content": [{"text": "Source [1]: text"}]}]
    assert "bracketed citation markers" in request["system"][0]["text"].lower()


@pytest.mark.asyncio
async def test_generate_uses_system_override_when_given():
    client = StubClient()
    provider = BedrockLLMProvider(client=client)
    parts = [part async for part in provider.generate("q", max_tokens=60, system="Rewrite only.")]
    assert parts == ["Hello", " world"]
    assert client.generation_requests[0]["system"] == [{"text": "Rewrite only."}]


@pytest.mark.asyncio
async def test_haiku_rejects_more_than_400_output_tokens():
    with pytest.raises(ValueError, match="400"):
        await anext(BedrockLLMProvider(client=StubClient()).generate("prompt", max_tokens=401))


def test_provider_factory_defaults_to_fake_and_rejects_unknown_mode(monkeypatch):
    monkeypatch.delenv("GLASSBOX_PROVIDER", raising=False)
    assert isinstance(get_embedding_provider(), FakeEmbeddingProvider)
    assert isinstance(get_llm_provider(), FakeLLMProvider)
    monkeypatch.setenv("GLASSBOX_PROVIDER", "invalid")
    with pytest.raises(ValueError, match="GLASSBOX_PROVIDER"):
        get_embedding_provider()


def test_provider_factory_selects_bedrock(monkeypatch):
    monkeypatch.setenv("GLASSBOX_PROVIDER", "bedrock")
    monkeypatch.setenv("BEDROCK_EMBEDDING_MODEL_ID", "amazon.titan-embed-text-v2:0")
    monkeypatch.setenv("BEDROCK_LLM_MODEL_ID", "us.anthropic.claude-haiku-4-5-20251001-v1:0")
    assert isinstance(get_embedding_provider(), BedrockEmbeddingProvider)
    assert isinstance(get_llm_provider(), BedrockLLMProvider)
