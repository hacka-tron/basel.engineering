"""Bedrock implementations of the local provider contracts."""

import asyncio
import json
import math
from collections.abc import AsyncIterator

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from services.glassbox.providers.base import (
    GROUNDING_RULES,
    ContentFilteredError,
    EmbeddingProvider,
    LLMAccessDeniedError,
    LLMProvider,
)

# What Bedrock Runtime returns when IAM denies the call, for example once the AWS
# budget action has attached the answer-model deny policy (budget.tf).
_ACCESS_DENIED_CODES = {"AccessDeniedException"}

DEFAULT_EMBEDDING_MODEL = "amazon.titan-embed-text-v2:0"
DEFAULT_LLM_MODEL = "us.amazon.nova-lite-v1:0"
# Output-token guard for generate(); api/ask.py _ANSWER_MAX_TOKENS must stay within it.
MAX_OUTPUT_TOKENS = 600
# Text Nova streams before a content_filtered stop (seen 2026-10-03).
_FILTER_NOTICE = "The generated text has been blocked by our content filters."


def _could_be_filter_notice(text: str) -> bool:
    """True while ``text`` is still a prefix of Nova's filter notice (or vice versa)."""
    candidate = text.lstrip(" -\n")
    return _FILTER_NOTICE.startswith(candidate) or candidate.startswith(_FILTER_NOTICE)


def _client(region: str):
    return boto3.client(
        "bedrock-runtime",
        region_name=region,
        config=Config(retries={"max_attempts": 5, "mode": "adaptive"}),
    )


def _next_event(iterator):
    try:
        return True, next(iterator)
    except StopIteration:
        return False, None


def _close_stream(response) -> None:
    closer = getattr(response.get("stream"), "close", None)
    if closer is not None:
        closer()


def _close_orphaned_response(opening: asyncio.Future) -> None:
    """Close a converse_stream response that arrived after its caller was cancelled."""
    if opening.cancelled() or opening.exception() is not None:
        return
    # Closing drops the HTTP connection, which ends generation and its billing.
    asyncio.get_running_loop().run_in_executor(None, _close_stream, opening.result())


def _invoke_embedding(client, model_id: str, value: str):
    response = client.invoke_model(
        modelId=model_id,
        body=json.dumps({"inputText": value, "dimensions": 512, "normalize": True}),
        contentType="application/json",
        accept="application/json",
    )
    return json.loads(response["body"].read())


class BedrockEmbeddingProvider(EmbeddingProvider):
    def __init__(
        self, *, client=None, model_id: str = DEFAULT_EMBEDDING_MODEL, region: str = "us-east-1"
    ):
        self.client = client if client is not None else _client(region)
        self.model_id = model_id

    async def embed(self, texts: list[str]) -> list[list[float]]:
        vectors = []
        for value in texts:
            if not value.strip():
                raise ValueError("embedding input must be non-empty")
            data = await asyncio.to_thread(_invoke_embedding, self.client, self.model_id, value)
            vector = data.get("embedding")
            if (
                not isinstance(vector, list)
                or len(vector) != 512
                or any(
                    isinstance(number, bool)
                    or not isinstance(number, int | float)
                    or not math.isfinite(number)
                    for number in vector
                )
            ):
                raise ValueError("Bedrock embedding response must contain 512 finite floats")
            vectors.append([float(number) for number in vector])
        return vectors


# Prompt v17: 0 (was 0.2) for answers, rewrites and judges alike. Grounded answers
# gain nothing from sampling variety, and repeat runs of the golden set get closer.
# The v18 casual-tone prompt passes its own (api/ask.py CASUAL_TEMPERATURE).
TEMPERATURE = 0.0


class BedrockLLMProvider(LLMProvider):
    reports_usage = True

    def __init__(
        self, *, client=None, model_id: str = DEFAULT_LLM_MODEL, region: str = "us-east-1"
    ):
        self.client = client if client is not None else _client(region)
        self.model_id = model_id

    async def generate(
        self,
        prompt: str,
        *,
        max_tokens: int,
        system: str | None = None,
        usage: dict | None = None,
        temperature: float | None = None,
    ) -> AsyncIterator[str]:
        """Stream text deltas; ``usage`` receives Bedrock's token counts if the
        stream reaches its final metadata event (it does not when stopped early)."""
        if not 1 <= max_tokens <= MAX_OUTPUT_TOKENS:
            raise ValueError(f"max_tokens must be between 1 and {MAX_OUTPUT_TOKENS}")
        # The call runs in a worker thread that cannot be interrupted. If the
        # client stops while it is still waiting for response headers, the
        # cancellation skips the `finally` below, so the stream that arrives
        # later would have no owner and keep generating. Shield the call and,
        # on cancellation, close whatever it returns once it returns.
        opening = asyncio.ensure_future(
            asyncio.to_thread(
                self.client.converse_stream,
                modelId=self.model_id,
                system=[{"text": GROUNDING_RULES if system is None else system}],
                messages=[{"role": "user", "content": [{"text": prompt}]}],
                inferenceConfig={
                    "maxTokens": max_tokens,
                    "temperature": TEMPERATURE if temperature is None else temperature,
                },
            )
        )
        try:
            response = await asyncio.shield(opening)
        except asyncio.CancelledError:
            opening.add_done_callback(_close_orphaned_response)
            raise
        except ClientError as exc:
            # IAM refuses before any output: a typed error the API turns into its
            # retrieval-only answer. Other errors (throttling, validation) stay as
            # they are.
            if exc.response.get("Error", {}).get("Code") in _ACCESS_DENIED_CODES:
                raise LLMAccessDeniedError(f"Bedrock access denied for {self.model_id}") from exc
            raise
        stream = response["stream"]
        iterator = iter(stream)
        held: str | None = ""  # None once the opening text is known not to be the notice
        try:
            while True:
                found, event = await asyncio.to_thread(_next_event, iterator)
                if not found:
                    break
                if "contentBlockDelta" in event:
                    part = event["contentBlockDelta"].get("delta", {}).get("text")
                    if part and held is not None:
                        # Hold back the opening text while it could still be Nova's
                        # filter notice, so a filtered answer never shows it.
                        held += part
                        if _could_be_filter_notice(held):
                            continue
                        part, held = held, None
                    if part:
                        yield part
                elif "metadata" in event and usage is not None:
                    reported = event["metadata"].get("usage", {})
                    usage.update(
                        {
                            key: reported[key]
                            for key in ("inputTokens", "outputTokens")
                            if isinstance(reported.get(key), int)
                        }
                    )
                elif "messageStop" in event:
                    reason = event["messageStop"].get("stopReason")
                    if reason == "content_filtered":
                        raise ContentFilteredError("Bedrock generation stopped: content_filtered")
                    if reason not in {"end_turn", "max_tokens", "stop_sequence"}:
                        raise RuntimeError(f"Bedrock generation stopped: {reason}")
            if held:
                yield held
        finally:
            closer = getattr(stream, "close", None)
            if closer is not None:
                await asyncio.to_thread(closer)
