"""Security response headers on HTML, static files, API JSON, SSE and 404s."""

import asyncio

import pytest
from fastapi.testclient import TestClient
from starlette.routing import Mount
from starlette.staticfiles import StaticFiles

from services.glassbox.api import ask, cluster
from services.glassbox.api.main import app
from services.glassbox.api.security_headers import (
    CSP_REPORT_ONLY_VALUE,
    SECURITY_HEADERS,
    SecurityHeadersMiddleware,
)
from services.glassbox.api.sse import frame

EXPECTED = {
    "strict-transport-security": "max-age=15552000",
    "x-content-type-options": "nosniff",
    "referrer-policy": "strict-origin-when-cross-origin",
    "x-frame-options": "DENY",
    "content-security-policy": "frame-ancestors 'none'",
    "content-security-policy-report-only": CSP_REPORT_ONLY_VALUE,
    "reporting-endpoints": 'csp="/api/csp-report"',
}


def assert_security_headers(response):
    for name, value in EXPECTED.items():
        assert response.headers.get(name) == value, name
    policy = response.headers["permissions-policy"]
    for feature in ("camera", "microphone", "geolocation", "payment", "usb"):
        assert f"{feature}=()" in policy
    # Clipboard stays allowed: the Contact control copies the email address.
    assert "clipboard" not in policy
    # One value per header, never a duplicate from a second layer.
    for name in SECURITY_HEADERS:
        assert len(response.headers.get_list(name)) == 1, name


def test_values_are_the_documented_ones():
    hsts = SECURITY_HEADERS["Strict-Transport-Security"]
    # Moderate max-age, never preload or includeSubDomains (docs/DESIGN.md §11).
    assert "preload" not in hsts and "includesubdomains" not in hsts.lower()
    # The enforced CSP only blocks framing; scripts and styles are Report-Only.
    assert SECURITY_HEADERS["Content-Security-Policy"] == "frame-ancestors 'none'"


def test_report_only_policy_is_strict_and_reports_to_the_endpoint():
    directives = {
        part.split(" ", 1)[0]: part.split(" ", 1)[1]
        for part in SECURITY_HEADERS["Content-Security-Policy-Report-Only"].split("; ")
    }
    assert directives == {
        "default-src": "'self'",
        "script-src": "'self'",
        "style-src": "'self'",
        "img-src": "'self'",
        "font-src": "'self'",
        "connect-src": "'self'",
        "object-src": "'none'",
        "base-uri": "'self'",
        "form-action": "'self'",
        "report-uri": "/api/csp-report",
        "report-to": "csp",
    }
    # No escape hatches, and no frame-ancestors (ignored in Report-Only, with a
    # console warning; the enforced header already carries it).
    value = SECURITY_HEADERS["Content-Security-Policy-Report-Only"]
    for loose in ("unsafe-inline", "unsafe-eval", "data:", "*", "http:", "https:"):
        assert loose not in value
    assert "frame-ancestors" not in value
    assert SECURITY_HEADERS["Reporting-Endpoints"] == 'csp="/api/csp-report"'


def test_index_html_has_no_inline_script():
    """script-src 'self' only works if the page has no inline <script> body."""
    import re
    from pathlib import Path

    html = (Path(__file__).resolve().parents[2] / "frontend" / "index.html").read_text()
    scripts = re.findall(r"<script\b([^>]*)>(.*?)</script>", html, flags=re.S)
    assert scripts, "expected the iOS zoom and app entry scripts"
    for attrs, body in scripts:
        assert "src=" in attrs and not body.strip(), attrs
    assert '<script src="/ios-zoom.js"></script>' in html
    # Classic and parser-blocking, so it still runs before first paint.
    assert not re.search(r'<script[^>]*ios-zoom[^>]*\b(async|defer|type="module")', html)
    assert "<style" not in html and " style=" not in html
    # Same logic as the old inline script: iOS/iPadOS only, maximum-scale=1.
    script = (Path(__file__).resolve().parents[2] / "frontend/public/ios-zoom.js").read_text()
    assert "/iP(hone|od|ad)/.test(navigator.userAgent)" in script
    assert "navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1" in script
    assert "', maximum-scale=1'" in script


@pytest.fixture
def frontend(tmp_path):
    """Mount a tiny built frontend on the real app, like the production image does."""
    (tmp_path / "index.html").write_text("<!doctype html><title>Glassbox</title>")
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "index-abc.js").write_text("console.log('hi')")
    (assets / "index-abc.css").write_text("body{}")
    mount = Mount("/", app=StaticFiles(directory=tmp_path, html=True), name="test-frontend")
    # First, so it wins over a real frontend/dist mount if one was built locally.
    # Only static paths are requested while it is in place.
    app.router.routes.insert(0, mount)
    try:
        yield
    finally:
        app.router.routes.remove(mount)


def test_html_and_static_assets_get_headers(frontend):
    client = TestClient(app)
    page = client.get("/")
    assert page.status_code == 200
    assert page.headers["content-type"].startswith("text/html")
    assert_security_headers(page)

    script = client.get("/assets/index-abc.js")
    assert script.status_code == 200
    # nosniff makes browsers enforce the type, so it must be a JavaScript one.
    assert "javascript" in script.headers["content-type"]
    assert_security_headers(script)

    style = client.get("/assets/index-abc.css")
    assert style.headers["content-type"].startswith("text/css")
    assert_security_headers(style)


def test_static_404_gets_headers(frontend):
    response = TestClient(app).get("/assets/missing.js")
    assert response.status_code == 404
    assert_security_headers(response)


def test_api_json_gets_headers():
    response = TestClient(app).get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert_security_headers(response)


def test_api_404_and_validation_error_get_headers():
    client = TestClient(app)
    missing = client.get("/api/nope")
    assert missing.status_code == 404
    assert_security_headers(missing)

    invalid = client.post("/api/ask", json={"question": "hi", "corpus": "bad"})
    assert invalid.status_code == 422
    assert_security_headers(invalid)


def test_ask_sse_keeps_streaming_headers_and_gets_security_headers(monkeypatch):
    async def fake_stream(*_args):
        yield frame("stage", {"stage": "api"})
        yield frame("done", {"mode": "full"})

    monkeypatch.setattr(ask, "_stream", fake_stream)
    response = TestClient(app).post(
        "/api/ask", json={"question": "Who is Basel?", "corpus": "about_me"}
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["x-accel-buffering"] == "no"
    assert response.headers["cache-control"] == "no-cache"
    assert "content-encoding" not in response.headers
    assert "event: stage" in response.text and "event: done" in response.text
    assert_security_headers(response)


def test_cluster_sse_gets_headers(monkeypatch):
    monkeypatch.setattr(cluster, "HUB", cluster.ClusterHub(idle_linger_s=0.05))
    for name in ("KUBERNETES_SERVICE_HOST", "KUBERNETES_SERVICE_PORT_HTTPS"):
        monkeypatch.delenv(name, raising=False)
    with TestClient(app) as client:
        response = client.get("/api/cluster/stream")
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["x-accel-buffering"] == "no"
    assert "cluster_unavailable" in response.text
    assert_security_headers(response)


def test_middleware_passes_body_chunks_through_without_buffering():
    """Each SSE frame reaches the server's send() before the next one exists."""
    released = asyncio.Event()
    sent: list[dict] = []

    async def streaming_app(scope, receive, send):
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [
                    (b"content-type", b"text/event-stream"),
                    (b"x-accel-buffering", b"no"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": b": ping\n\n", "more_body": True})
        # The second frame is only produced after the test has seen the first.
        await asyncio.wait_for(released.wait(), timeout=2)
        await send({"type": "http.response.body", "body": b"event: done\n\n", "more_body": False})

    async def receive():
        await asyncio.sleep(3600)

    async def send(message):
        sent.append(message)
        if message.get("body") == b": ping\n\n":
            released.set()

    async def scenario():
        scope = {"type": "http", "method": "GET", "path": "/", "headers": []}
        await SecurityHeadersMiddleware(streaming_app)(scope, receive, send)

    asyncio.run(scenario())
    assert [m["type"] for m in sent] == [
        "http.response.start",
        "http.response.body",
        "http.response.body",
    ]
    headers = dict(sent[0]["headers"])
    assert headers[b"x-accel-buffering"] == b"no"
    assert headers[b"x-content-type-options"] == b"nosniff"
    assert b"content-encoding" not in headers
    assert [m["body"] for m in sent[1:]] == [b": ping\n\n", b"event: done\n\n"]


def test_a_route_can_override_a_header():
    async def framed_app(scope, receive, send):
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"x-frame-options", b"SAMEORIGIN")],
            }
        )
        await send({"type": "http.response.body", "body": b""})

    sent: list[dict] = []

    async def send(message):
        sent.append(message)

    async def scenario():
        scope = {"type": "http", "method": "GET", "path": "/", "headers": []}
        await SecurityHeadersMiddleware(framed_app)(scope, None, send)

    asyncio.run(scenario())
    values = [v for k, v in sent[0]["headers"] if k == b"x-frame-options"]
    assert values == [b"SAMEORIGIN"]
