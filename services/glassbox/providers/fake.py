"""Deterministic local provider implementations."""

import hashlib
from collections.abc import AsyncIterator

from services.glassbox.providers.base import EmbeddingProvider, LLMProvider


class FakeEmbeddingProvider(EmbeddingProvider):
    model_id = "fake-v1"

    async def embed(self, texts: list[str]) -> list[list[float]]:
        vectors = []
        for text in texts:
            seed = hashlib.sha256(text.encode("utf-8")).digest()
            vector = []
            # 64 counter-hashed blocks provide 8 unsigned 32-bit values each.
            for counter in range(64):
                block = hashlib.sha256(seed + counter.to_bytes(4, "big")).digest()
                vector.extend(
                    int.from_bytes(block[offset : offset + 4], "big") / 2**32
                    for offset in range(0, 32, 4)
                )
            vectors.append(vector)
        return vectors


class FakeLLMProvider(LLMProvider):
    async def generate(self, prompt: str, *, max_tokens: int) -> AsyncIterator[str]:
        response = "This is a fake response for local development."
        for index, word in enumerate(response.split()[: max(0, max_tokens)]):
            yield word if index == 0 else f" {word}"
