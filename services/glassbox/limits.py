"""Provider-neutral request and generation limits backed by atomic Redis scripts."""

import hashlib
import hmac
import ipaddress
import logging
import os
import time
from datetime import UTC, datetime
from typing import Protocol

from fastapi import Request

LOGGER = logging.getLogger(__name__)
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
# The daily budget is enforced in integer quarter-units (DESIGN-002 §5.4/§9.4): a
# generated answer costs 4, a follow-up rewrite (a much smaller call) costs 1.
#
# Whole answers stay counted, one per answer, in the original key budget:llm:{date}
# (KEYS[1]), exactly as pre-rewrite code counts them, so old and new API pods share one
# answer counter during a rolling deploy. Rewrites are counted in quarter-units in
# budget:llm:rw:{date} (KEYS[2]). A reservation checks 4*answers + rewrites + units
# against 4*cap atomically, then increments the matching key. Accepted residual: an old
# pod only compares answers with the cap, so it cannot see rewrite usage during the
# brief overlap of a rolling deploy.
ANSWER_BUDGET_UNITS = 4
REWRITE_BUDGET_UNITS = 1
_DAILY_BUDGET_SCRIPT = """
local answers = tonumber(redis.call('GET', KEYS[1])) or 0
local rewrites = tonumber(redis.call('GET', KEYS[2])) or 0
local units = tonumber(ARGV[2])
if 4 * answers + rewrites + units > tonumber(ARGV[1]) then return 0 end
local key = KEYS[2]
if ARGV[3] == 'answer' then
  key = KEYS[1]
  redis.call('INCR', key)
else
  redis.call('INCRBY', key, units)
end
if redis.call('TTL', key) < 0 then redis.call('EXPIRE', key, 172800) end
return 1
"""


class RateLimiter(Protocol):
    async def allow(self, client_hash: str, *, now_ms: int | None = None) -> tuple[bool, int]: ...


class DailyBudget(Protocol):
    async def reserve(
        self, *, now: datetime | None = None, units: int = ANSWER_BUDGET_UNITS
    ) -> bool: ...


class RedisRateLimiter:
    def __init__(
        self,
        client,
        *,
        capacity: int = RATE_CAPACITY,
        window_ms: int = RATE_WINDOW_MS,
        key_prefix: str = "rl",
    ):
        self.client = client
        self.capacity = capacity
        self.window_ms = window_ms
        # "rl" is the /api/ask question bucket. Other endpoints pass their own
        # prefix so they never spend a visitor's question budget.
        self.key_prefix = key_prefix

    async def allow(self, client_hash: str, *, now_ms: int | None = None) -> tuple[bool, int]:
        now_ms = int(time.time() * 1000) if now_ms is None else now_ms
        allowed, retry = await self.client.eval(
            _TOKEN_BUCKET_SCRIPT,
            1,
            f"{self.key_prefix}:{client_hash}",
            now_ms,
            self.capacity,
            self.window_ms,
        )
        return bool(allowed), int(retry)


class RedisDailyBudget:
    def __init__(self, client, *, cap: int = 100):
        self.client = client
        self.cap = cap  # in generated answers

    async def reserve(
        self, *, now: datetime | None = None, units: int = ANSWER_BUDGET_UNITS
    ) -> bool:
        if units == ANSWER_BUDGET_UNITS:
            kind = "answer"
        elif units == REWRITE_BUDGET_UNITS:
            kind = "rewrite"
        else:
            raise ValueError(f"unsupported budget reservation size: {units}")
        today = (now or datetime.now(UTC)).astimezone(UTC).date().isoformat()
        return bool(
            await self.client.eval(
                _DAILY_BUDGET_SCRIPT,
                2,
                f"budget:llm:{today}",
                f"budget:llm:rw:{today}",
                self.cap * ANSWER_BUDGET_UNITS,
                units,
                kind,
            )
        )


def get_rate_limiter(client) -> RateLimiter:
    return RedisRateLimiter(client)


def get_daily_budget(client) -> DailyBudget:
    return RedisDailyBudget(client, cap=int(os.getenv("GLASSBOX_DAILY_LLM_CAP", "100")))


# Real ip_hash_salt values are 64 characters (infra/modules/secrets); anything
# much shorter is a misconfiguration, not a key.
MIN_IP_HASH_SALT_LENGTH = 32


def validate_ip_hash_salt() -> None:
    """Fail closed at API startup when production requires a stable salt.

    Without GLASSBOX_IP_HASH_SALT the hash falls back to a random per-process
    salt: still private, but every restart or extra replica gets its own rate
    limit buckets, and query-log hashes stop being comparable. Production sets
    GLASSBOX_REQUIRE_IP_HASH_SALT=true (k8s/base/configmap-app.yaml), so a
    missing or empty secret stops the pod instead of degrading silently.
    """
    if os.getenv("GLASSBOX_REQUIRE_IP_HASH_SALT", "").strip().lower() not in {"1", "true", "yes"}:
        return
    if len(os.getenv("GLASSBOX_IP_HASH_SALT", "").strip()) < MIN_IP_HASH_SALT_LENGTH:
        raise ValueError(
            "GLASSBOX_IP_HASH_SALT must be set (at least "
            f"{MIN_IP_HASH_SALT_LENGTH} characters) when GLASSBOX_REQUIRE_IP_HASH_SALT is true"
        )


def _parse_ip(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(value.strip())
    except ValueError:
        return None


def _is_trusted(address, networks) -> bool:
    return address is not None and any(address in network for network in networks)


# At most one "trusted proxy fallback" warning per interval per process.
_FALLBACK_WARNING_INTERVAL_S = 60.0
_last_fallback_warning = float("-inf")


def _warn_trusted_proxy_fallback() -> None:
    """Make a missing client IP header visible without flooding the log.

    If Cloudflare stops sending CF-Connecting-IP (for example its "Remove
    visitor IP headers" managed transform is switched on), every visitor would
    silently share the proxy's single rate-limit bucket. No addresses logged.
    """
    global _last_fallback_warning
    now = time.monotonic()
    if now - _last_fallback_warning < _FALLBACK_WARNING_INTERVAL_S:
        return
    _last_fallback_warning = now
    LOGGER.warning(
        "Client IP header missing or invalid on a proxied request; rate limit is "
        "keyed on the trusted proxy fallback (one shared bucket)"
    )


def client_address(request: Request) -> str:
    """The visitor's IP, believing forwarding headers only from trusted proxies.

    Forwarding headers are read only when the TCP peer is inside
    GLASSBOX_TRUSTED_PROXY_CIDRS (in production: the pod network, i.e. Traefik).
    Then, in order:
      1. GLASSBOX_CLIENT_IP_HEADER, if configured (production: CF-Connecting-IP,
         which Cloudflare always overwrites with the address it saw, and the
         node's security group only admits Cloudflare);
      2. X-Forwarded-For, walked from the right, skipping trusted proxies: the
         first untrusted hop is the closest address a trusted proxy vouched
         for. The leftmost entry is whatever the client chose to send, so it
         is never used on its own.
    Anything unparsable falls back to the next option, and finally to the peer.
    """
    peer = request.client.host if request.client else "unknown"
    trusted = os.getenv("GLASSBOX_TRUSTED_PROXY_CIDRS", "")
    networks = [
        ipaddress.ip_network(value.strip()) for value in trusted.split(",") if value.strip()
    ]
    if not _is_trusted(_parse_ip(peer), networks):
        return peer
    header = os.getenv("GLASSBOX_CLIENT_IP_HEADER", "").strip().lower()
    if header:
        address = _parse_ip(request.headers.get(header, ""))
        if address is not None:
            return str(address)
    hops = [hop for hop in request.headers.get("x-forwarded-for", "").split(",") if hop.strip()]
    for hop in reversed(hops):
        address = _parse_ip(hop)
        if address is None:
            break
        if not _is_trusted(address, networks):
            return str(address)
    # Only proxied requests carry X-Forwarded-For (Traefik always sets it); the
    # in-cluster answer warmer calls the api directly without it.
    if header and hops:
        _warn_trusted_proxy_fallback()
    return peer


def _rate_limit_identity(address: str) -> str:
    # One IPv6 subscriber usually holds a whole /64, so key on the /64;
    # otherwise rotating addresses inside it would give a fresh bucket each time.
    # An IPv4-mapped address (::ffff:1.2.3.4) is the same visitor as 1.2.3.4.
    parsed = _parse_ip(address)
    if isinstance(parsed, ipaddress.IPv6Address):
        if parsed.ipv4_mapped is not None:
            return str(parsed.ipv4_mapped)
        return str(ipaddress.ip_network(f"{parsed}/64", strict=False))
    return address


def client_ip_hash(request: Request) -> str:
    salt = os.getenv("GLASSBOX_IP_HASH_SALT", "").encode() or _LOCAL_SALT
    identity = _rate_limit_identity(client_address(request))
    return hmac.new(salt, identity.encode(), hashlib.sha256).hexdigest()
