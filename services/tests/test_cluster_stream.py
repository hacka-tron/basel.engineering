"""Tests for the /api/cluster/stream SSE endpoint (DESIGN.md §9.4/§9.5)."""

import json

from fastapi.testclient import TestClient

from services.glassbox.api.main import app


def _events(response):
    return [
        (name.removeprefix("event: "), json.loads(data.removeprefix("data: ")))
        for frame in response.text.strip().split("\n\n")
        for name, data in [frame.splitlines()]
    ]


def test_reports_unavailable_without_incluster_config(monkeypatch):
    """No ServiceAccount token/host env vars — the real local-dev situation."""
    monkeypatch.delenv("KUBERNETES_SERVICE_HOST", raising=False)
    monkeypatch.delenv("KUBERNETES_SERVICE_PORT_HTTPS", raising=False)
    monkeypatch.delenv("KUBERNETES_SERVICE_PORT", raising=False)
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:6379/0")

    response = TestClient(app).get("/api/cluster/stream")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    names = [name for name, _ in _events(response)]
    assert "cluster_unavailable" in names


class _FakeWatchResponse:
    def __init__(self, lines):
        self._lines = lines

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    def raise_for_status(self):
        pass

    async def aiter_lines(self):
        for line in self._lines:
            yield line


class _FakeListResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class _FakeK8sClient:
    def __init__(self, pods, watch_lines):
        self._pods = pods
        self._watch_lines = watch_lines

    async def get(self, path, params=None):
        return _FakeListResponse({"metadata": {"resourceVersion": "1"}, "items": self._pods})

    def stream(self, method, path, params=None):
        return _FakeWatchResponse(self._watch_lines)

    async def aclose(self):
        pass


def test_forwards_pod_and_backlog_events_from_a_fake_cluster(monkeypatch):
    from services.glassbox.api import cluster

    pod = {
        "metadata": {"name": "retrieval-worker-abc123"},
        "status": {"phase": "Running", "conditions": [{"type": "Ready", "status": "True"}]},
    }
    watch_line = json.dumps(
        {
            "type": "MODIFIED",
            "object": {
                "metadata": {"name": "retrieval-worker-abc123"},
                "status": {
                    "phase": "Running",
                    "conditions": [{"type": "Ready", "status": "False"}],
                },
            },
        }
    )
    fake_client = _FakeK8sClient([pod], [watch_line])
    monkeypatch.setattr(cluster, "_incluster_client", lambda: (fake_client, "https://fake"))
    monkeypatch.setattr(cluster, "_BACKLOG_POLL_S", 0.01)
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:6379/0")

    response = TestClient(app).get("/api/cluster/stream")
    events = _events(response)
    pod_events = [data for name, data in events if name == "pod"]

    assert pod_events[0] == {
        "type": "ADDED",
        "pod": "retrieval-worker-abc123",
        "phase": "Running",
        "ready": True,
    }
    assert {
        "type": "MODIFIED",
        "pod": "retrieval-worker-abc123",
        "phase": "Running",
        "ready": False,
    } in pod_events
    # Only name/phase/ready ever leave the cluster — never the full pod object.
    assert all(set(data.keys()) == {"type", "pod", "phase", "ready"} for data in pod_events)
    assert any(name == "backlog" for name, _ in events)


def test_pod_only_forwards_name_phase_ready_never_full_object():
    from services.glassbox.api.cluster import _pod_event

    pod = {
        "metadata": {"name": "retrieval-worker-xyz", "uid": "secret-uid", "labels": {"a": "b"}},
        "spec": {"nodeName": "ip-10-0-0-1"},
        "status": {"phase": "Pending", "conditions": []},
    }
    assert _pod_event("ADDED", pod) == {
        "type": "ADDED",
        "pod": "retrieval-worker-xyz",
        "phase": "Pending",
        "ready": False,
    }
