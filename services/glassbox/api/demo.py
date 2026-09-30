"""Stress-test endpoint: builds retrieval-queue backlog for the KEDA demo (DESIGN.md §9.4)."""

import os

import redis.asyncio as redis
from fastapi import APIRouter

from services.glassbox.api.capacity import LOCK_KEY, assess_capacity
from services.glassbox.worker.main import enqueue_synthetic_jobs

router = APIRouter()

LOCK_TTL_S = 300
SYNTHETIC_JOB_COUNT = 300
SYNTHETIC_JOB_DELAY_MS = 200


@router.post("/api/demo/load")
async def demo_load() -> dict:
    """Enqueue synthetic retrieval jobs so KEDA scales `retrieval-worker` up.

    Global cooldown via a Redis `SET NX EX` lock: while the lock holds, this
    returns the remaining cooldown instead of enqueueing anything, so the
    button can show a countdown rather than re-triggering the burst.
    """
    capacity = await assess_capacity()
    if not capacity["sufficient"]:
        return {"started": False, "enqueued": 0, "retry_after_s": 0, "reason": capacity["reason"]}
    client = redis.from_url(os.environ["REDIS_URL"])
    try:
        acquired = await client.set(LOCK_KEY, "1", nx=True, ex=LOCK_TTL_S)
        if not acquired:
            ttl = await client.ttl(LOCK_KEY)
            return {"started": False, "retry_after_s": max(0, ttl), "enqueued": 0}
        enqueued = await enqueue_synthetic_jobs(
            client, count=SYNTHETIC_JOB_COUNT, simulated_delay_ms=SYNTHETIC_JOB_DELAY_MS
        )
        return {"started": True, "enqueued": enqueued, "retry_after_s": LOCK_TTL_S}
    finally:
        await client.aclose()
