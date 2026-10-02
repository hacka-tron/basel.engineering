#!/usr/bin/env python3
"""Post-deploy streaming check through Cloudflare (docs/DESIGN-002-followups.md §7.5).

Run by .github/workflows/stream-check.yml after each release and once a day.
Standard library only, read-only, and free: it never calls /api/ask, because
every real question spends a slot of the daily LLM budget (and an embedding
call). It streams /api/cluster/stream instead, which goes through the same
Cloudflare `/api/*` cache rule, the same Traefik route and the same
`with_heartbeat` SSE wrapper as /api/ask, and costs nothing per visitor
(one shared Kubernetes watch per API process).

Checks, all against the public URL:

1. Optional: wait until GET /api/version reports the expected `build-N` (or a
   newer one) on several reads in a row, i.e. Flux has rolled the release out.
2. http:// answers 301 with a Location on https:// (Cloudflare "Always Use HTTPS").
3. The page and the SSE response carry the app's security headers.
4. The SSE response: 200, `text/event-stream`, no Content-Encoding even though
   the client accepts gzip/br/zstd, `no-cache`, not cached by Cloudflare.
5. Events arrive incrementally: the first event within a few seconds, `: ping`
   heartbeats arrive, and no gap between arrivals (or before the end of the
   listening window) is much longer than the heartbeat interval. A buffering
   layer would hold the snapshot and pings back and deliver them in one burst.

Exit status: 0 all checks passed, 1 a check failed, 2 the expected build never
went live within the wait timeout.
"""

import argparse
import http.client
import json
import os
import re
import sys
import time
import urllib.parse

# The headers services/glassbox/api/security_headers.py adds to every response
# (services/tests/test_stream_check.py keeps this list in sync with it).
SECURITY_HEADERS = (
    "strict-transport-security",
    "x-content-type-options",
    "referrer-policy",
    "x-frame-options",
    "content-security-policy",
    "permissions-policy",
    "content-security-policy-report-only",
    "reporting-endpoints",
)
# cf-cache-status values that mean Cloudflare did not cache the response.
UNCACHED_STATUSES = {"DYNAMIC", "BYPASS"}
STREAM_PATH = "/api/cluster/stream"
VERSION_PATH = "/api/version"
USER_AGENT = "glassbox-stream-check/1 (+https://github.com/hacka-tron/basel.engineering)"
BUSY_STATUSES = {429, 503}


class Failures(list):
    def check(self, ok: bool, message: str) -> None:
        print(("ok   " if ok else "FAIL ") + message, flush=True)
        if not ok:
            self.append(message)


def _connection(url: str, timeout: float) -> tuple[http.client.HTTPConnection, str]:
    parts = urllib.parse.urlsplit(url)
    cls = http.client.HTTPSConnection if parts.scheme == "https" else http.client.HTTPConnection
    path = parts.path or "/"
    if parts.query:
        path += "?" + parts.query
    return cls(parts.netloc, timeout=timeout), path


def fetch(url: str, headers: dict | None = None, timeout: float = 20.0):
    """GET without following redirects. Returns (status, headers, body)."""
    conn, path = _connection(url, timeout)
    try:
        conn.request("GET", path, headers={"User-Agent": USER_AGENT, **(headers or {})})
        response = conn.getresponse()
        return response.status, response.headers, response.read()
    finally:
        conn.close()


def build_number(tag: str) -> int | None:
    match = re.fullmatch(r"build-(\d+)", tag or "")
    return int(match.group(1)) if match else None


def live_build(base_url: str) -> str | None:
    try:
        status, _, body = fetch(base_url + VERSION_PATH, {"Accept": "application/json"})
        if status != 200:
            return None
        build = json.loads(body).get("build")
        return build if isinstance(build, str) else None
    except (OSError, http.client.HTTPException, ValueError, AttributeError):
        return None


def wait_for_build(
    base_url: str, expected: str, timeout_s: float, poll_s: float, needed: int = 3
) -> bool:
    """Wait until /api/version reports `expected` or newer on `needed` reads in a row.

    Several reads in a row because during a rolling update requests can still
    land on an old pod. Newer counts as live: a later release can overtake
    this one before it finishes rolling out (Flux deploys the highest build-N).
    """
    want = build_number(expected)
    if want is None:
        raise ValueError(f"expected build must look like build-N, got {expected!r}")
    deadline = time.monotonic() + timeout_s
    streak, last_seen = 0, object()
    while True:
        build = live_build(base_url)
        if build != last_seen:
            print(f"live build: {build or 'unknown'} (waiting for {expected})", flush=True)
            last_seen = build
        number = build_number(build or "")
        streak = streak + 1 if number is not None and number >= want else 0
        if streak >= needed:
            print(f"ok   {build} is live", flush=True)
            return True
        if time.monotonic() + poll_s > deadline:
            print(
                f"FAIL {expected} not live after {timeout_s:.0f}s (last seen {build or 'unknown'})"
            )
            return False
        time.sleep(poll_s)


def check_redirect(http_url: str, https_prefix: str, failures: Failures) -> None:
    try:
        status, headers, _ = fetch(http_url)
    except (OSError, http.client.HTTPException) as exc:
        failures.check(False, f"{http_url} unreachable: {exc}")
        return
    location = headers.get("Location", "")
    failures.check(
        status == 301 and location.startswith(https_prefix),
        f"{http_url} answers 301 to https (got {status}, Location {location or 'none'})",
    )


def check_security_headers(headers, where: str, failures: Failures) -> None:
    missing = [name for name in SECURITY_HEADERS if not headers.get(name)]
    failures.check(
        not missing, f"security headers on {where}" + (f", missing {missing}" if missing else "")
    )
    nosniff = headers.get("x-content-type-options", "")
    if nosniff:
        failures.check(
            nosniff.lower() == "nosniff",
            f"X-Content-Type-Options on {where} is nosniff (got {nosniff})",
        )


def open_stream(url: str, timeout: float, attempts: int = 3, max_retry_after_s: float = 60.0):
    """Open the SSE stream, waiting out a per-IP or global stream cap (429/503)."""
    for attempt in range(1, attempts + 1):
        conn, path = _connection(url, timeout)
        start = time.monotonic()
        conn.request(
            "GET",
            path,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "text/event-stream",
                "Cache-Control": "no-cache",
                # A layer that compresses the stream has to buffer it, so ask
                # for compression and require that none is applied.
                "Accept-Encoding": "gzip, br, zstd",
            },
        )
        response = conn.getresponse()
        if response.status not in BUSY_STATUSES or attempt == attempts:
            return conn, response, start
        retry_after = min(float(response.headers.get("Retry-After") or 10), max_retry_after_s)
        print(f"stream busy ({response.status}), retrying in {retry_after:.0f}s", flush=True)
        conn.close()
        time.sleep(retry_after)
    raise AssertionError("unreachable")


def read_stream(
    conn, response, start: float, listen_s: float
) -> tuple[list[tuple[float, str]], bool]:
    """Read SSE lines until `listen_s` after `start`.

    Returns ([(seconds since start, item)], ended_early) where item is an event
    name or "ping".
    """
    arrivals: list[tuple[float, str]] = []
    event_name = None
    while True:
        remaining = start + listen_s - time.monotonic()
        if remaining <= 0:
            return arrivals, False
        if conn.sock is not None:
            conn.sock.settimeout(remaining)
        try:
            raw = response.readline()
        except TimeoutError:
            return arrivals, False
        if not raw:
            return arrivals, True
        line = raw.decode("utf-8", "replace").rstrip("\r\n")
        now = time.monotonic() - start
        if line.startswith(":"):
            arrivals.append((now, "ping" if line[1:].strip() == "ping" else "comment"))
        elif line.startswith("event:"):
            event_name = line[6:].strip()
        elif line == "" and event_name is not None:
            arrivals.append((now, event_name))
            event_name = None


def check_stream(
    url: str,
    failures: Failures,
    listen_s: float = 50.0,
    max_first_event_s: float = 5.0,
    heartbeat_s: float = 15.0,
    gap_slack_s: float = 5.0,
    connect_timeout_s: float = 20.0,
) -> list[tuple[float, str]]:
    try:
        conn, response, start = open_stream(url, connect_timeout_s)
    except (OSError, http.client.HTTPException) as exc:
        failures.check(False, f"{url} unreachable: {exc}")
        return []
    try:
        headers = response.headers
        failures.check(response.status == 200, f"stream answers 200 (got {response.status})")
        if response.status != 200:
            return []
        content_type = headers.get("Content-Type", "")
        failures.check(
            content_type.startswith("text/event-stream"),
            f"Content-Type is text/event-stream (got {content_type or 'none'})",
        )
        encoding = headers.get("Content-Encoding", "")
        failures.check(
            encoding in ("", "identity"),
            f"stream is not compressed (Content-Encoding {encoding or 'none'})",
        )
        cache_control = headers.get("Cache-Control", "")
        failures.check(
            "no-cache" in cache_control or "no-store" in cache_control,
            f"Cache-Control forbids caching (got {cache_control or 'none'})",
        )
        cf_cache = headers.get("cf-cache-status")
        if cf_cache is not None:
            failures.check(
                cf_cache.upper() in UNCACHED_STATUSES,
                f"Cloudflare does not cache it (cf-cache-status {cf_cache})",
            )
        check_security_headers(headers, "the stream", failures)

        arrivals, ended_early = read_stream(conn, response, start, listen_s)
    finally:
        conn.close()

    print("arrivals (seconds after the request was sent):")
    for at, item in arrivals:
        print(f"  {at:7.2f}  {item}")
    events = [at for at, item in arrivals if item not in ("ping", "comment")]
    pings = [at for at, item in arrivals if item == "ping"]
    failures.check(not ended_early, f"stream stays open for {listen_s:.0f}s")
    first = f"{events[0]:.2f}s" if events else "none"
    failures.check(
        bool(events) and events[0] <= max_first_event_s,
        f"first event within {max_first_event_s:g}s (got {first})",
    )
    failures.check(bool(pings), f"heartbeats arrive ({len(pings)} ': ping' in {listen_s:.0f}s)")
    # From the request to the first arrival, between arrivals, and from the
    # last arrival to the end of the window.
    times = [0.0] + [at for at, _ in arrivals]
    if arrivals:
        gaps = [later - earlier for earlier, later in zip(times, times[1:], strict=False)]
        if not ended_early:
            gaps.append(listen_s - times[-1])
        longest = max(gaps, default=0.0)
        limit = heartbeat_s + gap_slack_s
        failures.check(
            longest <= limit,
            f"arrivals are spread out, longest gap {longest:.2f}s <= {limit:g}s",
        )
    return arrivals


def write_summary(base_url: str, failures: Failures, arrivals) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    lines = [f"### Stream check: {'passed' if not failures else 'FAILED'} ({base_url})", ""]
    lines += [f"- FAIL {message}" for message in failures]
    if arrivals:
        lines += ["", "| s after request | item |", "|---:|---|"]
        lines += [f"| {at:.2f} | {item} |" for at, item in arrivals]
    with open(path, "a", encoding="utf-8") as summary:
        summary.write("\n".join(lines) + "\n")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--base-url", default="https://basel.engineering")
    parser.add_argument(
        "--expect-build", help="wait until /api/version reports this build-N (or newer)"
    )
    parser.add_argument("--wait-timeout", type=float, default=1800.0)
    parser.add_argument("--poll", type=float, default=20.0)
    parser.add_argument("--listen", type=float, default=50.0, help="seconds to read the stream")
    parser.add_argument("--max-first-event", type=float, default=5.0)
    parser.add_argument("--heartbeat", type=float, default=15.0, help="the server's ping interval")
    parser.add_argument("--gap-slack", type=float, default=5.0)
    args = parser.parse_args(argv)
    base_url = args.base_url.rstrip("/")

    if args.expect_build and not wait_for_build(
        base_url, args.expect_build, args.wait_timeout, args.poll
    ):
        return 2

    failures = Failures()
    parts = urllib.parse.urlsplit(base_url)
    if parts.scheme == "https":
        check_redirect(f"http://{parts.netloc}/", f"https://{parts.netloc}/", failures)
    try:
        status, headers, _ = fetch(base_url + "/")
        failures.check(status == 200, f"{base_url}/ answers 200 (got {status})")
        check_security_headers(headers, "the page", failures)
    except (OSError, http.client.HTTPException) as exc:
        failures.check(False, f"{base_url}/ unreachable: {exc}")
    arrivals = check_stream(
        base_url + STREAM_PATH,
        failures,
        listen_s=args.listen,
        max_first_event_s=args.max_first_event,
        heartbeat_s=args.heartbeat,
        gap_slack_s=args.gap_slack,
    )
    write_summary(base_url, failures, arrivals)
    if failures:
        print(f"{len(failures)} check(s) failed", file=sys.stderr)
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
