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


# The pre-quarter-unit reservation script, verbatim from the base commit (6f0f009).
# During a rolling deploy, old API pods still run it against budget:llm:{date}.
OLD_POD_BUDGET_SCRIPT = """
local used = tonumber(redis.call('GET', KEYS[1])) or 0
if used >= tonumber(ARGV[1]) then return 0 end
used = redis.call('INCR', KEYS[1])
if used == 1 then redis.call('EXPIRE', KEYS[1], 172800) end
return 1
"""


async def _redis_or_skip():
    client = redis.from_url("redis://127.0.0.1:6379/0")
    try:
        await client.ping()
    except Exception as exc:
        await client.aclose()
        pytest.skip(f"local Redis unavailable: {exc}")
    return client


def _budget_keys(day: datetime) -> tuple[str, str]:
    date = day.date().isoformat()
    return f"budget:llm:{date}", f"budget:llm:rw:{date}"


@pytest.mark.asyncio
async def test_daily_budget_caps_and_rolls_over():
    client = await _redis_or_skip()
    today = datetime(2030, 1, 1, tzinfo=UTC)
    tomorrow = today + timedelta(days=1)
    keys = [*_budget_keys(today), *_budget_keys(tomorrow)]
    await client.delete(*keys)
    budget = RedisDailyBudget(client, cap=2)
    try:
        assert await budget.reserve(now=today)
        assert await budget.reserve(now=today)
        assert not await budget.reserve(now=today)
        assert await budget.reserve(now=tomorrow)
        answers_key, rewrites_key = _budget_keys(today)
        # Whole answers stay in the original key, counted as whole answers.
        assert int(await client.get(answers_key)) == 2
        assert 0 < await client.ttl(answers_key) <= 172800
        assert await client.get(rewrites_key) is None
    finally:
        await client.delete(*keys)
        await client.aclose()


@pytest.mark.asyncio
async def test_daily_budget_counts_rewrites_as_quarter_answers():
    client = await _redis_or_skip()
    today = datetime(2030, 2, 1, tzinfo=UTC)
    answers_key, rewrites_key = _budget_keys(today)
    await client.delete(answers_key, rewrites_key)
    budget = RedisDailyBudget(client, cap=1)
    try:
        assert await budget.reserve(now=today, units=REWRITE_BUDGET_UNITS)
        # 1 of 4 quarter-units used: a full answer (4 units) no longer fits.
        assert not await budget.reserve(now=today)
        assert await budget.reserve(now=today, units=REWRITE_BUDGET_UNITS)
        assert await budget.reserve(now=today, units=REWRITE_BUDGET_UNITS)
        assert await budget.reserve(now=today, units=REWRITE_BUDGET_UNITS)
        assert not await budget.reserve(now=today, units=REWRITE_BUDGET_UNITS)
        assert int(await client.get(rewrites_key)) == ANSWER_BUDGET_UNITS
        assert 0 < await client.ttl(rewrites_key) <= 172800
        assert await client.get(answers_key) is None
    finally:
        await client.delete(answers_key, rewrites_key)
        await client.aclose()


@pytest.mark.asyncio
async def test_daily_budget_combines_answers_and_rewrites():
    client = await _redis_or_skip()
    today = datetime(2030, 3, 2, tzinfo=UTC)
    answers_key, rewrites_key = _budget_keys(today)
    await client.delete(answers_key, rewrites_key)
    budget = RedisDailyBudget(client, cap=3)
    try:
        await client.set(answers_key, 2)  # 2 whole answers = 8 of 12 quarter-units
        assert await budget.reserve(now=today, units=REWRITE_BUDGET_UNITS)  # 9
        assert not await budget.reserve(now=today)  # 9 + 4 > 12
        for _ in range(3):
            assert await budget.reserve(now=today, units=REWRITE_BUDGET_UNITS)  # 12
        assert not await budget.reserve(now=today, units=REWRITE_BUDGET_UNITS)
        assert int(await client.get(answers_key)) == 2
        assert int(await client.get(rewrites_key)) == 4
    finally:
        await client.delete(answers_key, rewrites_key)
        await client.aclose()


@pytest.mark.asyncio
async def test_new_pod_answers_are_visible_to_old_pod_script():
    """Rolling deploy: an answer reserved by new code must count for an old pod."""
    client = await _redis_or_skip()
    today = datetime(2030, 3, 1, tzinfo=UTC)
    answers_key, rewrites_key = _budget_keys(today)
    await client.delete(answers_key, rewrites_key)
    budget = RedisDailyBudget(client, cap=1)
    try:
        assert await budget.reserve(now=today)
        assert not await client.eval(OLD_POD_BUDGET_SCRIPT, 1, answers_key, 1)
    finally:
        await client.delete(answers_key, rewrites_key)
        await client.aclose()


@pytest.mark.asyncio
async def test_old_pod_answers_are_visible_to_new_code():
    client = await _redis_or_skip()
    today = datetime(2030, 3, 3, tzinfo=UTC)
    answers_key, rewrites_key = _budget_keys(today)
    await client.delete(answers_key, rewrites_key)
    budget = RedisDailyBudget(client, cap=1)
    try:
        assert await client.eval(OLD_POD_BUDGET_SCRIPT, 1, answers_key, 1)
        assert not await budget.reserve(now=today)
        assert not await budget.reserve(now=today, units=REWRITE_BUDGET_UNITS)
        assert await client.get(rewrites_key) is None
    finally:
        await client.delete(answers_key, rewrites_key)
        await client.aclose()


def test_daily_budget_rejects_unknown_unit_sizes():
    budget = RedisDailyBudget(client=None, cap=1)
    with pytest.raises(ValueError):
        asyncio.run(budget.reserve(units=3))


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
