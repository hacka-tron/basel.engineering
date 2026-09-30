"""SSE framing, heartbeats, and disconnect handling for streamed answers."""

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator, Awaitable, Callable

# DESIGN-002 §7.4 / §9.2: a comment line that keeps proxies from idling out a quiet
# stream. Every SSE parser (ours included) ignores lines starting with ':'.
PING_FRAME = ": ping\n\n"

# Cleanup tasks outlive the response that spawned them; keep strong references so
# they are not garbage-collected mid-flight.
_CLEANUP_TASKS: set[asyncio.Task] = set()


def frame(event: str, payload: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, separators=(',', ':'))}\n\n"


_END = object()


async def _next(iterator: AsyncIterator[str]) -> object:
    try:
        return await anext(iterator)
    except StopAsyncIteration:
        return _END


async def _close(pending: asyncio.Task | None, iterator: AsyncIterator[str]) -> None:
    if pending is not None:
        pending.cancel()
        with contextlib.suppress(Exception, asyncio.CancelledError):
            await pending
    aclose = getattr(iterator, "aclose", None)
    if aclose is not None:
        with contextlib.suppress(Exception):
            await aclose()


async def with_heartbeat(
    source: AsyncIterator[str],
    *,
    interval_s: float,
    is_disconnected: Callable[[], Awaitable[bool]] | None = None,
    poll_interval_s: float = 1.0,
) -> AsyncIterator[str]:
    """Relay ``source`` unchanged, adding a ping whenever nothing was sent for ``interval_s``.

    The next source item is awaited in its own task and raced against a timer, so
    events are never dropped or reordered: a ping only goes out while the source is
    still working on its next item (retrieval wait, cold LLM call, slow tokens).

    Stopping (DESIGN-002 §6.2): when the client goes away, the source is cancelled
    and closed so generation stops instead of spending tokens for nobody. Starlette
    surfaces a disconnect by cancelling this generator (or closing it); as a
    fallback that does not depend on the server version, ``is_disconnected`` is
    polled every ``poll_interval_s`` while waiting. Cleanup runs in a separate task
    because awaits inside a cancelled response scope would be cancelled again.
    """
    loop = asyncio.get_running_loop()
    iterator = aiter(source)
    pending: asyncio.Task | None = None
    finished = False
    last_sent = loop.time()
    try:
        while True:
            if pending is None:
                pending = asyncio.create_task(_next(iterator))
            wait_s = max(0.0, last_sent + interval_s - loop.time())
            if is_disconnected is not None:
                wait_s = min(wait_s, poll_interval_s)
            done, _ = await asyncio.wait({pending}, timeout=wait_s)
            if done:
                task, pending = pending, None
                finished = True  # Until proven otherwise: result() may raise.
                item = task.result()
                if item is _END:
                    return
                finished = False
                yield item
                last_sent = loop.time()
                continue
            if is_disconnected is not None and await is_disconnected():
                return
            if loop.time() - last_sent >= interval_s:
                yield PING_FRAME
                last_sent = loop.time()
    finally:
        if not finished:
            task = asyncio.get_running_loop().create_task(_close(pending, iterator))
            _CLEANUP_TASKS.add(task)
            task.add_done_callback(_CLEANUP_TASKS.discard)
