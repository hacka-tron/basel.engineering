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


_MISSING = object()


class FakeNodeClient:
    """Serves /api/v1/nodes and the metrics.k8s.io node list for one node."""

    def __init__(self, allocatable, used="600Mi", pressure="False", metrics=True):
        self.allocatable = allocatable
        self.used = used
        self.pressure = pressure
        self.metrics = metrics
        self.paths = []
        self.closed = False

    async def get(self, path):
        self.paths.append(path)
        if path == "/api/v1/nodes":
            conditions = (
                []
                if self.pressure is _MISSING
                else [{"type": "MemoryPressure", "status": self.pressure}]
            )
            return FakeResponse(
                {
                    "items": [
                        {
                            "metadata": {"name": "node-a"},
                            "status": {
                                "allocatable": {"memory": self.allocatable},
                                "conditions": conditions,
                            },
                        }
                    ]
                }
            )
        if path == "/apis/metrics.k8s.io/v1beta1/nodes":
            if not self.metrics:
                return ForbiddenResponse()
            return FakeResponse(
                {"items": [{"metadata": {"name": "node-a"}, "usage": {"memory": self.used}}]}
            )
        raise AssertionError(f"unexpected path {path}")

    async def aclose(self):
        self.closed = True


class ForbiddenResponse:
    def raise_for_status(self):
        raise RuntimeError("403 Forbidden")


def _capacity(monkeypatch, node):
    from services.glassbox.api import capacity

    monkeypatch.setattr(capacity, "_incluster_client", lambda: (node, "https://fake"))
    return TestClient(app).get("/api/demo/capacity").json()


def test_capacity_allows_node_with_enough_live_free_memory(monkeypatch):
    node = FakeNodeClient("2Gi", used="1000Mi")  # 1048 MiB free >= 768 required

    assert _capacity(monkeypatch, node)["sufficient"] is True
    assert sorted(node.paths) == ["/api/v1/nodes", "/apis/metrics.k8s.io/v1beta1/nodes"]
    assert node.closed


def test_capacity_denies_busy_node_even_when_allocatable_is_large(monkeypatch):
    # Codex review: total allocatable alone approved a heavily occupied node.
    assert _capacity(monkeypatch, FakeNodeClient("4Gi", used="3500Mi"))["sufficient"] is False


def test_capacity_denies_memory_pressure_or_missing_condition(monkeypatch):
    # A missing MemoryPressure condition must fail closed, not count as healthy.
    for pressure in ("True", "Unknown", _MISSING):
        node = FakeNodeClient("4Gi", used="100Mi", pressure=pressure)
        assert _capacity(monkeypatch, node)["sufficient"] is False


def test_capacity_denies_when_node_metrics_are_unavailable(monkeypatch):
    assert (
        _capacity(monkeypatch, FakeNodeClient("4Gi", used="100Mi", metrics=False))["sufficient"]
        is False
    )


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


class _LockRedis:
    def __init__(self, ttl):
        self._ttl = ttl

    async def ttl(self, key):
        assert key == "demo:load:lock"
        return self._ttl

    async def aclose(self):
        pass


def test_capacity_reports_cooldown_while_real_burst_lock_is_held(monkeypatch):
    # Codex review: another visitor's lock must show every visitor the
    # simulated state, not a tiger that promises a real burst.
    from services.glassbox.api import capacity

    monkeypatch.setenv("REDIS_URL", "redis://unused")
    monkeypatch.setattr(capacity.redis, "from_url", lambda _url: _LockRedis(212))

    async def must_not_assess():
        raise AssertionError("node capacity is irrelevant while the lock is held")

    monkeypatch.setattr(capacity, "assess_capacity", must_not_assess)
    body = TestClient(app).get("/api/demo/capacity").json()
    assert body == {
        "sufficient": False,
        "reason": "A real stress test is cooling down.",
        "retry_after_s": 212,
    }


def test_capacity_falls_through_to_node_check_without_a_lock(monkeypatch):
    from services.glassbox.api import capacity

    monkeypatch.setenv("REDIS_URL", "redis://unused")
    monkeypatch.setattr(capacity.redis, "from_url", lambda _url: _LockRedis(-2))
    node = FakeNodeClient("2Gi", used="1000Mi")
    assert _capacity(monkeypatch, node)["sufficient"] is True
