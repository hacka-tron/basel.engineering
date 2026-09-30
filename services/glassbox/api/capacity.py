"""Conservative memory gate for the optional KEDA stress test.

A real burst scales retrieval-worker from 1 to 5 replicas, so the node needs
enough *currently free* memory for four more workers at their memory limit,
plus a margin for rollout Jobs and transient spikes. Free memory is the node's
allocatable memory minus its live usage from metrics-server (bundled with
k3s); comparing a static estimate against total allocatable would approve a
busy node. Anything that can't be established — no cluster access, more than
one node, no metrics, a missing or unknown MemoryPressure condition — denies,
and the frontend falls back to the visual-only demo.

The check and the enqueue in POST /api/demo/load are not atomic; the margin
covers usage drifting between them, and the burst is short-lived.
"""

import asyncio
import logging

from fastapi import APIRouter

from services.glassbox.api.cluster import _incluster_client

router = APIRouter()
LOGGER = logging.getLogger(__name__)

# MiB. Four extra retrieval-worker replicas at their memory *limit*
# (k8s/base/worker-deployment.yaml), not their request or typical usage.
EXTRA_WORKERS_MI = 4 * 128
SAFETY_MARGIN_MI = 256
REQUIRED_FREE_MI = EXTRA_WORKERS_MI + SAFETY_MARGIN_MI

_NODES_PATH = "/api/v1/nodes"
_NODE_METRICS_PATH = "/apis/metrics.k8s.io/v1beta1/nodes"


def _memory_mi(quantity: str) -> int:
    """Convert Kubernetes memory quantities to whole MiB, rounded down."""
    multipliers = {"Ki": 1 / 1024, "Mi": 1, "Gi": 1024, "Ti": 1024 * 1024}
    for suffix, multiplier in multipliers.items():
        if quantity.endswith(suffix):
            return int(float(quantity[: -len(suffix)]) * multiplier)
    return int(int(quantity) / (1024 * 1024))


def _denied(reason: str) -> dict[str, bool | str]:
    return {"sufficient": False, "reason": reason}


async def assess_capacity() -> dict[str, bool | str]:
    """Deny unless live free memory on the single node covers the burst."""
    try:
        client, _ = _incluster_client()
        try:
            nodes_response, metrics_response = await asyncio.wait_for(
                asyncio.gather(client.get(_NODES_PATH), client.get(_NODE_METRICS_PATH)),
                timeout=5,
            )
            nodes_response.raise_for_status()
            metrics_response.raise_for_status()
            nodes = nodes_response.json().get("items", [])
            metrics = metrics_response.json().get("items", [])
        finally:
            await client.aclose()

        if len(nodes) != 1:
            return _denied("Node capacity is unknown.")
        node = nodes[0]
        pressure = next(
            (c for c in node["status"].get("conditions", []) if c.get("type") == "MemoryPressure"),
            None,
        )
        if pressure is None or pressure.get("status") != "False":
            return _denied("The node's memory pressure status is not confirmed healthy.")

        name = node["metadata"]["name"]
        usage = next((m for m in metrics if m.get("metadata", {}).get("name") == name), None)
        if usage is None:
            return _denied("Live node memory usage is unavailable.")

        allocatable_mi = _memory_mi(node["status"]["allocatable"]["memory"])
        used_mi = _memory_mi(usage["usage"]["memory"])
        free_mi = allocatable_mi - used_mi
        reason = f"Burst needs {REQUIRED_FREE_MI} MiB free; node has {free_mi} MiB free."
        return {"sufficient": free_mi >= REQUIRED_FREE_MI, "reason": reason}
    except Exception:
        LOGGER.warning("Could not verify node memory capacity", exc_info=True)
        return _denied("Cluster capacity is unavailable.")


@router.get("/api/demo/capacity")
async def demo_capacity() -> dict[str, bool | str]:
    return await assess_capacity()
