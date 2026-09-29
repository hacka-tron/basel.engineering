"""Choose both providers together so embeddings share one vector space."""

import os
from functools import lru_cache

from services.glassbox.providers.base import EmbeddingProvider, LLMProvider
from services.glassbox.providers.fake import FakeEmbeddingProvider, FakeLLMProvider


def _mode() -> str:
    mode = os.getenv("GLASSBOX_PROVIDER", "fake").lower()
    if mode not in {"fake", "bedrock"}:
        raise ValueError("GLASSBOX_PROVIDER must be 'fake' or 'bedrock'")
    return mode


@lru_cache(maxsize=8)
def _bedrock_embedding(model_id: str, region: str) -> EmbeddingProvider:
    from services.glassbox.providers.bedrock import BedrockEmbeddingProvider

    return BedrockEmbeddingProvider(model_id=model_id, region=region)


@lru_cache(maxsize=8)
def _bedrock_llm(model_id: str, region: str) -> LLMProvider:
    from services.glassbox.providers.bedrock import BedrockLLMProvider

    return BedrockLLMProvider(model_id=model_id, region=region)


def get_embedding_provider() -> EmbeddingProvider:
    if _mode() == "fake":
        return FakeEmbeddingProvider()
    from services.glassbox.providers.bedrock import DEFAULT_EMBEDDING_MODEL

    return _bedrock_embedding(
        os.getenv("BEDROCK_EMBEDDING_MODEL_ID", DEFAULT_EMBEDDING_MODEL),
        os.getenv("AWS_REGION", "us-east-1"),
    )


def get_llm_provider() -> LLMProvider:
    if _mode() == "fake":
        return FakeLLMProvider()
    from services.glassbox.providers.bedrock import DEFAULT_LLM_MODEL

    return _bedrock_llm(
        os.getenv("BEDROCK_LLM_MODEL_ID", DEFAULT_LLM_MODEL),
        os.getenv("AWS_REGION", "us-east-1"),
    )
