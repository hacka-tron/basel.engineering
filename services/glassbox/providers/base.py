"""Interfaces for Glassbox embedding and text generation providers."""

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator


class EmbeddingProvider(ABC):
    # Persisted with every chunk; changing this identity requires re-embedding.
    model_id: str

    @abstractmethod
    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Return a 512-float vector for each input text in order."""


class LLMProvider(ABC):
    # Included in answer-cache identity; changing models must not replay old answers.
    model_id: str

    @abstractmethod
    async def generate(self, prompt: str, *, max_tokens: int) -> AsyncIterator[str]:
        """Stream generated text chunks for a prompt."""
        yield ""
