"""/api/csp-report: accepts CSP violation reports, logs three sanitized fields."""

import json
import logging

import pytest
from fastapi.testclient import TestClient

from services.glassbox.api import csp_report
from services.glassbox.api.main import app
from services.glassbox.limits import RedisRateLimiter

LOGGER_NAME = "services.glassbox.api.csp_report"

REPORT_URI_BODY = {
    "csp-report": {
        "document-uri": "https://basel.engineering/?q=secret-question#frag",
        "referrer": "https://www.google.com/search?q=basel",
        "violated-directive": "script-src-elem",
        "effective-directive": "script-src-elem",
        "original-policy": "default-src 'self'; script-src 'self'",
        "disposition": "report",
        "blocked-uri": "https://evil.example.com:8443/x.js?token=abc123",
        "line-number": 12,
        "source-file": "https://basel.engineering/assets/index.js",
        "script-sample": "alert('secret sample')",
        "status-code": 200,
    }
}

REPORTING_API_BODY = [
    {
        "age": 10,
        "type": "csp-violation",
        "url": "https://basel.engineering/?session=xyz",
        "user_agent": "Mozilla/5.0 SecretAgent",
        "body": {
            "documentURL": "https://basel.engineering/?session=xyz",
            "blockedURL": "inline",
            "effectiveDirective": "style-src-attr",
            "disposition": "report",
            "sample": "color: red",
        },
    },
    {"type": "deprecation", "body": {"id": "x"}},
]


class FakeRedis:
    """Just enough of the token-bucket EVAL for the shared limiter."""

    def __init__(self):
        self.buckets: dict[str, int] = {}
        self.keys: list[str] = []

    async def eval(self, _script, _numkeys, key, _now_ms, capacity, _window_ms):
        self.keys.append(key)
        used = self.buckets.get(key, 0)
        if used >= int(capacity):
            return [0, 60]
        self.buckets[key] = used + 1
        return [1, 0]

    async def aclose(self):
        pass


@pytest.fixture
def redis(monkeypatch):
    client = FakeRedis()
    monkeypatch.setattr(csp_report, "_redis_client", lambda: client)
    # A fresh log budget per test.
    monkeypatch.setattr(csp_report, "LOG_BUDGET", csp_report._LogBudget())
    return client


def post(client, body, content_type="application/csp-report", **headers):
    raw = body if isinstance(body, bytes) else json.dumps(body).encode()
    return client.post(
        "/api/csp-report", content=raw, headers={"content-type": content_type, **headers}
    )


def csp_lines(caplog):
    return [r.getMessage() for r in caplog.records if r.name == LOGGER_NAME]


def test_report_uri_report_is_accepted_and_logged_sanitized(redis, caplog):
    caplog.set_level(logging.WARNING, logger=LOGGER_NAME)
    response = post(TestClient(app), REPORT_URI_BODY)
    assert response.status_code == 204
    assert response.content == b""
    assert csp_lines(caplog) == [
        "csp-report-only violation directive=script-src-elem "
        "blocked=https://evil.example.com:8443 path=/"
    ]


def test_reporting_api_batch_is_accepted_and_only_csp_entries_logged(redis, caplog):
    caplog.set_level(logging.WARNING, logger=LOGGER_NAME)
    response = post(TestClient(app), REPORTING_API_BODY, "application/reports+json")
    assert response.status_code == 204
    assert csp_lines(caplog) == [
        "csp-report-only violation directive=style-src-attr blocked=inline path=/"
    ]


def test_nothing_sensitive_reaches_the_log(redis, caplog):
    caplog.set_level(logging.DEBUG)
    client = TestClient(app)
    headers = {"x-forwarded-for": "203.0.113.9", "user-agent": "SecretAgent/1.0"}
    post(client, REPORT_URI_BODY, **headers)
    post(client, REPORTING_API_BODY, "application/reports+json", **headers)
    text = "\n".join(r.getMessage() for r in caplog.records)
    for secret in (
        "secret-question",
        "frag",
        "token=abc123",
        "abc123",
        "x.js",
        "google.com",
        "secret sample",
        "color: red",
        "session=xyz",
        "SecretAgent",
        "203.0.113.9",
        "testclient",
        "original-policy",
        "default-src",
    ):
        assert secret not in text, secret
    # Not the IP hash either: the rate-limit key never appears in the log.
    for key in redis.keys:
        assert key.split(":")[-1] not in text


@pytest.mark.parametrize(
    ("blocked", "expected"),
    [
        ("inline", "inline"),
        ("EVAL", "eval"),
        ("data", "data"),
        ("data:image/png;base64,AAAA", "data:"),
        ("blob:https://basel.engineering/1234-5678", "blob:"),
        ("chrome-extension://abcdefghijklmnop/content.js", "chrome-extension:"),
        (
            "https://user:pass@cdn.example.com/a.css?x=1",  # pragma: allowlist secret
            "https://cdn.example.com",
        ),
        ("wss://basel.engineering/socket", "wss://basel.engineering"),
        ("http://192.168.1.20:8080/x", "http://ip-literal:8080"),
        ("http://[2001:db8::1]/x", "http://ip-literal"),
        ("", "none"),
        (None, "none"),
        ("https://exa mple.com/<script>", "other"),
        ("javascript:alert(1)", "javascript:"),
        ("not a url at all\nINJECTED LINE", "other"),
        (42, "none"),
    ],
)
def test_blocked_origin_is_reduced_to_an_origin_or_keyword(blocked, expected):
    assert csp_report._blocked_origin(blocked) == expected


@pytest.mark.parametrize(
    ("document", "expected"),
    [
        ("https://basel.engineering/", "/"),
        ("https://basel.engineering", "/"),
        ("https://basel.engineering/assets/x.js?q=1#f", "/assets/x.js"),
        ("https://basel.engineering/a b", "other"),
        ("https://basel.engineering/" + "a" * 200, "other"),
        ("https://basel.engineering/%0AINJECTED", "other"),
        (None, "other"),
    ],
)
def test_document_path_drops_query_and_odd_paths(document, expected):
    assert csp_report._document_path(document) == expected


@pytest.mark.parametrize(
    ("directive", "expected"),
    [
        ("script-src-elem", "script-src-elem"),
        ("script-src 'self'", "script-src"),
        ("Style-Src", "style-src"),
        ("x\ny", "other"),
        ("a" * 80, "other"),
        (None, "other"),
    ],
)
def test_directive_is_a_bare_directive_name(directive, expected):
    assert csp_report._directive(directive) == expected


def test_oversize_body_is_rejected_by_declared_length(redis, caplog):
    caplog.set_level(logging.WARNING, logger=LOGGER_NAME)
    big = {"csp-report": {**REPORT_URI_BODY["csp-report"], "script-sample": "x" * 9000}}
    response = post(TestClient(app), big)
    assert response.status_code == 413
    assert csp_lines(caplog) == []
    # Rejected before it costs the visitor a rate-limit token.
    assert redis.keys == []


def test_oversize_streamed_body_is_cut_off_while_reading(redis, caplog):
    """A chunked body with no Content-Length is still capped at 8 KiB."""
    caplog.set_level(logging.WARNING, logger=LOGGER_NAME)

    def chunks():
        yield b'{"csp-report": {"script-sample": "'
        for _ in range(20):
            yield b"x" * 1024
        yield b'"}}'

    response = TestClient(app).post(
        "/api/csp-report", content=chunks(), headers={"content-type": "application/csp-report"}
    )
    assert response.status_code == 413
    assert csp_lines(caplog) == []


@pytest.mark.parametrize(
    "body",
    [b"not json", b"\xff\xfe", b"[]", b"{}", b'{"csp-report": "x"}', b"[1, 2]", b"null"],
)
def test_invalid_reports_are_rejected(redis, caplog, body):
    caplog.set_level(logging.WARNING, logger=LOGGER_NAME)
    response = post(TestClient(app), body)
    assert response.status_code == 400
    assert csp_lines(caplog) == []


def test_wrong_content_type_is_rejected(redis):
    response = post(TestClient(app), REPORT_URI_BODY, "text/plain")
    assert response.status_code == 415
    assert redis.keys == []


def test_only_post_is_allowed(redis):
    # 405 from the router, or 404 when a built frontend/dist mount answers GETs.
    assert TestClient(app).get("/api/csp-report").status_code in (404, 405)


def test_rate_limited_per_visitor_with_its_own_bucket(redis, caplog):
    caplog.set_level(logging.WARNING, logger=LOGGER_NAME)
    client = TestClient(app)
    codes = [post(client, REPORT_URI_BODY).status_code for _ in range(csp_report.RATE_CAPACITY + 3)]
    assert codes == [204] * csp_report.RATE_CAPACITY + [429] * 3
    assert len(csp_lines(caplog)) == csp_report.RATE_CAPACITY
    # Its own Redis key, never the /api/ask question bucket ("rl:<hash>").
    assert {key.rsplit(":", 1)[0] for key in redis.keys} == {"rl:csp"}


def test_per_process_log_cap_bounds_lines_across_visitors(redis, caplog, monkeypatch):
    """Many addresses together still can't flood the log."""
    caplog.set_level(logging.WARNING, logger=LOGGER_NAME)
    budget = csp_report._LogBudget(per_window=3, window_s=60)
    monkeypatch.setattr(csp_report, "LOG_BUDGET", budget)
    client = TestClient(app)
    for i in range(10):
        post(client, REPORT_URI_BODY, **{"x-forwarded-for": f"198.51.100.{i}"})
    assert len(csp_lines(caplog)) == 3
    assert budget.suppressed == 7
    # The next window starts with one summary line, then logs again.
    assert budget.take(now=budget.window_start + 61)
    lines = csp_lines(caplog)
    assert lines[-1] == (
        "csp-report-only violation log cap: 7 more reports not logged in the last minute"
    )


def test_reports_per_request_are_capped(redis, caplog):
    caplog.set_level(logging.WARNING, logger=LOGGER_NAME)
    small = {
        "type": "csp-violation",
        "body": {"effectiveDirective": "img-src", "blockedURL": "data"},
    }
    batch = [small] * 40
    raw = json.dumps(batch).encode()
    assert len(raw) < csp_report.MAX_BODY_BYTES
    response = post(TestClient(app), raw, "application/reports+json")
    assert response.status_code == 204
    assert len(csp_lines(caplog)) == csp_report.MAX_REPORTS_PER_REQUEST


def test_no_redis_means_report_dropped_not_logged(monkeypatch, caplog):
    caplog.set_level(logging.WARNING, logger=LOGGER_NAME)
    monkeypatch.setattr(csp_report, "LOG_BUDGET", csp_report._LogBudget())

    def broken():
        raise ConnectionError("redis down")

    monkeypatch.setattr(csp_report, "_redis_client", broken)
    response = post(TestClient(app), REPORT_URI_BODY)
    assert response.status_code == 204
    assert csp_lines(caplog) == ["CSP report rate limiter unavailable; report dropped"]


def test_no_database_or_other_writes(redis, monkeypatch):
    """The endpoint touches nothing but its rate-limit bucket."""
    from services.glassbox.api import ask

    def forbidden(*_args, **_kwargs):
        raise AssertionError("no database writes from the CSP report endpoint")

    monkeypatch.setattr(ask, "get_session_factory", forbidden)
    assert post(TestClient(app), REPORT_URI_BODY).status_code == 204
    assert all(key.startswith("rl:csp:") for key in redis.keys)


def test_report_response_carries_the_security_headers(redis):
    response = post(TestClient(app), REPORT_URI_BODY)
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "content-security-policy-report-only" in response.headers


def test_rate_limiter_default_prefix_is_unchanged():
    """/api/ask keeps its existing rl:<hash> keys (live buckets survive the deploy)."""
    import asyncio

    seen = []

    class Recorder:
        async def eval(self, _script, _n, key, *_args):
            seen.append(key)
            return [1, 0]

    asyncio.run(RedisRateLimiter(Recorder()).allow("abc"))
    asyncio.run(RedisRateLimiter(Recorder(), key_prefix="rl:csp").allow("abc"))
    assert seen == ["rl:abc", "rl:csp:abc"]
