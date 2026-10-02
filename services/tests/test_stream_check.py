"""Offline tests for .github/scripts/stream-check.py against a local fake site.

The fake serves the page, /api/version and an SSE stream on a background
thread, scaled down in time (heartbeat 0.3 s instead of 15 s). Each stream mode
models one way production streaming breaks.
"""

import importlib.util
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from services.glassbox.api.security_headers import SECURITY_HEADERS

_SCRIPT = Path(__file__).resolve().parents[2] / ".github" / "scripts" / "stream-check.py"
_spec = importlib.util.spec_from_file_location("stream_check", _SCRIPT)
stream_check = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(stream_check)

HEARTBEAT = 0.3
LISTEN = 1.5
TIMING = {
    "listen_s": LISTEN,
    "max_first_event_s": 0.5,
    "heartbeat_s": HEARTBEAT,
    "gap_slack_s": 0.3,
}
SNAPSHOT = (
    'retry: 5000\n\nevent: pod\ndata: {"pod":"w","ready":true}\n\n'
    'event: backlog\ndata: {"backlog":0}\n\nevent: synced\ndata: {}\n\n'
)


class FakeSite(BaseHTTPRequestHandler):
    stream_mode = "good"
    builds: list[str] = []
    omit_header: str | None = None
    stream_status = 200
    stream_attempts = 0
    page_errors = 0

    def log_message(self, *args):
        pass

    def _security_headers(self):
        for name, value in SECURITY_HEADERS.items():
            if name.lower() != (self.omit_header or "").lower():
                self.send_header(name, value)

    def do_GET(self):  # noqa: N802 (http.server API)
        if self.path == "/" and FakeSite.page_errors > 0:
            FakeSite.page_errors -= 1
            self.send_response(503)
            self.end_headers()
        elif self.path == "/":
            self.send_response(200)
            self._security_headers()
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<html></html>")
        elif self.path == "/redirect":
            self.send_response(301)
            self.send_header("Location", "https://example.test/")
            self.end_headers()
        elif self.path == "/api/version":
            build = FakeSite.builds.pop(0) if len(FakeSite.builds) > 1 else FakeSite.builds[0]
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"build":"%s"}' % build.encode())
        elif self.path == "/api/cluster/stream":
            self._stream()
        else:
            self.send_response(404)
            self.end_headers()

    def _stream(self):
        FakeSite.stream_attempts += 1
        if FakeSite.stream_status != 200 and FakeSite.stream_attempts == 1:
            self.send_response(FakeSite.stream_status)
            self.send_header("Retry-After", "0")
            self.end_headers()
            return
        mode = FakeSite.stream_mode
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("cf-cache-status", "HIT" if mode == "cached" else "DYNAMIC")
        if mode == "gzip":
            self.send_header("Content-Encoding", "gzip")
        self._security_headers()
        self.end_headers()
        try:
            if mode == "buffered":
                # A buffering proxy: nothing until the buffer flushes, then a burst.
                time.sleep(LISTEN * 0.8)
                self.wfile.write((SNAPSHOT + ": ping\n\n: ping\n\n").encode())
                self.wfile.flush()
                time.sleep(LISTEN)
                return
            self.wfile.write(SNAPSHOT.encode())
            self.wfile.flush()
            if mode == "closes":
                return
            if mode == "busy":
                # Events more often than the heartbeat interval: no pings are due.
                for _ in range(10):
                    time.sleep(HEARTBEAT * 0.6)
                    self.wfile.write(b'event: backlog\ndata: {"backlog":1}\n\n')
                    self.wfile.flush()
                time.sleep(LISTEN)
                return
            beats = 0 if mode == "no-pings" else 10
            for _ in range(beats):
                time.sleep(HEARTBEAT)
                self.wfile.write(b": ping\n\n")
                self.wfile.flush()
            time.sleep(LISTEN)
        except (BrokenPipeError, ConnectionResetError):
            pass


@pytest.fixture
def site():
    FakeSite.stream_mode = "good"
    FakeSite.builds = ["build-1"]
    FakeSite.omit_header = None
    FakeSite.stream_status = 200
    FakeSite.stream_attempts = 0
    FakeSite.page_errors = 0
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeSite)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def _stream_failures(base_url):
    failures = stream_check.Failures()
    arrivals = stream_check.check_stream(base_url + stream_check.STREAM_PATH, failures, **TIMING)
    return failures, arrivals


def test_header_list_matches_the_app_middleware():
    assert set(stream_check.SECURITY_HEADERS) == {name.lower() for name in SECURITY_HEADERS}


def test_a_healthy_stream_passes(site):
    failures, arrivals = _stream_failures(site)
    assert failures == []
    assert [item for _, item in arrivals[:3]] == ["pod", "backlog", "synced"]
    assert any(item == "ping" for _, item in arrivals)


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        ("buffered", "first event within"),
        ("no-pings", "heartbeats arrive"),
        ("closes", "stream stays open"),
        ("gzip", "stream is not compressed"),
        ("cached", "Cloudflare does not cache it"),
    ],
)
def test_broken_streams_fail(site, mode, expected):
    FakeSite.stream_mode = mode
    failures, _ = _stream_failures(site)
    assert any(message.startswith(expected) for message in failures), failures


def test_a_stream_busy_with_events_needs_no_pings(site):
    FakeSite.stream_mode = "busy"
    failures, arrivals = _stream_failures(site)
    assert failures == []
    assert not any(item == "ping" for _, item in arrivals)


def test_buffered_burst_also_fails_the_gap_check(site):
    FakeSite.stream_mode = "buffered"
    failures, _ = _stream_failures(site)
    assert any(message.startswith("arrivals are spread out") for message in failures), failures


def test_missing_security_header_fails(site):
    FakeSite.omit_header = "X-Frame-Options"
    failures, _ = _stream_failures(site)
    assert any("missing ['x-frame-options']" in message for message in failures), failures


def test_busy_stream_is_retried(site):
    FakeSite.stream_status = 429
    failures, _ = _stream_failures(site)
    assert failures == []
    assert FakeSite.stream_attempts == 2


def test_redirect_check(site):
    failures = stream_check.Failures()
    stream_check.check_redirect(site + "/redirect", "https://example.test/", failures)
    assert failures == []
    stream_check.check_redirect(site + "/", "https://example.test/", failures)
    assert len(failures) == 1


def test_wait_for_build_needs_consecutive_reads_and_accepts_newer(site):
    # An old pod answers once mid-rollout, so the streak restarts.
    FakeSite.builds = ["build-6", "build-7", "build-6", "build-7", "build-8"]
    assert stream_check.wait_for_build(site, "build-7", timeout_s=5, poll_s=0.01, needed=3)
    assert FakeSite.builds == ["build-8"]


def test_build_numbers_compare_numerically():
    assert stream_check.build_number("build-10") > stream_check.build_number("build-9")
    assert stream_check.build_number("dev") is None
    assert stream_check.build_number("build-") is None


def test_wait_for_build_accepts_a_multi_digit_newer_build(site):
    FakeSite.builds = ["build-10"]
    assert stream_check.wait_for_build(site, "build-9", timeout_s=5, poll_s=0.01)
    FakeSite.builds = ["build-9"]
    assert not stream_check.wait_for_build(site, "build-10", timeout_s=0.2, poll_s=0.05)


def test_wait_for_build_times_out(site):
    FakeSite.builds = ["build-6"]
    assert not stream_check.wait_for_build(site, "build-7", timeout_s=0.2, poll_s=0.05)


def test_main_against_the_fake_site(site, tmp_path, monkeypatch):
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    FakeSite.builds = ["build-3"]
    args = ["--base-url", site, "--expect-build", "build-3", "--poll", "0.01"]
    args += [
        "--listen",
        str(LISTEN),
        "--max-first-event",
        "0.5",
        "--heartbeat",
        str(HEARTBEAT),
        "--gap-slack",
        "0.3",
    ]
    assert stream_check.main(args) == 0
    assert "Stream check: passed" in summary.read_text()
    FakeSite.builds = ["dev"]
    assert stream_check.main([*args[:4], "--wait-timeout", "0.1", "--poll", "0.05"]) == 2


def test_page_5xx_during_a_rollout_is_retried(site):
    FakeSite.page_errors = 2
    status, headers, _ = stream_check.fetch_page(site + "/", retry_s=0.01)
    assert status == 200 and headers.get("x-frame-options") == "DENY"
    FakeSite.page_errors = 5
    assert stream_check.fetch_page(site + "/", retry_s=0.01)[0] == 503
