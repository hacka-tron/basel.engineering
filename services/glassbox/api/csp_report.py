"""Receive Content-Security-Policy violation reports (docs/DESIGN.md §11).

The site sends ``Content-Security-Policy-Report-Only`` (security_headers.py),
so browsers report what an enforced policy would block, here, through both
reporting mechanisms:

- ``report-uri``: one ``application/csp-report`` POST per violation, body
  ``{"csp-report": {"effective-directive": ..., "blocked-uri": ...}}``
  (Firefox, Safari, and Chrome when it has no Reporting API endpoint);
- ``report-to`` + ``Reporting-Endpoints``: ``application/reports+json``, a
  JSON list of ``{"type": "csp-violation", "body": {...}}`` (Chromium).

The endpoint is public and unauthenticated, so it is built so it can't be
used to flood the logs or store anything:

- no database or Redis writes beyond the per-visitor rate-limit bucket;
- a per-visitor token bucket (the shared Redis limiter under its own
  ``rl:csp`` key, so reports never spend a visitor's question budget), then
  the body is read with an 8 KiB cap, checked before and while reading;
- at most ``MAX_REPORTS_PER_REQUEST`` reports per POST are looked at, and a
  per-process cap on log lines per minute (one summary line for the rest),
  so even many addresses together can't flood the log;
- each log line holds only three sanitized fields: the directive, the blocked
  resource's origin (scheme and host, or a keyword like ``inline``) and the
  document's path. No query strings, fragments, full URLs, code samples,
  user agents, IP addresses or IP hashes. Anything that doesn't fit the
  expected shape is logged as ``other``.

It always answers 204 to a well-formed report (also when a log line is
suppressed), 429 when the visitor is over the rate limit, 413/400/415 for
oversized, malformed or wrongly typed bodies. Browsers ignore the status;
these are for tests and for anyone probing the endpoint.

An oversized body is dropped whole, with one budgeted ``oversize`` line that
Ops · Diagnose counts. Chromium batches Reporting API reports, so a page with
many violations at once could produce a batch over 8 KiB; on a clean site
that doesn't happen, and the line makes it visible if it ever does. A client
that disconnects mid-body is dropped silently.

Known limits: there is no app-level timeout on reading the body; a slow
sender is bounded by Traefik's and Cloudflare's timeouts, and by the rate
limit checked before reading. The log-cap summary line for a minute is
written when the next report arrives after that minute, not on a timer, so
a flood that ends abruptly may never get its summary line.
"""

import ipaddress
import json
import logging
import os
import re
import time
from urllib.parse import urlsplit

import redis.asyncio as redis
from fastapi import APIRouter, Request, Response
from starlette.requests import ClientDisconnect

from services.glassbox.limits import RedisRateLimiter, client_ip_hash

router = APIRouter()
LOGGER = logging.getLogger(__name__)

REPORT_PATH = "/api/csp-report"
# The fixed prefix of every log line, so Ops · Diagnose can count them.
LOG_MARKER = "csp-report-only violation"

MAX_BODY_BYTES = 8 * 1024
MAX_REPORTS_PER_REQUEST = 5
# Per visitor (IP hash, IPv6 /64): a page load with a few violations sends a
# few reports, so this is generous for a real browser and small for a script.
RATE_CAPACITY = 30
RATE_WINDOW_MS = 10 * 60 * 1000
RATE_KEY_PREFIX = "rl:csp"
# Per api process, whatever the source: at most this many log lines a minute.
LOG_LINES_PER_MINUTE = 30
_LOG_WINDOW_S = 60.0

_CONTENT_TYPES = {"application/csp-report", "application/reports+json", "application/json"}
_DIRECTIVE_RE = re.compile(r"[a-z][a-z-]{0,39}")
_HOST_RE = re.compile(r"[a-z0-9.-]{1,100}")
_SCHEME_RE = re.compile(r"[a-z][a-z0-9+.-]{0,30}")
_PATH_RE = re.compile(r"/[A-Za-z0-9/._~-]{0,63}")
# blocked-uri / blockedURL values that are keywords, not URLs.
_BLOCKED_KEYWORDS = {
    "inline",
    "eval",
    "wasm-eval",
    "self",
    "data",
    "blob",
    "trusted-types-policy",
    "trusted-types-sink",
}
_NETWORK_SCHEMES = {"http", "https", "ws", "wss"}


class _LogBudget:
    """A fixed one-minute window of log lines for this process."""

    def __init__(self, per_window: int = LOG_LINES_PER_MINUTE, window_s: float = _LOG_WINDOW_S):
        self.per_window = per_window
        self.window_s = window_s
        self.window_start = float("-inf")
        self.used = 0
        self.suppressed = 0

    def take(self, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        if now - self.window_start >= self.window_s:
            if self.suppressed:
                LOGGER.warning(
                    "%s log cap: %d more reports not logged in the last minute",
                    LOG_MARKER,
                    self.suppressed,
                )
            self.window_start = now
            self.used = 0
            self.suppressed = 0
        if self.used >= self.per_window:
            self.suppressed += 1
            return False
        self.used += 1
        return True


LOG_BUDGET = _LogBudget()


def _redis_client():
    return redis.from_url(os.environ["REDIS_URL"])


def get_report_rate_limiter(client) -> RedisRateLimiter:
    return RedisRateLimiter(
        client, capacity=RATE_CAPACITY, window_ms=RATE_WINDOW_MS, key_prefix=RATE_KEY_PREFIX
    )


def _directive(value: object) -> str:
    if not isinstance(value, str):
        return "other"
    # violated-directive may carry the whole directive ("script-src 'self'").
    token = value.strip().lower().split(" ", 1)[0]
    return token if _DIRECTIVE_RE.fullmatch(token) else "other"


def _blocked_origin(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        return "none"
    value = value.strip()
    if value.lower() in _BLOCKED_KEYWORDS:
        return value.lower()
    try:
        parts = urlsplit(value)
        scheme = parts.scheme.lower()
        host = (parts.hostname or "").lower()
        port = parts.port
    except ValueError:
        return "other"
    if not _SCHEME_RE.fullmatch(scheme):
        return "other"
    if scheme not in _NETWORK_SCHEMES:
        # data:, blob:, chrome-extension: and the like: the scheme says enough
        # (an extension's ID would only identify the visitor's setup).
        return f"{scheme}:"
    if not host:
        return "other"
    try:
        ipaddress.ip_address(host)
        host = "ip-literal"
    except ValueError:
        if not _HOST_RE.fullmatch(host):
            return "other"
    return f"{scheme}://{host}" + (f":{port}" if port else "")


def _document_path(value: object) -> str:
    if not isinstance(value, str):
        return "other"
    try:
        path = urlsplit(value.strip()).path or "/"
    except ValueError:
        return "other"
    return path if _PATH_RE.fullmatch(path) else "other"


def _violations(payload: object) -> list[tuple[str, str, str]] | None:
    """(directive, blocked origin, path) per report, or None if not a CSP report."""
    if isinstance(payload, dict) and isinstance(payload.get("csp-report"), dict):
        report = payload["csp-report"]
        return [
            (
                _directive(report.get("effective-directive") or report.get("violated-directive")),
                _blocked_origin(report.get("blocked-uri")),
                _document_path(report.get("document-uri")),
            )
        ]
    if isinstance(payload, list):
        found = []
        for item in payload[:MAX_REPORTS_PER_REQUEST]:
            if not isinstance(item, dict) or item.get("type") != "csp-violation":
                continue
            body = item.get("body")
            if not isinstance(body, dict):
                continue
            found.append(
                (
                    _directive(body.get("effectiveDirective")),
                    _blocked_origin(body.get("blockedURL")),
                    _document_path(body.get("documentURL") or item.get("url")),
                )
            )
        return found or None
    return None


_OVERSIZE = object()
_DISCONNECTED = object()


async def _read_capped(request: Request) -> bytes | object:
    """The body, _OVERSIZE as soon as it grows past MAX_BODY_BYTES, or
    _DISCONNECTED if the client goes away before sending all of it."""
    body = bytearray()
    try:
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > MAX_BODY_BYTES:
                return _OVERSIZE
    except ClientDisconnect:
        return _DISCONNECTED
    return bytes(body)


def _oversize() -> Response:
    if LOG_BUDGET.take():
        LOGGER.warning("%s oversize: body over %d bytes, dropped", LOG_MARKER, MAX_BODY_BYTES)
    return Response(status_code=413)


async def _allowed(request: Request) -> bool:
    client = _redis_client()
    try:
        allowed, _retry = await get_report_rate_limiter(client).allow(client_ip_hash(request))
        return allowed
    finally:
        await client.aclose()


@router.post(REPORT_PATH)
async def csp_report(request: Request) -> Response:
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type not in _CONTENT_TYPES:
        return Response(status_code=415)
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        return _oversize()
    try:
        allowed = await _allowed(request)
    except Exception:
        # No Redis, no way to bound this visitor: accept and drop the report.
        # This line shares the per-process log cap, and carries no details.
        if LOG_BUDGET.take():
            LOGGER.warning("CSP report rate limiter unavailable; report dropped")
        return Response(status_code=204)
    if not allowed:
        return Response(status_code=429)
    raw = await _read_capped(request)
    if raw is _OVERSIZE:
        return _oversize()
    if raw is _DISCONNECTED:
        # Nobody is left to answer; nothing worth logging.
        return Response(status_code=400)
    assert isinstance(raw, bytes)
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, ValueError):
        return Response(status_code=400)
    violations = _violations(payload)
    if violations is None:
        return Response(status_code=400)
    for directive, blocked, path in violations:
        if LOG_BUDGET.take():
            LOGGER.warning(
                "%s directive=%s blocked=%s path=%s", LOG_MARKER, directive, blocked, path
            )
    return Response(status_code=204)
