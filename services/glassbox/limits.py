"""Provider-neutral request and generation limits backed by atomic Redis scripts."""

import hashlib
import ipaddress
import os
import time
from datetime import UTC, datetime
from typing import Protocol

from fastapi import Request

RATE_WINDOW_MS = 10 * 60 * 1000
RATE_CAPACITY = 10
_LOCAL_SALT = os.urandom(32)
_TOKEN_BUCKET_SCRIPT = """
local data = redis.call('HMGET', KEYS[1], 'tokens', 'ts')
local tokens = tonumber(data[1]) or tonumber(ARGV[2])
local last = tonumber(data[2]) or tonumber(ARGV[1])
local elapsed = math.max(0, tonumber(ARGV[1]) - last)
tokens = math.min(tonumber(ARGV[2]), tokens + elapsed * tonumber(ARGV[2]) / tonumber(ARGV[3]))
local allowed = 0
local retry = 0
if tokens >= 1 then
  tokens = tokens - 1
  allowed = 1
else
  retry = math.ceil((1 - tokens) * tonumber(ARGV[3]) / tonumber(ARGV[2]) / 1000)
end
redis.call('HSET', KEYS[1], 'tokens', tokens, 'ts', ARGV[1])
redis.call('PEXPIRE', KEYS[1], ARGV[3])
return {allowed, retry}
"""
_DAILY_BUDGET_SCRIPT = """
local used = tonumber(redis.call('GET', KEYS[1])) or 0
if used >= tonumber(ARGV[1]) then return 0 end
used = redis.call('INCR', KEYS[1])
if used == 1 then redis.call('EXPIRE', KEYS[1], 172800) end
return 1
"""


class RateLimiter(Protocol):
    async def allow(self, client_hash: str, *, now_ms: int | None = None) -> tuple[bool, int]: ...


class DailyBudget(Protocol):
    async def reserve(self, *, now: datetime | None = None) -> bool: ...


class RedisRateLimiter:
    def __init__(self, client, *, capacity: int = RATE_CAPACITY, window_ms: int = RATE_WINDOW_MS):
        self.client = client
        self.capacity = capacity
        self.window_ms = window_ms

    async def allow(self, client_hash: str, *, now_ms: int | None = None) -> tuple[bool, int]:
        now_ms = int(time.time() * 1000) if now_ms is None else now_ms
        allowed, retry = await self.client.eval(
            _TOKEN_BUCKET_SCRIPT, 1, f"rl:{client_hash}", now_ms, self.capacity, self.window_ms
        )
        return bool(allowed), int(retry)


class RedisDailyBudget:
    def __init__(self, client, *, cap: int = 100):
        self.client = client
        self.cap = cap

    async def reserve(self, *, now: datetime | None = None) -> bool:
        today = (now or datetime.now(UTC)).astimezone(UTC).date().isoformat()
        return bool(
            await self.client.eval(_DAILY_BUDGET_SCRIPT, 1, f"budget:llm:{today}", self.cap)
        )


def get_rate_limiter(client) -> RateLimiter:
    return RedisRateLimiter(client)


def get_daily_budget(client) -> DailyBudget:
    return RedisDailyBudget(client, cap=int(os.getenv("GLASSBOX_DAILY_LLM_CAP", "100")))


def client_ip_hash(request: Request) -> str:
    peer = request.client.host if request.client else "unknown"
    try:
        peer_address = ipaddress.ip_address(peer)
    except ValueError:
        peer_address = None
    trusted = os.getenv("GLASSBOX_TRUSTED_PROXY_CIDRS", "")
    networks = [
        ipaddress.ip_network(value.strip()) for value in trusted.split(",") if value.strip()
    ]
    if peer_address is not None and any(peer_address in network for network in networks):
        forwarded = request.headers.get("x-forwarded-for", "").split(",", 1)[0].strip()
        try:
            peer = str(ipaddress.ip_address(forwarded))
        except ValueError:
            pass
    salt = os.getenv("GLASSBOX_IP_HASH_SALT", "").encode() or _LOCAL_SALT
    return hashlib.sha256(salt + peer.encode()).hexdigest()
