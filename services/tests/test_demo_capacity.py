"""Capacity gate for the synthetic worker burst."""

from fastapi.testclient import TestClient

from services.glassbox.api.main import app


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self.payload


class FakeNodeClient:
    def __init__(self, allocatable, pressure="False"):
        self.allocatable = allocatable
        self.pressure = pressure
        self.paths = []
        self.closed = False

    async def get(self, path):
        self.paths.append(path)
        return FakeResponse(
            {
                "items": [
                    {
                        "status": {
                            "allocatable": {"memory": self.allocatable},
                            "conditions": [{"type": "MemoryPressure", "status": self.pressure}],
                        }
                    }
                ]
            }
        )

    async def aclose(self):
        self.closed = True


def test_capacity_uses_live_node_allocatable_and_is_conservative(monkeypatch):
    from services.glassbox.api import capacity

    node = FakeNodeClient("4Gi")
    monkeypatch.setattr(capacity, "_incluster_client", lambda: (node, "https://fake"))

    response = TestClient(app).get("/api/demo/capacity")

    assert response.status_code == 200
    assert response.json()["sufficient"] is True
    assert node.paths == ["/api/v1/nodes"]
    assert node.closed


def test_capacity_denies_tight_node_or_memory_pressure(monkeypatch):
    from services.glassbox.api import capacity

    for node in (FakeNodeClient("2Gi"), FakeNodeClient("4Gi", pressure="True")):
        monkeypatch.setattr(capacity, "_incluster_client", lambda node=node: (node, "https://fake"))
        response = TestClient(app).get("/api/demo/capacity")
        assert response.json()["sufficient"] is False


def test_capacity_denies_unknown_cluster(monkeypatch):
    from services.glassbox.api import capacity

    def unavailable():
        raise RuntimeError("not in a cluster")

    monkeypatch.setattr(capacity, "_incluster_client", unavailable)
    response = TestClient(app).get("/api/demo/capacity")
    assert response.status_code == 200
    assert response.json()["sufficient"] is False


def test_direct_load_request_cannot_bypass_capacity_gate(monkeypatch):
    from services.glassbox.api import demo

    async def insufficient():
        return {"sufficient": False, "reason": "Not enough memory headroom"}

    def forbid_redis(_url):
        raise AssertionError("must not enqueue or lock when capacity is insufficient")

    monkeypatch.setattr(demo, "assess_capacity", insufficient)
    monkeypatch.setattr(demo.redis, "from_url", forbid_redis)
    response = TestClient(app).post("/api/demo/load")
    assert response.status_code == 200
    assert response.json()["enqueued"] == 0
    assert response.json()["started"] is False
