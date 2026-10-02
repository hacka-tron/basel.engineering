"""Stream retrieval-worker pod status and queue backlog (DESIGN.md §9.4/§9.5).

Reads pod events through the Kubernetes API using the tightly-scoped
ServiceAccount described in DESIGN.md §9.5 (`get`/`list`/`watch` on `pods` in
the `app` namespace only — see k8s/base/rbac-api.yaml). Only pod name, phase
and readiness ever leave the cluster; nothing else about the pod object is
forwarded.

The endpoint is public and unauthenticated, so its cost must not grow with the
number of visitors (DESIGN.md §9.5, "Bounded cost"):

- One shared upstream per api process (`ClusterHub`): a single Kubernetes
  list+watch and a single Redis client polling the backlog, started by the
  first subscriber and stopped shortly after the last one leaves. Each SSE
  client only gets a bounded in-process queue fed by that upstream.
- A global cap and a per-IP cap on concurrent streams. Over a cap the endpoint
  answers 503 (global) or 429 (per IP) with `Retry-After` and opens nothing.
- A maximum connection lifetime (with jitter): the server sends `reconnect`
  and closes, and the client reconnects, so a dead client is reaped even if
  its TCP connection never reports the disconnect.
- `: ping` heartbeats keep proxies from idling out a quiet stream and make a
  dead peer's socket fail, so its slot is released.

A subscriber that falls more than `_SUBSCRIBER_QUEUE_SIZE` events behind is
dropped (it reconnects and gets a fresh snapshot) instead of buffering without
bound.
"""

import asyncio
import contextlib
import json
import logging
import os
import random
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

import httpx
import redis.asyncio as redis
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from starlette.background import BackgroundTask

from services.glassbox.api.sse import frame, with_heartbeat
from services.glassbox.limits import client_ip_hash
from services.glassbox.worker.main import GROUP_NAME, STREAM_NAME

router = APIRouter()
LOGGER = logging.getLogger(__name__)

_SA_DIR = Path("/var/run/secrets/kubernetes.io/serviceaccount")
_NAMESPACE = "app"
_LABEL_SELECTOR = "app=retrieval-worker"
_BACKLOG_POLL_S = 2.0

# Defaults for the configurable limits (ConfigMap keys in
# k8s/base/configmap-app.yaml; DESIGN.md §9.5 explains the sizing).
DEFAULT_MAX_CLIENTS = 100
DEFAULT_MAX_PER_IP = 5
DEFAULT_MAX_LIFETIME_S = 600.0
DEFAULT_HEARTBEAT_S = 15.0
DEFAULT_RETRY_AFTER_S = 30

# Events a subscriber may lag behind before it is dropped. A snapshot is one
# event per worker pod (at most 3) plus backlog and `synced`.
_SUBSCRIBER_QUEUE_SIZE = 256
# Keep the upstream running this long after the last subscriber leaves, so
# lifetime reconnects and page reloads do not restart the watch each time.
_IDLE_LINGER_S = 30.0
# Pause before re-listing after the API server ends a watch normally, and the
# backoff after a failed list/watch.
_WATCH_RESTART_S = 1.0
_WATCH_RETRY_BASE_S = 2.0
_WATCH_RETRY_MAX_S = 30.0
# Upper bound on how long aclose() waits for upstream tasks to finish closing
# their clients: comfortably below uvicorn's --timeout-graceful-shutdown 25 and
# the pod's 30 s grace, so a close that never returns cannot hang shutdown.
_CLOSE_TIMEOUT_S = 10.0
# Indirection so tests can intercept the watch restart/backoff pause without
# patching asyncio.sleep globally.
_sleep = asyncio.sleep
# Ask the API server to end each watch after this long (a normal re-list
# follows), and give up on a watch that sends nothing for longer than that
# plus a margin: a half-open watch would otherwise freeze every viewer.
_WATCH_TIMEOUT_S = 300
_WATCH_READ_TIMEOUT_S = _WATCH_TIMEOUT_S + 30.0
# How often a stream checks whether its client is still there.
_DISCONNECT_POLL_S = 5.0
# `retry:` hint for EventSource clients that reconnect on their own (a tab
# still running a bundle from before this change).
_CLIENT_RETRY_MS = 5000
# Lifetime jitter: each stream lives between this fraction and 1x the maximum,
# so clients that connected together do not all reconnect together.
_LIFETIME_JITTER_FLOOR = 0.8

_CLOSE = object()


def _positive_int(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, ""))
    except ValueError:
        return default
    return value if value > 0 else default


def _positive_float(name: str, default: float) -> float:
    try:
        value = float(os.getenv(name, ""))
    except ValueError:
        return default
    return value if value > 0 else default


@dataclass(frozen=True)
class StreamLimits:
    max_clients: int = DEFAULT_MAX_CLIENTS
    max_per_ip: int = DEFAULT_MAX_PER_IP
    max_lifetime_s: float = DEFAULT_MAX_LIFETIME_S
    heartbeat_s: float = DEFAULT_HEARTBEAT_S
    retry_after_s: int = DEFAULT_RETRY_AFTER_S

    @classmethod
    def from_env(cls) -> "StreamLimits":
        """Read the limits; a missing, unparsable or non-positive value uses the default."""
        return cls(
            max_clients=_positive_int("GLASSBOX_CLUSTER_STREAM_MAX_CLIENTS", DEFAULT_MAX_CLIENTS),
            max_per_ip=_positive_int("GLASSBOX_CLUSTER_STREAM_MAX_PER_IP", DEFAULT_MAX_PER_IP),
            max_lifetime_s=_positive_float(
                "GLASSBOX_CLUSTER_STREAM_MAX_LIFETIME_S", DEFAULT_MAX_LIFETIME_S
            ),
            heartbeat_s=_positive_float("GLASSBOX_CLUSTER_STREAM_HEARTBEAT_S", DEFAULT_HEARTBEAT_S),
            retry_after_s=_positive_int(
                "GLASSBOX_CLUSTER_STREAM_RETRY_AFTER_S", DEFAULT_RETRY_AFTER_S
            ),
        )


class ClusterUnavailable(Exception):
    """Raised when this process has no in-cluster Kubernetes API access.

    Expected and handled gracefully in local/dev environments (no
    ServiceAccount token mounted, no KUBERNETES_SERVICE_HOST) — the endpoint
    reports this as a single `cluster_unavailable` SSE event rather than
    crashing, so the frontend can fall back to mock/demo data.
    """


def _incluster_client() -> tuple[httpx.AsyncClient, str]:
    token_path = _SA_DIR / "token"
    ca_path = _SA_DIR / "ca.crt"
    host = os.environ.get("KUBERNETES_SERVICE_HOST")
    port = os.environ.get("KUBERNETES_SERVICE_PORT_HTTPS") or os.environ.get(
        "KUBERNETES_SERVICE_PORT"
    )
    if not (token_path.is_file() and ca_path.is_file() and host and port):
        raise ClusterUnavailable("no in-cluster Kubernetes API access")
    token = token_path.read_text().strip()
    base_url = f"https://{host}:{port}"
    client = httpx.AsyncClient(
        base_url=base_url,
        verify=str(ca_path),
        headers={"Authorization": f"Bearer {token}"},
        timeout=httpx.Timeout(10.0, read=_WATCH_READ_TIMEOUT_S),
    )
    return client, base_url


def _redis_client():
    return redis.from_url(os.environ["REDIS_URL"])


def _pod_event(event_type: str, pod: dict) -> dict:
    name = pod.get("metadata", {}).get("name", "")
    status = pod.get("status", {})
    phase = status.get("phase", "Unknown")
    conditions = status.get("conditions") or []
    ready = any(
        condition.get("type") == "Ready" and condition.get("status") == "True"
        for condition in conditions
    )
    return {"type": event_type, "pod": name, "phase": phase, "ready": ready}


_PODS_PATH = f"/api/v1/namespaces/{_NAMESPACE}/pods"


async def _list_pods(client: httpx.AsyncClient) -> tuple[list[dict], str | None]:
    response = await client.get(_PODS_PATH, params={"labelSelector": _LABEL_SELECTOR})
    response.raise_for_status()
    listing = response.json()
    events = [_pod_event("ADDED", item) for item in listing.get("items", [])]
    return events, listing.get("metadata", {}).get("resourceVersion")


async def _watch_pods(client: httpx.AsyncClient, resource_version: str | None):
    """Yield pod events until the API server ends the watch or reports an error."""
    params = {
        "labelSelector": _LABEL_SELECTOR,
        "watch": "true",
        "timeoutSeconds": str(_WATCH_TIMEOUT_S),
    }
    if resource_version:
        params["resourceVersion"] = resource_version
    async with client.stream("GET", _PODS_PATH, params=params) as watch_response:
        watch_response.raise_for_status()
        async for line in watch_response.aiter_lines():
            if not line.strip():
                continue
            event = json.loads(line)
            event_type = event.get("type", "MODIFIED")
            if event_type == "BOOKMARK":
                continue
            if event_type == "ERROR":
                # Typically 410 Gone (resourceVersion too old): re-list.
                return
            yield _pod_event(event_type, event.get("object", {}))


async def _queue_backlog(redis_client) -> int:
    """Same metric KEDA's redis-streams trigger watches (DESIGN.md §9.3)."""
    try:
        groups = await redis_client.xinfo_groups(STREAM_NAME)
    except Exception:
        return 0
    for group in groups:
        name = group.get(b"name", group.get("name"))
        if isinstance(name, bytes):
            name = name.decode()
        if name == GROUP_NAME:
            lag = group.get(b"lag", group.get("lag"))
            return int(lag) if lag is not None else 0
    return 0


class Subscriber:
    """One SSE client: a bounded queue the shared upstream writes into.

    `dropped` means "stop now, without draining": set when the client fell too
    far behind. A normal end is the `_CLOSE` item, queued after any final event.
    """

    __slots__ = ("ip_key", "queue", "dropped")

    def __init__(self, ip_key: str, maxsize: int):
        self.ip_key = ip_key
        self.queue: asyncio.Queue = asyncio.Queue(maxsize)
        self.dropped = False


class ClusterHub:
    """Fans one Kubernetes watch and one backlog poll out to many SSE clients.

    All bookkeeping runs on the event loop thread without awaits between the
    check and the update, so the caps cannot be raced past.
    """

    def __init__(
        self,
        *,
        queue_size: int = _SUBSCRIBER_QUEUE_SIZE,
        idle_linger_s: float = _IDLE_LINGER_S,
    ):
        self._queue_size = queue_size
        self._idle_linger_s = idle_linger_s
        self._subscribers: set[Subscriber] = set()
        self._per_ip: dict[str, int] = {}
        self._pods: dict[str, dict] = {}
        self._listed = False
        self._backlog: int | None = None
        self._upstream: asyncio.Task | None = None
        self._linger: asyncio.Task | None = None
        # Cancelled upstreams still closing their clients. The loop only keeps
        # weak references to tasks, so hold them here: they are not garbage
        # collected mid-close, and `aclose` can wait for them.
        self._stopping: set[asyncio.Task] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self.upstream_starts = 0  # For tests and logs: how many times the watch started.

    @property
    def client_count(self) -> int:
        return len(self._subscribers)

    def clients_for(self, ip_key: str) -> int:
        return self._per_ip.get(ip_key, 0)

    def _bind_loop(self) -> None:
        # One event loop per process in production. A new loop (tests create
        # one per TestClient) means any task from the old loop is dead.
        loop = asyncio.get_running_loop()
        if self._loop is not loop:
            self._loop = loop
            self._subscribers.clear()
            self._per_ip.clear()
            self._reset_state()
            self._upstream = None
            self._linger = None
            self._stopping.clear()

    def _reset_state(self) -> None:
        self._pods.clear()
        self._listed = False
        self._backlog = None

    def try_subscribe(self, ip_key: str, limits: StreamLimits) -> Subscriber | str:
        """Register a client, or return "global" / "per_ip" naming the cap it hit."""
        self._bind_loop()
        if len(self._subscribers) >= limits.max_clients:
            return "global"
        if self._per_ip.get(ip_key, 0) >= limits.max_per_ip:
            return "per_ip"
        subscriber = Subscriber(ip_key, self._queue_size)
        self._subscribers.add(subscriber)
        self._per_ip[ip_key] = self._per_ip.get(ip_key, 0) + 1
        if self._linger is not None:
            self._linger.cancel()
            self._linger = None
        if self._upstream is None or self._upstream.done():
            # Start fresh before serving any snapshot: state left by an
            # upstream that is still shutting down may be stale (a pod that
            # vanished would linger as a ghost). The new list provides it.
            self._reset_state()
            self.upstream_starts += 1
            self._upstream = asyncio.get_running_loop().create_task(self._run_upstream())
        if self._listed:
            for event in self._pods.values():
                self._offer(subscriber, ("pod", {**event, "type": "ADDED"}))
            if self._backlog is not None:
                self._offer(subscriber, ("backlog", {"backlog": self._backlog}))
            self._offer(subscriber, ("synced", {}))
        elif self._backlog is not None:
            self._offer(subscriber, ("backlog", {"backlog": self._backlog}))
        return subscriber

    def unsubscribe(self, subscriber: Subscriber) -> None:
        """Release a client's slot. Idempotent and synchronous (safe in cleanup)."""
        if subscriber not in self._subscribers:
            return
        self._subscribers.discard(subscriber)
        remaining = self._per_ip.get(subscriber.ip_key, 0) - 1
        if remaining > 0:
            self._per_ip[subscriber.ip_key] = remaining
        else:
            self._per_ip.pop(subscriber.ip_key, None)
        if not self._subscribers and self._upstream is not None and not self._upstream.done():
            if self._linger is None or self._linger.done():
                self._linger = asyncio.get_running_loop().create_task(self._stop_when_idle())

    async def _stop_when_idle(self) -> None:
        await asyncio.sleep(self._idle_linger_s)
        if not self._subscribers and self._upstream is not None:
            # Forget it at once: the cancelled task may still be closing its
            # clients, and a subscriber arriving meanwhile must start a fresh
            # upstream rather than wait on one that is going away.
            stopping = self._upstream
            self._upstream = None
            self._stopping.add(stopping)
            stopping.add_done_callback(self._stopping.discard)
            stopping.cancel()

    def _offer(self, subscriber: Subscriber, item) -> None:
        try:
            subscriber.queue.put_nowait(item)
        except asyncio.QueueFull:
            # Too slow to keep up: drop it rather than buffer without bound.
            # Its stream ends and the client reconnects to a fresh snapshot.
            LOGGER.warning("cluster stream subscriber fell behind; dropping it")
            subscriber.dropped = True
            self.unsubscribe(subscriber)

    def _broadcast(self, kind: str, payload: dict) -> None:
        for subscriber in list(self._subscribers):
            self._offer(subscriber, (kind, payload))

    def _close_all(self, final: tuple[str, dict] | None = None) -> None:
        for subscriber in list(self._subscribers):
            if final is not None:
                self._offer(subscriber, final)
            try:
                subscriber.queue.put_nowait(_CLOSE)
            except asyncio.QueueFull:
                subscriber.dropped = True
            self.unsubscribe(subscriber)

    def _apply_listing(self, events: list[dict]) -> None:
        current = {event["pod"]: event for event in events}
        for name, old in list(self._pods.items()):
            if name not in current:
                self._broadcast("pod", {**old, "type": "DELETED"})
        self._pods = current
        for event in events:
            self._broadcast("pod", event)
        self._listed = True
        self._broadcast("synced", {})

    def _apply_event(self, event: dict) -> None:
        if event["type"] == "DELETED":
            self._pods.pop(event["pod"], None)
        else:
            self._pods[event["pod"]] = event
        self._broadcast("pod", event)

    async def _run_upstream(self) -> None:
        backlog_task = asyncio.get_running_loop().create_task(self._poll_backlog())
        try:
            await self._watch_loop(backlog_task)
        finally:
            backlog_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await backlog_task
            # A replacement upstream may already be running (started while
            # this one was stopping); its state is not ours to clear.
            if self._upstream is None or self._upstream is asyncio.current_task():
                self._reset_state()

    async def _watch_loop(self, backlog_task: asyncio.Task) -> None:
        failures = 0
        while True:
            try:
                client, _ = _incluster_client()
            except ClusterUnavailable as exc:
                self._close_all(("cluster_unavailable", {"message": str(exc)}))
                return
            except Exception:
                client = None
                failures += 1
                LOGGER.exception("could not create the Kubernetes client (attempt %d)", failures)
            try:
                if client is not None:
                    try:
                        events, resource_version = await _list_pods(client)
                        self._apply_listing(events)
                        failures = 0
                        async for event in _watch_pods(client, resource_version):
                            self._apply_event(event)
                    finally:
                        task = asyncio.current_task()
                        if task is not None and task.cancelling():
                            # Stopping: silence the poller before the
                            # (possibly slow) client close, so it cannot
                            # broadcast after a replacement upstream starts.
                            backlog_task.cancel()
                        await client.aclose()
            except asyncio.CancelledError:
                raise
            except httpx.ReadTimeout:
                # A watch silent past the server's own timeout: treat it as
                # half-open and re-list, like a normal end.
                LOGGER.warning("pod watch read timed out; re-listing")
            except Exception:
                failures += 1
                LOGGER.exception("pod watch failed (attempt %d); retrying", failures)
            if failures:
                delay = min(_WATCH_RETRY_MAX_S, _WATCH_RETRY_BASE_S * 2 ** (failures - 1))
            else:
                delay = _WATCH_RESTART_S
            await _sleep(delay)

    async def _poll_backlog(self) -> None:
        try:
            client = _redis_client()
        except Exception:
            LOGGER.exception("backlog poll could not start")
            return
        try:
            while True:
                value = await _queue_backlog(client)
                if value != self._backlog:
                    self._backlog = value
                    self._broadcast("backlog", {"backlog": value})
                await asyncio.sleep(_BACKLOG_POLL_S)
        finally:
            with contextlib.suppress(Exception):
                await client.aclose()

    async def aclose(self) -> None:
        """Stop the upstream and end every stream (api shutdown)."""
        if self._loop is not asyncio.get_running_loop():
            return
        self._close_all()
        stopping = list(self._stopping)
        for task in (self._linger, self._upstream):
            if task is not None and not task.done():
                task.cancel()
                stopping.append(task)
        # Wait for every cancelled upstream to finish closing its clients.
        # Already-cancelled ones are only awaited, not cancelled again, which
        # would cut their close short.
        if stopping:
            _, pending = await asyncio.wait(stopping, timeout=_CLOSE_TIMEOUT_S)
            if pending:
                LOGGER.warning(
                    "%d cluster upstream task(s) still closing after %.0fs; abandoning them",
                    len(pending),
                    _CLOSE_TIMEOUT_S,
                )
            for task in stopping:
                if task.done() and not task.cancelled():
                    task.exception()  # mark retrieved; nothing to raise at shutdown
        self._linger = None
        self._upstream = None


HUB = ClusterHub()


async def _subscriber_events(subscriber: Subscriber, lifetime_s: float) -> AsyncIterator[str]:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + lifetime_s
    yield f"retry: {_CLIENT_RETRY_MS}\n\n"
    while True:
        remaining = deadline - loop.time()
        if remaining <= 0:
            # Planned end of this connection; the client reconnects promptly.
            yield frame("reconnect", {})
            return
        try:
            item = await asyncio.wait_for(subscriber.queue.get(), timeout=remaining)
        except TimeoutError:
            continue
        if item is _CLOSE:
            return
        if subscriber.dropped:
            # Fell behind and lost its slot: send it down the short
            # reconnect path to a fresh snapshot.
            yield frame("reconnect", {})
            return
        kind, payload = item
        yield frame(kind, payload)
        if kind == "cluster_unavailable":
            return


async def _stream(
    hub: ClusterHub, subscriber: Subscriber, request: Request, limits: StreamLimits
) -> AsyncIterator[str]:
    lifetime_s = limits.max_lifetime_s * random.uniform(_LIFETIME_JITTER_FLOOR, 1.0)
    try:
        async for chunk in with_heartbeat(
            _subscriber_events(subscriber, lifetime_s),
            interval_s=limits.heartbeat_s,
            is_disconnected=request.is_disconnected,
            poll_interval_s=_DISCONNECT_POLL_S,
        ):
            yield chunk
    finally:
        hub.unsubscribe(subscriber)


async def _release(hub: ClusterHub, subscriber: Subscriber) -> None:
    hub.unsubscribe(subscriber)


@router.get("/api/cluster/stream")
async def cluster_stream(request: Request) -> Response:
    limits = StreamLimits.from_env()
    hub = HUB
    subscriber = hub.try_subscribe(client_ip_hash(request), limits)
    if isinstance(subscriber, str):
        global_cap = subscriber == "global"
        return JSONResponse(
            {
                "error": "cluster_stream_busy" if global_cap else "too_many_cluster_streams",
                "retry_after_s": limits.retry_after_s,
            },
            status_code=503 if global_cap else 429,
            headers={"Retry-After": str(limits.retry_after_s), "Cache-Control": "no-store"},
        )
    return StreamingResponse(
        _stream(hub, subscriber, request, limits),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        # Also release the slot if the body never started (client gone before
        # the first chunk); unsubscribe is idempotent. Async on purpose: a
        # sync background function runs in Starlette's threadpool, off the
        # event loop, where the hub's bookkeeping and idle timer cannot run.
        background=BackgroundTask(_release, hub, subscriber),
    )
