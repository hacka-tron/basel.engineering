"""Atomic Redis limits and proxy-safe client hashing."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
import redis.asyncio as redis
from starlette.requests import Request

from services.glassbox.limits import (
    ANSWER_BUDGET_UNITS,
    MIN_IP_HASH_SALT_LENGTH,
    REWRITE_BUDGET_UNITS,
    RedisDailyBudget,
    RedisRateLimiter,
    client_address,
    client_ip_hash,
    validate_ip_hash_salt,
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


def _request(peer: str, forwarded: str | None = None, **headers: str) -> Request:
    raw = [(name.replace("_", "-").encode(), value.encode()) for name, value in headers.items()]
    if forwarded is not None:
        raw.append((b"x-forwarded-for", forwarded.encode()))
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/ask",
            "headers": raw,
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


@pytest.fixture
def proxy_env(monkeypatch):
    monkeypatch.setenv("GLASSBOX_IP_HASH_SALT", "test-salt")
    monkeypatch.setenv("GLASSBOX_TRUSTED_PROXY_CIDRS", "10.42.0.0/16")
    monkeypatch.delenv("GLASSBOX_CLIENT_IP_HEADER", raising=False)


def test_spoofed_leftmost_forwarded_entry_is_ignored(proxy_env):
    # A proxy that appends (rather than replaces) keeps whatever the client
    # sent on the left. The rightmost untrusted hop is the one to believe.
    spoofed = _request("10.42.0.9", "198.51.100.77, 203.0.113.4")
    assert client_address(spoofed) == "203.0.113.4"
    assert client_ip_hash(spoofed) == client_ip_hash(_request("10.42.0.9", "203.0.113.4"))


def test_forwarded_walk_skips_trusted_hops_and_stops_at_garbage(proxy_env):
    assert client_address(_request("10.42.0.9", "203.0.113.4, 10.42.0.3")) == "203.0.113.4"
    # Only trusted hops: nothing better than the peer.
    assert client_address(_request("10.42.0.9", "10.42.0.3")) == "10.42.0.9"
    # An unparsable hop ends the walk instead of trusting what is left of it.
    assert client_address(_request("10.42.0.9", "203.0.113.4, not-an-ip")) == "10.42.0.9"
    assert client_address(_request("10.42.0.9")) == "10.42.0.9"


def test_client_ip_header_wins_from_trusted_proxy_only(proxy_env, monkeypatch):
    monkeypatch.setenv("GLASSBOX_CLIENT_IP_HEADER", "CF-Connecting-IP")
    via_proxy = _request("10.42.0.9", "198.51.100.77, 162.158.1.1", cf_connecting_ip="203.0.113.4")
    assert client_address(via_proxy) == "203.0.113.4"
    # From an untrusted peer the header is just client input.
    direct = _request("198.51.100.1", None, cf_connecting_ip="203.0.113.4")
    assert client_address(direct) == "198.51.100.1"
    # Missing or garbage header: fall back to X-Forwarded-For.
    assert client_address(_request("10.42.0.9", "203.0.113.4")) == "203.0.113.4"
    garbage = _request("10.42.0.9", "203.0.113.4", cf_connecting_ip="x")
    assert client_address(garbage) == "203.0.113.4"
    # IPv6 is normalised, so one visitor maps to one bucket.
    v6 = _request("10.42.0.9", None, cf_connecting_ip="2001:DB8::0001")
    assert client_address(v6) == "2001:db8::1"


def test_ipv6_visitors_share_a_bucket_per_64(proxy_env, monkeypatch):
    monkeypatch.setenv("GLASSBOX_CLIENT_IP_HEADER", "cf-connecting-ip")

    def hashed(address: str) -> str:
        return client_ip_hash(_request("10.42.0.9", None, cf_connecting_ip=address))

    assert hashed("2001:db8:1:2::1") == hashed("2001:db8:1:2:ffff::7")
    assert hashed("2001:db8:1:2::1") != hashed("2001:db8:1:3::1")
    assert hashed("203.0.113.4") != hashed("203.0.113.5")


def test_salt_is_optional_unless_required(monkeypatch):
    monkeypatch.delenv("GLASSBOX_REQUIRE_IP_HASH_SALT", raising=False)
    monkeypatch.delenv("GLASSBOX_IP_HASH_SALT", raising=False)
    validate_ip_hash_salt()


@pytest.mark.parametrize("salt", [None, "", "   ", "x" * (MIN_IP_HASH_SALT_LENGTH - 1)])
def test_required_salt_fails_closed(monkeypatch, salt):
    monkeypatch.setenv("GLASSBOX_REQUIRE_IP_HASH_SALT", "true")
    if salt is None:
        monkeypatch.delenv("GLASSBOX_IP_HASH_SALT", raising=False)
    else:
        monkeypatch.setenv("GLASSBOX_IP_HASH_SALT", salt)
    with pytest.raises(ValueError, match="GLASSBOX_IP_HASH_SALT"):
        validate_ip_hash_salt()


def test_required_salt_accepts_a_real_salt(monkeypatch):
    monkeypatch.setenv("GLASSBOX_REQUIRE_IP_HASH_SALT", "true")
    monkeypatch.setenv("GLASSBOX_IP_HASH_SALT", "s" * 64)
    validate_ip_hash_salt()


def test_api_refuses_to_start_without_required_salt(monkeypatch):
    from fastapi.testclient import TestClient

    from services.glassbox.api.main import app

    monkeypatch.setenv("GLASSBOX_REQUIRE_IP_HASH_SALT", "true")
    monkeypatch.delenv("GLASSBOX_IP_HASH_SALT", raising=False)
    with pytest.raises(ValueError, match="GLASSBOX_IP_HASH_SALT"):
        with TestClient(app):
            pass
