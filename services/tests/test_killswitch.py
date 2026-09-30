"""Redis-backed LLM kill switch."""

import pytest
import redis.asyncio as redis

from services.glassbox.killswitch import RedisKillSwitch


@pytest.mark.asyncio
async def test_llm_disabled_reads_opt_in_flag():
    client = redis.from_url("redis://127.0.0.1:6379/0")
    try:
        await client.ping()
    except Exception as exc:
        await client.aclose()
        pytest.skip(f"local Redis unavailable: {exc}")
    switch = RedisKillSwitch(client)
    try:
        await client.delete(RedisKillSwitch.KEY)
        assert not await switch.llm_disabled()
        await client.set(RedisKillSwitch.KEY, "1")
        assert await switch.llm_disabled()
        await client.set(RedisKillSwitch.KEY, "0")
        assert not await switch.llm_disabled()
    finally:
        await client.delete(RedisKillSwitch.KEY)
        await client.aclose()
