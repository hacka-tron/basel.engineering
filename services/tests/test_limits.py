"""Atomic Redis limits and proxy-safe client hashing."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
import redis.asyncio as redis
from starlette.requests import Request

from services.glassbox.limits import (
    ANSWER_BUDGET_UNITS,
    REWRITE_BUDGET_UNITS,
    RedisDailyBudget,
    RedisRateLimiter,
    client_ip_hash,
)


@pytest.mark.asyncio
async def test_token_bucket_refills_and_is_atomic():
    client = redis.from_url("redis://127.0.0.1:6379/0")
    try:
        await client.ping()
    except Exception as exc:
        await client.aclose()
        pytest.skip(f"local Redis unavailable: {exc}")
    identity = uuid4().hex
    limiter = RedisRateLimiter(client, capacity=2, window_ms=600_000)
    try:
        assert await limiter.allow(identity, now_ms=1_000_000) == (True, 0)
        assert await limiter.allow(identity, now_ms=1_000_000) == (True, 0)
        assert await limiter.allow(identity, now_ms=1_000_000) == (False, 300)
        assert await limiter.allow(identity, now_ms=1_300_000) == (True, 0)
        concurrent_identity = uuid4().hex
        one_slot = RedisRateLimiter(client, capacity=1)
        outcomes = await asyncio.gather(
            *[one_slot.allow(concurrent_identity, now_ms=1_000_000) for _ in range(5)]
        )
        assert sum(allowed for allowed, _ in outcomes) == 1
    finally:
        await client.delete(f"rl:{identity}", f"rl:{concurrent_identity}")
        await client.aclose()


@pytest.mark.asyncio
async def test_daily_budget_caps_and_rolls_over():
    client = redis.from_url("redis://127.0.0.1:6379/0")
    try:
        await client.ping()
    except Exception as exc:
        await client.aclose()
        pytest.skip(f"local Redis unavailable: {exc}")
    today = datetime(2030, 1, 1, tzinfo=UTC)
    tomorrow = today + timedelta(days=1)
    keys = [f"budget:llm:q:{day.date().isoformat()}" for day in (today, tomorrow)]
    await client.delete(*keys)
    budget = RedisDailyBudget(client, cap=2)
    try:
        assert await budget.reserve(now=today)
        assert await budget.reserve(now=today)
        assert not await budget.reserve(now=today)
        assert await budget.reserve(now=tomorrow)
        assert 0 < await client.ttl(keys[0]) <= 172800
        assert int(await client.get(keys[0])) == 8
    finally:
        await client.delete(*keys)
        await client.aclose()


@pytest.mark.asyncio
async def test_daily_budget_counts_rewrites_as_quarter_answers():
    client = redis.from_url("redis://127.0.0.1:6379/0")
    try:
        await client.ping()
    except Exception as exc:
        await client.aclose()
        pytest.skip(f"local Redis unavailable: {exc}")
    today = datetime(2030, 2, 1, tzinfo=UTC)
    key = f"budget:llm:q:{today.date().isoformat()}"
    await client.delete(key)
    budget = RedisDailyBudget(client, cap=1)
    try:
        assert await budget.reserve(now=today, units=REWRITE_BUDGET_UNITS)
        # 1 of 4 quarter-units used: a full answer (4 units) no longer fits.
        assert not await budget.reserve(now=today)
        assert await budget.reserve(now=today, units=REWRITE_BUDGET_UNITS)
        assert await budget.reserve(now=today, units=REWRITE_BUDGET_UNITS)
        assert await budget.reserve(now=today, units=REWRITE_BUDGET_UNITS)
        assert not await budget.reserve(now=today, units=REWRITE_BUDGET_UNITS)
        assert int(await client.get(key)) == ANSWER_BUDGET_UNITS
        assert 0 < await client.ttl(key) <= 172800
    finally:
        await client.delete(key)
        await client.aclose()


@pytest.mark.asyncio
async def test_daily_budget_counts_legacy_whole_answer_key_on_transition_day():
    """Usage recorded under the pre-quarter-unit key must still count that day."""
    client = redis.from_url("redis://127.0.0.1:6379/0")
    try:
        await client.ping()
    except Exception as exc:
        await client.aclose()
        pytest.skip(f"local Redis unavailable: {exc}")
    today = datetime(2030, 3, 1, tzinfo=UTC)
    day = today.date().isoformat()
    legacy, key = f"budget:llm:{day}", f"budget:llm:q:{day}"
    await client.delete(legacy, key)
    budget = RedisDailyBudget(client, cap=100)
    try:
        await client.set(legacy, 100)
        assert not await budget.reserve(now=today)
        assert not await budget.reserve(now=today, units=REWRITE_BUDGET_UNITS)
        assert await client.get(key) is None
    finally:
        await client.delete(legacy, key)
        await client.aclose()


@pytest.mark.asyncio
async def test_daily_budget_combines_partial_legacy_and_quarter_usage():
    client = redis.from_url("redis://127.0.0.1:6379/0")
    try:
        await client.ping()
    except Exception as exc:
        await client.aclose()
        pytest.skip(f"local Redis unavailable: {exc}")
    today = datetime(2030, 3, 2, tzinfo=UTC)
    day = today.date().isoformat()
    legacy, key = f"budget:llm:{day}", f"budget:llm:q:{day}"
    await client.delete(legacy, key)
    budget = RedisDailyBudget(client, cap=3)
    try:
        await client.set(legacy, 2)  # 2 whole answers = 8 of 12 quarter-units
        assert await budget.reserve(now=today, units=REWRITE_BUDGET_UNITS)  # 9
        assert not await budget.reserve(now=today)  # 9 + 4 > 12
        assert await budget.reserve(now=today, units=3)  # 12
        assert not await budget.reserve(now=today, units=REWRITE_BUDGET_UNITS)
        assert int(await client.get(key)) == 4
        assert int(await client.get(legacy)) == 2  # legacy key is read, never written
    finally:
        await client.delete(legacy, key)
        await client.aclose()


def _request(peer: str, forwarded: str) -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/ask",
            "headers": [(b"x-forwarded-for", forwarded.encode())],
            "client": (peer, 12345),
        }
    )


def test_forwarded_ip_is_used_only_for_trusted_proxy(monkeypatch):
    monkeypatch.setenv("GLASSBOX_IP_HASH_SALT", "test-salt")
    monkeypatch.setenv("GLASSBOX_TRUSTED_PROXY_CIDRS", "10.0.0.0/8")
    trusted = client_ip_hash(_request("10.0.0.1", "203.0.113.4"))
    direct = client_ip_hash(_request("203.0.113.4", "198.51.100.1"))
    untrusted = client_ip_hash(_request("198.51.100.1", "203.0.113.4"))
    assert trusted == direct
    assert trusted != untrusted
