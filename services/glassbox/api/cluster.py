"""Stream retrieval-worker pod status and queue backlog (DESIGN.md §9.4/§9.5).

Reads pod events through the Kubernetes API using the tightly-scoped
ServiceAccount described in DESIGN.md §9.5 (`get`/`list`/`watch` on `pods` in
the `app` namespace only — see k8s/base/rbac-api.yaml). Only pod name, phase
and readiness ever leave the cluster; nothing else about the pod object is
forwarded.
"""

import asyncio
import json
import logging
import os
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import redis.asyncio as redis
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from services.glassbox.api.sse import frame
from services.glassbox.worker.main import GROUP_NAME, STREAM_NAME

router = APIRouter()
LOGGER = logging.getLogger(__name__)

_SA_DIR = Path("/var/run/secrets/kubernetes.io/serviceaccount")
_NAMESPACE = "app"
_LABEL_SELECTOR = "app=retrieval-worker"
_BACKLOG_POLL_S = 2.0


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
        timeout=httpx.Timeout(10.0, read=None),
    )
    return client, base_url


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


async def _iter_pod_events(client: httpx.AsyncClient) -> AsyncIterator[dict]:
    path = f"/api/v1/namespaces/{_NAMESPACE}/pods"
    response = await client.get(path, params={"labelSelector": _LABEL_SELECTOR})
    response.raise_for_status()
    listing = response.json()
    resource_version = listing.get("metadata", {}).get("resourceVersion")
    for item in listing.get("items", []):
        yield _pod_event("ADDED", item)

    async with client.stream(
        "GET",
        path,
        params={
            "labelSelector": _LABEL_SELECTOR,
            "watch": "true",
            "resourceVersion": resource_version,
        },
    ) as watch_response:
        watch_response.raise_for_status()
        async for line in watch_response.aiter_lines():
            if not line.strip():
                continue
            event = json.loads(line)
            yield _pod_event(event.get("type", "MODIFIED"), event.get("object", {}))


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


async def _stream(request: Request) -> AsyncIterator[str]:
    queue: asyncio.Queue = asyncio.Queue()
    stop = asyncio.Event()

    async def watch_pods() -> None:
        try:
            client, _ = _incluster_client()
        except ClusterUnavailable as exc:
            await queue.put(("cluster_unavailable", {"message": str(exc)}))
            stop.set()
            return
        try:
            async for event in _iter_pod_events(client):
                await queue.put(("pod", event))
        except Exception:
            LOGGER.exception("pod watch failed")
            await queue.put(("cluster_unavailable", {"message": "pod watch failed"}))
        finally:
            await client.aclose()
            stop.set()

    async def poll_backlog() -> None:
        try:
            client = redis.from_url(os.environ["REDIS_URL"])
        except Exception:
            LOGGER.exception("backlog poll could not start")
            return
        try:
            # Emit one reading immediately (so the counter shows up right
            # away rather than after the first poll interval), then keep
            # polling until the pod watch ends.
            while True:
                await queue.put(("backlog", {"backlog": await _queue_backlog(client)}))
                if stop.is_set():
                    break
                await asyncio.sleep(_BACKLOG_POLL_S)
        except Exception:
            LOGGER.exception("backlog poll failed")
        finally:
            await client.aclose()

    watch_task = asyncio.create_task(watch_pods())
    poll_task = asyncio.create_task(poll_backlog())
    try:
        while True:
            if watch_task.done() and poll_task.done() and queue.empty():
                break
            if await request.is_disconnected():
                break
            try:
                kind, payload = await asyncio.wait_for(queue.get(), timeout=1.0)
            except TimeoutError:
                continue
            yield frame(kind, payload)
            if kind == "cluster_unavailable":
                break
    finally:
        stop.set()
        for task in (watch_task, poll_task):
            task.cancel()
        for task in (watch_task, poll_task):
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass


@router.get("/api/cluster/stream")
async def cluster_stream(request: Request) -> StreamingResponse:
    return StreamingResponse(
        _stream(request),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
