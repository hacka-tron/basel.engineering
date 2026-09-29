"""Shared Redis sequence and request timing conventions for trace events."""

import time


async def next_seq(redis_client, request_id: str) -> int:
    key = f"seq:{request_id}"
    sequence = await redis_client.incr(key)
    await redis_client.expire(key, 300)
    return sequence


def elapsed_ms(request_start_ts: int) -> int:
    return max(0, int(time.time() * 1000) - request_start_ts)
