"""Deterministic local provider implementations."""

import hashlib
from collections.abc import AsyncIterator

from services.glassbox.providers.base import (
    ABSTENTION_ANSWER,
    REWRITE_FOLLOW_UP_PREFIX,
    REWRITE_PROMPT_SUFFIX,
    EmbeddingProvider,
    LLMProvider,
)


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


# Put this in a question to make the fake answer abstain, so the refusal path
# (never cached) can be exercised locally without a real model.
FAKE_ABSTAIN_MARKER = "[fake-abstain]"


class FakeLLMProvider(LLMProvider):
    model_id = "fake-llm-v1"

    async def generate(
        self,
        prompt: str,
        *,
        max_tokens: int,
        system: str | None = None,
        temperature: float | None = None,
    ) -> AsyncIterator[str]:
        if prompt.rstrip().endswith(REWRITE_PROMPT_SUFFIX):
            # Follow-up rewrite: an identity rewrite keeps local retrieval and the
            # displayed "searched for" text meaningful instead of a canned sentence.
            follow_ups = [
                line.removeprefix(REWRITE_FOLLOW_UP_PREFIX).strip()
                for line in prompt.splitlines()
                if line.startswith(REWRITE_FOLLOW_UP_PREFIX)
            ]
            if follow_ups:
                yield follow_ups[-1]
                return
        question = prompt.rsplit("Question:", 1)[-1]
        if FAKE_ABSTAIN_MARKER in question:
            yield ABSTENTION_ANSWER
            return
        response = "This is a fake response for local development."
        for index, word in enumerate(response.split()[: max(0, max_tokens)]):
            yield word if index == 0 else f" {word}"
