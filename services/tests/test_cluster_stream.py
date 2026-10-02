"""Tests for the /api/cluster/stream SSE endpoint (DESIGN.md §9.4/§9.5).

The Kubernetes API and Redis are faked: one fake API server counts how many
list/watch calls it receives, so the tests can prove that many SSE clients
share one upstream watch, and that the caps, lifetime and heartbeat bound
what a client can hold open.
"""

import asyncio
import gc
import json

import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from services.glassbox.api import cluster
from services.glassbox.api.main import app
from services.glassbox.worker.main import GROUP_NAME


def _events(text):
    """Parse SSE text into (event, data) pairs, skipping retry and ping frames."""
    out = []
    for block in text.strip().split("\n\n"):
        lines = block.splitlines()
        names = [line.removeprefix("event: ") for line in lines if line.startswith("event: ")]
        data = [line.removeprefix("data: ") for line in lines if line.startswith("data: ")]
        if names and data:
            out.append((names[0], json.loads(data[0])))
    return out


def _pod(name, *, ready=True, phase="Running"):
    return {
        "metadata": {"name": name, "uid": "secret-uid"},
        "spec": {"nodeName": "node-1"},
        "status": {
            "phase": phase,
            "conditions": [{"type": "Ready", "status": "True" if ready else "False"}],
        },
    }


class _FakeRedis:
    def __init__(self, lag=7):
        self.lag = lag

    async def xinfo_groups(self, _stream):
        return [{"name": GROUP_NAME, "lag": self.lag}]

    async def aclose(self):
        pass


class _ListResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class _WatchResponse:
    def __init__(self, server):
        self._server = server

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    def raise_for_status(self):
        pass

    async def aiter_lines(self):
        if self._server.watch_lines is not None:
            for line in self._server.watch_lines:
                yield line
            return
        while True:
            line = await self._server.live.get()
            if line is None:
                return
            yield line


class _FakeK8s:
    """A fake API server. `listings` are returned in order (the last one repeats).

    With `watch_lines` set, each watch replays those lines and ends (the API
    server closing the watch). Otherwise the watch blocks on `live`, a queue
    the test pushes lines into; None ends the watch.
    """

    def __init__(self, listings, watch_lines=None):
        self.listings = listings
        self.watch_lines = watch_lines
        self.live: asyncio.Queue | None = None
        self.list_calls = 0
        self.watch_calls = 0
        self.closed_clients = 0
        self.watch_params: list[dict] = []
        self.watch_error: Exception | None = None
        # When set, closing a client waits for this event (a slow shutdown).
        self.close_gate: asyncio.Event | None = None

    def client(self):
        server = self

        class _Client:
            async def get(self, path, params=None):
                index = min(server.list_calls, len(server.listings) - 1)
                server.list_calls += 1
                return _ListResponse(
                    {"metadata": {"resourceVersion": "1"}, "items": server.listings[index]}
                )

            def stream(self, method, path, params=None):
                server.watch_calls += 1
                server.watch_params.append(params or {})
                if server.watch_error is not None:
                    raise server.watch_error
                return _WatchResponse(server)

            async def aclose(self):
                if server.close_gate is not None:
                    await server.close_gate.wait()
                server.closed_clients += 1

        return _Client(), "https://fake"


@pytest.fixture
def fake_env(monkeypatch):
    """A fresh hub, fake Redis, and fast timings."""
    hub = cluster.ClusterHub(idle_linger_s=0.05)
    monkeypatch.setattr(cluster, "HUB", hub)
    monkeypatch.setattr(cluster, "_redis_client", lambda: _FakeRedis())
    monkeypatch.setattr(cluster, "_BACKLOG_POLL_S", 0.01)
    monkeypatch.setattr(cluster, "_WATCH_RESTART_S", 0.05)
    monkeypatch.setattr(cluster, "_WATCH_RETRY_BASE_S", 0.05)
    for name in (
        "GLASSBOX_CLUSTER_STREAM_MAX_CLIENTS",
        "GLASSBOX_CLUSTER_STREAM_MAX_PER_IP",
        "GLASSBOX_CLUSTER_STREAM_MAX_LIFETIME_S",
        "GLASSBOX_CLUSTER_STREAM_HEARTBEAT_S",
        "GLASSBOX_CLUSTER_STREAM_RETRY_AFTER_S",
    ):
        monkeypatch.delenv(name, raising=False)
    return hub


def _request(client_ip="203.0.113.5", disconnected=lambda: False):
    async def receive():
        await asyncio.sleep(3600)
        return {"type": "http.disconnect"}

    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/cluster/stream",
            "headers": [],
            "query_string": b"",
            "client": (client_ip, 50000),
        },
        receive,
    )

    async def is_disconnected():
        return disconnected()

    request.is_disconnected = is_disconnected  # type: ignore[method-assign]
    return request


async def _drain(subscriber, until, timeout=2.0):
    """Collect (kind, payload) items from a subscriber's queue until `until` matches."""
    items = []
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        remaining = deadline - asyncio.get_running_loop().time()
        assert remaining > 0, f"timed out; got {items}"
        item = await asyncio.wait_for(subscriber.queue.get(), timeout=remaining)
        items.append(item)
        if until(item):
            return items


# --- Endpoint (TestClient) ---------------------------------------------------


def test_reports_unavailable_without_incluster_config(fake_env, monkeypatch):
    """No ServiceAccount token/host env vars — the real local-dev situation."""
    monkeypatch.delenv("KUBERNETES_SERVICE_HOST", raising=False)
    monkeypatch.delenv("KUBERNETES_SERVICE_PORT_HTTPS", raising=False)
    monkeypatch.delenv("KUBERNETES_SERVICE_PORT", raising=False)

    with TestClient(app) as client:
        response = client.get("/api/cluster/stream")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    names = [name for name, _ in _events(response.text)]
    assert names[-1] == "cluster_unavailable"
    assert fake_env.client_count == 0


def test_forwards_pod_and_backlog_events_then_asks_for_reconnect(fake_env, monkeypatch):
    watch_line = json.dumps(
        {"type": "MODIFIED", "object": _pod("retrieval-worker-abc123", ready=False)}
    )
    server = _FakeK8s([[_pod("retrieval-worker-abc123")]], watch_lines=[watch_line])
    monkeypatch.setattr(cluster, "_incluster_client", server.client)
    monkeypatch.setenv("GLASSBOX_CLUSTER_STREAM_MAX_LIFETIME_S", "0.4")

    with TestClient(app) as client:
        response = client.get("/api/cluster/stream")
    assert response.text.startswith("retry: ")
    events = _events(response.text)
    names = [name for name, _ in events]
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
    # The snapshot ends with `synced`; the planned close is a `reconnect` event.
    assert names.index("synced") > names.index("pod")
    assert names[-1] == "reconnect"
    assert ("backlog", {"backlog": 7}) in events
    # Only name/phase/ready ever leave the cluster — never the full pod object.
    assert all(set(data.keys()) == {"type", "pod", "phase", "ready"} for data in pod_events)
    assert fake_env.client_count == 0


def test_pod_only_forwards_name_phase_ready_never_full_object():
    pod = {
        "metadata": {"name": "retrieval-worker-xyz", "uid": "secret-uid", "labels": {"a": "b"}},
        "spec": {"nodeName": "ip-10-0-0-1"},
        "status": {"phase": "Pending", "conditions": []},
    }
    assert cluster._pod_event("ADDED", pod) == {
        "type": "ADDED",
        "pod": "retrieval-worker-xyz",
        "phase": "Pending",
        "ready": False,
    }


# --- Caps ---------------------------------------------------------------------


def test_global_cap_returns_503_with_retry_after(fake_env, monkeypatch):
    server = _FakeK8s([[]])
    monkeypatch.setattr(cluster, "_incluster_client", server.client)
    monkeypatch.setenv("GLASSBOX_CLUSTER_STREAM_MAX_CLIENTS", "2")
    monkeypatch.setenv("GLASSBOX_CLUSTER_STREAM_RETRY_AFTER_S", "45")

    async def scenario():
        server.live = asyncio.Queue()
        first = await cluster.cluster_stream(_request("203.0.113.1"))
        second = await cluster.cluster_stream(_request("203.0.113.2"))
        third = await cluster.cluster_stream(_request("203.0.113.3"))
        assert first.status_code == 200 and second.status_code == 200
        assert third.status_code == 503
        assert third.headers["retry-after"] == "45"
        assert json.loads(third.body)["error"] == "cluster_stream_busy"
        assert fake_env.client_count == 2
        await fake_env.aclose()

    asyncio.run(scenario())


def test_per_ip_cap_returns_429_and_other_ips_still_connect(fake_env, monkeypatch):
    server = _FakeK8s([[]])
    monkeypatch.setattr(cluster, "_incluster_client", server.client)
    monkeypatch.setenv("GLASSBOX_CLUSTER_STREAM_MAX_PER_IP", "2")

    async def scenario():
        server.live = asyncio.Queue()
        statuses = [
            (await cluster.cluster_stream(_request("198.51.100.7"))).status_code for _ in range(3)
        ]
        assert statuses == [200, 200, 429]
        rejected = await cluster.cluster_stream(_request("198.51.100.7"))
        assert rejected.headers["retry-after"] == str(cluster.DEFAULT_RETRY_AFTER_S)
        other = await cluster.cluster_stream(_request("198.51.100.8"))
        assert other.status_code == 200
        assert fake_env.client_count == 3
        await fake_env.aclose()

    asyncio.run(scenario())


def test_rejection_opens_no_upstream(fake_env, monkeypatch):
    """Over the cap nothing is started: no watch, no Redis client."""
    calls = []
    monkeypatch.setattr(cluster, "_incluster_client", lambda: calls.append("k8s"))
    limits = cluster.StreamLimits(max_clients=1)

    async def scenario():
        fake_env._bind_loop()
        fake_env._subscribers.add(cluster.Subscriber("x", 4))  # occupy the only slot
        assert fake_env.try_subscribe("y", limits) == "global"
        await asyncio.sleep(0.05)
        assert calls == [] and fake_env.upstream_starts == 0

    asyncio.run(scenario())


def test_released_slot_can_be_reused(fake_env, monkeypatch):
    server = _FakeK8s([[]])
    monkeypatch.setattr(cluster, "_incluster_client", server.client)
    limits = cluster.StreamLimits(max_clients=1, max_per_ip=1)

    async def scenario():
        server.live = asyncio.Queue()
        sub = fake_env.try_subscribe("a", limits)
        assert fake_env.try_subscribe("a", limits) == "global"
        fake_env.unsubscribe(sub)
        fake_env.unsubscribe(sub)  # idempotent: must not go negative
        assert fake_env.clients_for("a") == 0
        again = fake_env.try_subscribe("a", limits)
        assert isinstance(again, cluster.Subscriber)
        await fake_env.aclose()

    asyncio.run(scenario())


def test_limits_from_env_fall_back_to_safe_defaults(monkeypatch):
    monkeypatch.setenv("GLASSBOX_CLUSTER_STREAM_MAX_CLIENTS", "0")
    monkeypatch.setenv("GLASSBOX_CLUSTER_STREAM_MAX_PER_IP", "not-a-number")
    monkeypatch.setenv("GLASSBOX_CLUSTER_STREAM_MAX_LIFETIME_S", "-5")
    monkeypatch.setenv("GLASSBOX_CLUSTER_STREAM_HEARTBEAT_S", "")
    monkeypatch.setenv("GLASSBOX_CLUSTER_STREAM_RETRY_AFTER_S", "12")
    assert cluster.StreamLimits.from_env() == cluster.StreamLimits(retry_after_s=12)


# --- Fan-out --------------------------------------------------------------------


def test_many_clients_share_one_watch_and_one_redis_client(fake_env, monkeypatch):
    server = _FakeK8s([[_pod("retrieval-worker-a")]])
    monkeypatch.setattr(cluster, "_incluster_client", server.client)
    redis_clients = []

    def make_redis():
        redis_clients.append(_FakeRedis())
        return redis_clients[-1]

    monkeypatch.setattr(cluster, "_redis_client", make_redis)
    limits = cluster.StreamLimits()

    async def scenario():
        server.live = asyncio.Queue()
        subs = [fake_env.try_subscribe(f"ip-{i}", limits) for i in range(25)]
        for sub in subs:
            await _drain(sub, lambda item: item[0] == "synced")
        await server.live.put(
            json.dumps({"type": "MODIFIED", "object": _pod("retrieval-worker-a", ready=False)})
        )
        for sub in subs:
            items = await _drain(sub, lambda item: item[0] == "pod")
            assert items[-1][1]["ready"] is False
        assert server.list_calls == 1
        assert server.watch_calls == 1
        assert len(redis_clients) == 1
        assert fake_env.upstream_starts == 1
        await fake_env.aclose()

    asyncio.run(scenario())


def test_late_subscriber_gets_snapshot_without_a_new_watch(fake_env, monkeypatch):
    server = _FakeK8s([[_pod("retrieval-worker-a"), _pod("retrieval-worker-b", ready=False)]])
    monkeypatch.setattr(cluster, "_incluster_client", server.client)
    limits = cluster.StreamLimits()

    async def scenario():
        server.live = asyncio.Queue()
        first = fake_env.try_subscribe("one", limits)
        await _drain(first, lambda item: item[0] == "synced")
        await asyncio.sleep(0.05)  # let the backlog poll report once
        late = fake_env.try_subscribe("two", limits)
        snapshot = await _drain(late, lambda item: item[0] == "synced")
        pods = {payload["pod"]: payload for kind, payload in snapshot if kind == "pod"}
        assert set(pods) == {"retrieval-worker-a", "retrieval-worker-b"}
        assert all(payload["type"] == "ADDED" for payload in pods.values())
        assert ("backlog", {"backlog": 7}) in snapshot
        assert server.list_calls == 1 and server.watch_calls == 1
        await fake_env.aclose()

    asyncio.run(scenario())


def test_relist_after_watch_ends_reports_pods_that_vanished(fake_env, monkeypatch):
    server = _FakeK8s(
        [[_pod("retrieval-worker-a"), _pod("retrieval-worker-b")], [_pod("retrieval-worker-a")]],
        watch_lines=[],
    )
    monkeypatch.setattr(cluster, "_incluster_client", server.client)

    async def scenario():
        sub = fake_env.try_subscribe("one", cluster.StreamLimits())
        items = await _drain(sub, lambda item: item[0] == "pod" and item[1]["type"] == "DELETED")
        assert items[-1][1]["pod"] == "retrieval-worker-b"
        assert server.list_calls >= 2
        await fake_env.aclose()

    asyncio.run(scenario())


def test_watch_failure_retries_instead_of_ending_streams(fake_env, monkeypatch):
    server = _FakeK8s([[_pod("retrieval-worker-a")]])
    attempts = []

    def flaky_client():
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("apiserver hiccup")
        return server.client()

    monkeypatch.setattr(cluster, "_incluster_client", flaky_client)

    async def scenario():
        server.live = asyncio.Queue()
        sub = fake_env.try_subscribe("one", cluster.StreamLimits())
        items = await _drain(sub, lambda item: item[0] == "synced")
        assert all(kind != "cluster_unavailable" for kind, _ in items)
        assert len(attempts) == 2
        await fake_env.aclose()

    asyncio.run(scenario())


def test_slow_subscriber_is_dropped_not_buffered(fake_env, monkeypatch):
    server = _FakeK8s([[]])
    monkeypatch.setattr(cluster, "_incluster_client", server.client)
    hub = cluster.ClusterHub(queue_size=3, idle_linger_s=0.05)
    monkeypatch.setattr(cluster, "HUB", hub)

    async def scenario():
        server.live = asyncio.Queue()
        slow = hub.try_subscribe("slow", cluster.StreamLimits())
        await asyncio.sleep(0.05)
        for i in range(10):
            await server.live.put(
                json.dumps({"type": "MODIFIED", "object": _pod(f"retrieval-worker-{i}")})
            )
        await asyncio.sleep(0.05)
        assert slow.dropped
        assert hub.client_count == 0
        assert slow.queue.qsize() <= 3
        await hub.aclose()

    asyncio.run(scenario())


def test_upstream_stops_after_last_subscriber_leaves(fake_env, monkeypatch):
    server = _FakeK8s([[]])
    monkeypatch.setattr(cluster, "_incluster_client", server.client)

    async def scenario():
        server.live = asyncio.Queue()
        sub = fake_env.try_subscribe("one", cluster.StreamLimits())
        await _drain(sub, lambda item: item[0] == "synced")
        upstream = fake_env._upstream
        fake_env.unsubscribe(sub)
        await asyncio.sleep(0.2)
        assert upstream.done()
        assert server.closed_clients == 1

    asyncio.run(scenario())


def test_reconnect_within_linger_reuses_the_watch(fake_env, monkeypatch):
    server = _FakeK8s([[]])
    monkeypatch.setattr(cluster, "_incluster_client", server.client)
    hub = cluster.ClusterHub(idle_linger_s=1.0)
    monkeypatch.setattr(cluster, "HUB", hub)

    async def scenario():
        server.live = asyncio.Queue()
        sub = hub.try_subscribe("one", cluster.StreamLimits())
        await _drain(sub, lambda item: item[0] == "synced")
        hub.unsubscribe(sub)
        again = hub.try_subscribe("one", cluster.StreamLimits())
        await _drain(again, lambda item: item[0] == "synced")
        assert hub.upstream_starts == 1 and server.watch_calls == 1
        await hub.aclose()

    asyncio.run(scenario())


# --- Per-connection stream: heartbeat, lifetime, disconnect --------------------


def test_stream_sends_heartbeats_and_ends_at_max_lifetime(fake_env, monkeypatch):
    server = _FakeK8s([[]])
    monkeypatch.setattr(cluster, "_incluster_client", server.client)
    limits = cluster.StreamLimits(max_lifetime_s=0.5, heartbeat_s=0.1)

    async def scenario():
        server.live = asyncio.Queue()
        sub = fake_env.try_subscribe("one", limits)
        chunks = [chunk async for chunk in cluster._stream(fake_env, sub, _request(), limits)]
        text = "".join(chunks)
        assert text.count(": ping") >= 2
        assert _events(text)[-1][0] == "reconnect"
        assert fake_env.client_count == 0

    asyncio.run(scenario())


def test_disconnected_client_releases_its_slot(fake_env, monkeypatch):
    server = _FakeK8s([[]])
    monkeypatch.setattr(cluster, "_incluster_client", server.client)
    monkeypatch.setattr(cluster, "_DISCONNECT_POLL_S", 0.05)
    limits = cluster.StreamLimits(max_lifetime_s=60, heartbeat_s=60)
    gone = {"value": False}

    async def scenario():
        server.live = asyncio.Queue()
        sub = fake_env.try_subscribe("one", limits)

        async def consume():
            return [
                chunk
                async for chunk in cluster._stream(
                    fake_env, sub, _request(disconnected=lambda: gone["value"]), limits
                )
            ]

        task = asyncio.create_task(consume())
        await asyncio.sleep(0.1)
        assert fake_env.client_count == 1
        gone["value"] = True
        await asyncio.wait_for(task, timeout=1.0)
        assert fake_env.client_count == 0

    asyncio.run(scenario())


def test_cancelled_stream_releases_its_slot(fake_env, monkeypatch):
    """Starlette cancels the body iterator when the client goes away."""
    server = _FakeK8s([[]])
    monkeypatch.setattr(cluster, "_incluster_client", server.client)
    limits = cluster.StreamLimits(max_lifetime_s=60, heartbeat_s=60)

    async def scenario():
        server.live = asyncio.Queue()
        sub = fake_env.try_subscribe("one", limits)

        async def consume():
            async for _chunk in cluster._stream(fake_env, sub, _request(), limits):
                pass

        task = asyncio.create_task(consume())
        await asyncio.sleep(0.1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert fake_env.client_count == 0

    asyncio.run(scenario())


# --- Review round 1 regressions -------------------------------------------------


def test_release_for_a_body_that_never_started_runs_on_the_loop(fake_env, monkeypatch):
    """The response's background release must be async: a sync one runs in the
    threadpool, where the hub cannot arm its idle timer, so the upstream would
    run forever after the last client left before its body started."""
    server = _FakeK8s([[]])
    monkeypatch.setattr(cluster, "_incluster_client", server.client)

    async def scenario():
        server.live = asyncio.Queue()
        response = await cluster.cluster_stream(_request())
        assert response.status_code == 200
        assert fake_env.client_count == 1
        upstream = fake_env._upstream
        # Starlette runs this after the response even if the body never ran.
        await response.background()
        assert fake_env.client_count == 0
        await asyncio.sleep(0.2)
        assert upstream.done()
        assert server.closed_clients == 1

    asyncio.run(scenario())


def test_client_joining_while_the_hub_stops_gets_a_fresh_upstream(fake_env, monkeypatch):
    server = _FakeK8s([[_pod("retrieval-worker-a")]])
    monkeypatch.setattr(cluster, "_incluster_client", server.client)

    async def scenario():
        server.live = asyncio.Queue()
        server.close_gate = asyncio.Event()  # the old upstream's aclose blocks
        first = fake_env.try_subscribe("one", cluster.StreamLimits())
        await _drain(first, lambda item: item[0] == "synced")
        old = fake_env._upstream
        fake_env.unsubscribe(first)
        await asyncio.sleep(0.15)  # past the 0.05 s linger: old upstream cancelled
        assert old.cancelling() and not old.done()

        # The old upstream is still blocked in aclose while the client joins.
        late = fake_env.try_subscribe("two", cluster.StreamLimits())
        assert fake_env._upstream is not old
        snapshot = await _drain(late, lambda item: item[0] == "synced")
        assert any(kind == "pod" for kind, _ in snapshot)
        assert fake_env.upstream_starts == 2
        assert old.cancelling() and not old.done()  # the race really was open

        server.close_gate.set()  # now let the old upstream finish closing
        await asyncio.wait([old], timeout=2.0)
        assert old.done()
        # The finishing old upstream must not wipe the new one's state.
        assert fake_env._listed and "retrieval-worker-a" in fake_env._pods
        await fake_env.aclose()

    asyncio.run(scenario())


def test_watch_asks_for_a_server_timeout_and_relists_after_a_read_timeout(fake_env, monkeypatch):
    server = _FakeK8s([[_pod("retrieval-worker-a")]])
    server.watch_error = httpx.ReadTimeout("silent watch")
    monkeypatch.setattr(cluster, "_incluster_client", server.client)

    async def scenario():
        sub = fake_env.try_subscribe("one", cluster.StreamLimits())
        items = await _drain(sub, lambda item: item[0] == "synced")
        await _drain(sub, lambda item: item[0] == "synced")  # a second list
        assert all(kind != "cluster_unavailable" for kind, _ in items)
        assert server.list_calls >= 2
        assert server.watch_params[0]["timeoutSeconds"] == str(cluster._WATCH_TIMEOUT_S)
        await fake_env.aclose()

    asyncio.run(scenario())


def test_dropped_slow_client_is_told_to_reconnect(fake_env, monkeypatch):
    async def scenario():
        sub = cluster.Subscriber("slow", 4)
        sub.queue.put_nowait(
            ("pod", {"type": "ADDED", "pod": "a", "phase": "Running", "ready": True})
        )
        sub.dropped = True
        text = "".join([chunk async for chunk in cluster._subscriber_events(sub, 60)])
        assert _events(text) == [("reconnect", {})]

    asyncio.run(scenario())


# --- Review round 2 regressions -------------------------------------------------


def test_join_while_stopping_gets_no_stale_snapshot(fake_env, monkeypatch):
    """A pod that vanished must not reappear as a ghost from the old state."""
    server = _FakeK8s([[_pod("retrieval-worker-a"), _pod("retrieval-worker-b")]])
    monkeypatch.setattr(cluster, "_incluster_client", server.client)

    async def scenario():
        server.live = asyncio.Queue()
        server.close_gate = asyncio.Event()  # the old upstream's close blocks
        first = fake_env.try_subscribe("one", cluster.StreamLimits())
        await _drain(first, lambda item: item[0] == "synced")
        old = fake_env._upstream
        fake_env.unsubscribe(first)
        await asyncio.sleep(0.15)
        assert old.cancelling() and not old.done()

        server.listings = [[_pod("retrieval-worker-a")]]  # b is gone now
        late = fake_env.try_subscribe("two", cluster.StreamLimits())
        server.close_gate.set()
        snapshot = await _drain(late, lambda item: item[0] == "synced")
        pods = {payload["pod"] for kind, payload in snapshot if kind == "pod"}
        assert pods == {"retrieval-worker-a"}
        await fake_env.aclose()

    asyncio.run(scenario())


def test_stopping_upstream_silences_its_poller_before_closing_the_client(fake_env, monkeypatch):
    server = _FakeK8s([[]])
    monkeypatch.setattr(cluster, "_incluster_client", server.client)
    polls = []

    class _CountingRedis(_FakeRedis):
        async def xinfo_groups(self, stream):
            polls.append(1)
            return await super().xinfo_groups(stream)

    monkeypatch.setattr(cluster, "_redis_client", _CountingRedis)

    async def scenario():
        server.live = asyncio.Queue()
        server.close_gate = asyncio.Event()  # the close hangs while stopping
        sub = fake_env.try_subscribe("one", cluster.StreamLimits())
        await _drain(sub, lambda item: item[0] == "synced")
        old = fake_env._upstream
        fake_env.unsubscribe(sub)
        await asyncio.sleep(0.15)  # linger passed; old upstream stuck in aclose
        assert old.cancelling() and not old.done()
        count = len(polls)
        await asyncio.sleep(0.1)  # ten poll intervals
        assert len(polls) == count
        server.close_gate.set()
        await asyncio.sleep(0.05)
        assert old.done()

    asyncio.run(scenario())


def test_aclose_is_bounded_when_an_upstream_close_never_returns(fake_env, monkeypatch):
    server = _FakeK8s([[_pod("retrieval-worker-a")]])
    monkeypatch.setattr(cluster, "_incluster_client", server.client)
    monkeypatch.setattr(cluster, "_CLOSE_TIMEOUT_S", 0.2)

    async def scenario():
        server.live = asyncio.Queue()
        server.close_gate = asyncio.Event()  # never set: the client close hangs
        sub = fake_env.try_subscribe("one", cluster.StreamLimits())
        await _drain(sub, lambda item: item[0] == "synced")
        loop = asyncio.get_running_loop()
        started = loop.time()
        await asyncio.wait_for(fake_env.aclose(), timeout=2.0)
        assert loop.time() - started < 1.0
        assert fake_env._upstream is None
        assert server.closed_clients == 0  # the close really was stuck

    asyncio.run(scenario())


# --- Test-depth follow-ups from the #99 review ---------------------------------


def test_read_timeout_relists_after_the_restart_delay_not_the_error_backoff(fake_env, monkeypatch):
    server = _FakeK8s([[_pod("retrieval-worker-a")]])
    server.watch_error = httpx.ReadTimeout("silent watch")
    monkeypatch.setattr(cluster, "_incluster_client", server.client)
    # Distinct, recognisable values; the sleeps are recorded and skipped.
    monkeypatch.setattr(cluster, "_WATCH_RESTART_S", 1.0)
    monkeypatch.setattr(cluster, "_WATCH_RETRY_BASE_S", 2.0)
    delays = []

    async def recording_sleep(delay):
        delays.append(delay)  # recorded and skipped
        await asyncio.sleep(0)

    monkeypatch.setattr(cluster, "_sleep", recording_sleep)

    async def scenario():
        sub = fake_env.try_subscribe("one", cluster.StreamLimits())
        await _drain(sub, lambda item: item[0] == "synced")
        await _drain(sub, lambda item: item[0] == "synced")  # a second list
        await fake_env.aclose()

    asyncio.run(scenario())
    assert delays and set(delays) == {1.0}


def test_aclose_waits_for_the_cancelled_upstream_to_finish(fake_env, monkeypatch):
    server = _FakeK8s([[_pod("retrieval-worker-a")]])
    monkeypatch.setattr(cluster, "_incluster_client", server.client)

    async def scenario():
        problems = []
        asyncio.get_running_loop().set_exception_handler(
            lambda _loop, context: problems.append(context.get("message", ""))
        )
        server.live = asyncio.Queue()
        server.close_gate = asyncio.Event()
        sub = fake_env.try_subscribe("one", cluster.StreamLimits())
        await _drain(sub, lambda item: item[0] == "synced")
        old = fake_env._upstream
        fake_env.unsubscribe(sub)
        await asyncio.sleep(0.15)  # linger passed: cancelled, stuck in aclose
        assert old.cancelling() and not old.done()
        assert fake_env._upstream is None
        del old
        gc.collect()  # only the hub's own reference keeps the task alive now

        closing = asyncio.ensure_future(fake_env.aclose())
        await asyncio.sleep(0.05)
        assert not closing.done()  # aclose is waiting on the cancelled upstream
        assert server.closed_clients == 0
        server.close_gate.set()
        await asyncio.wait_for(closing, timeout=2.0)
        assert server.closed_clients == 1  # the old client really finished closing
        assert not fake_env._stopping
        gc.collect()
        assert not [m for m in problems if "destroyed" in m]

    asyncio.run(scenario())
