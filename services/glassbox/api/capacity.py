"""Conservative memory gate for the optional KEDA stress test."""

import asyncio
import logging

from fastapi import APIRouter

from services.glassbox.api.cluster import _incluster_client

router = APIRouter()
LOGGER = logging.getLogger(__name__)

# MiB: declared requests for always-on app/data pods, plus the DESIGN.md §9.7
# allowances for k3s, system pods, Flux and KEDA. The four added workers are
# budgeted at their observed ~90Mi each, above their 64Mi scheduler requests.
# A 256Mi margin covers transient rollout/job pressure on this single node.
BASELINE_MI = 600 + 100 + 150 + 150 + 200 + 60 + 120 + 64
EXTRA_WORKERS_MI = 4 * 90
SAFETY_MARGIN_MI = 256
REQUIRED_MI = BASELINE_MI + EXTRA_WORKERS_MI + SAFETY_MARGIN_MI


def _memory_mi(quantity: str) -> int:
    """Convert Kubernetes node memory quantities to whole MiB, rounded down."""
    multipliers = {"Ki": 1 / 1024, "Mi": 1, "Gi": 1024, "Ti": 1024 * 1024}
    for suffix, multiplier in multipliers.items():
        if quantity.endswith(suffix):
            return int(float(quantity[: -len(suffix)]) * multiplier)
    return int(int(quantity) / (1024 * 1024))


async def assess_capacity() -> dict[str, bool | str]:
    """Deny when cluster access or memory headroom cannot be established."""
    try:
        client, _ = _incluster_client()
        try:
            response = await asyncio.wait_for(client.get("/api/v1/nodes"), timeout=5)
            response.raise_for_status()
            nodes = response.json().get("items", [])
        finally:
            await client.aclose()
        if len(nodes) != 1:
            return {"sufficient": False, "reason": "Node capacity is unknown."}
        status = nodes[0]["status"]
        if any(
            condition.get("type") == "MemoryPressure" and condition.get("status") != "False"
            for condition in status.get("conditions", [])
        ):
            return {"sufficient": False, "reason": "The node reports memory pressure."}
        allocatable_mi = _memory_mi(status["allocatable"]["memory"])
        reason = (
            f"Estimated peak needs {REQUIRED_MI} MiB; node allocatable is {allocatable_mi} MiB."
        )
        if allocatable_mi < REQUIRED_MI:
            return {"sufficient": False, "reason": reason}
        return {"sufficient": True, "reason": reason}
    except Exception:
        LOGGER.warning("Could not verify node memory capacity", exc_info=True)
        return {"sufficient": False, "reason": "Cluster capacity is unavailable."}


@router.get("/api/demo/capacity")
async def demo_capacity() -> dict[str, bool | str]:
    return await assess_capacity()
