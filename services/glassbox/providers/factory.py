"""Choose both providers together so embeddings share one vector space."""

import os
import re
from functools import lru_cache

from services.glassbox.providers.base import EmbeddingProvider, LLMProvider
from services.glassbox.providers.fake import FakeEmbeddingProvider, FakeLLMProvider


def _mode() -> str:
    mode = os.getenv("GLASSBOX_PROVIDER", "fake").lower()
    if mode not in {"fake", "bedrock"}:
        raise ValueError("GLASSBOX_PROVIDER must be 'fake' or 'bedrock'")
    return mode


def _validate_bedrock_model_id(name: str, model_id: str) -> None:
    # Bedrock model and inference-profile IDs contain a provider separator;
    # reject empty values, whitespace, and malformed punctuation at startup.
    if not re.fullmatch(r"[a-z0-9][a-z0-9._:-]*", model_id) or "." not in model_id:
        raise ValueError(f"{name} must be a Bedrock model or inference-profile ID")


def validate_provider_config() -> None:
    """Validate local configuration without making a paid Bedrock request."""
    mode = _mode()
    raw_cap = os.getenv("GLASSBOX_DAILY_LLM_CAP")
    if raw_cap is not None:
        try:
            cap = int(raw_cap)
        except ValueError as exc:
            raise ValueError("GLASSBOX_DAILY_LLM_CAP must be a positive integer") from exc
        if cap <= 0:
            raise ValueError("GLASSBOX_DAILY_LLM_CAP must be a positive integer")
    if mode == "bedrock":
        from services.glassbox.providers.bedrock import (
            DEFAULT_EMBEDDING_MODEL,
            DEFAULT_LLM_MODEL,
        )

        _validate_bedrock_model_id(
            "BEDROCK_EMBEDDING_MODEL_ID",
            os.getenv("BEDROCK_EMBEDDING_MODEL_ID", DEFAULT_EMBEDDING_MODEL),
        )
        _validate_bedrock_model_id(
            "BEDROCK_LLM_MODEL_ID", os.getenv("BEDROCK_LLM_MODEL_ID", DEFAULT_LLM_MODEL)
        )
    get_embedding_provider()
    get_llm_provider()


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
