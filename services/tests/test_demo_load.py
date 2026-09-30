"""Tests for the stress-test /api/demo/load endpoint (DESIGN.md §9.4)."""

import pytest
from fastapi.testclient import TestClient

from services.glassbox.api.main import app


class FakeRedis:
    def __init__(self):
        self.store: dict[str, str] = {}
        self.ttls: dict[str, int] = {}

    async def set(self, key, value, *, nx=False, ex=None):
        if nx and key in self.store:
            return False
        self.store[key] = value
        self.ttls[key] = ex if ex is not None else -1
        return True

    async def ttl(self, key):
        return self.ttls.get(key, -2) if key in self.store else -2

    async def aclose(self):
        pass


def test_first_request_acquires_lock_and_enqueues_synthetic_jobs(monkeypatch):
    from services.glassbox.api import demo

    client = FakeRedis()
    calls = {}

    async def fake_enqueue(redis_client, *, count, simulated_delay_ms):
        assert redis_client is client
        calls["count"] = count
        calls["delay"] = simulated_delay_ms
        return count

    monkeypatch.setenv("REDIS_URL", "redis://unused")
    monkeypatch.setattr(demo.redis, "from_url", lambda url: client)
    monkeypatch.setattr(demo, "enqueue_synthetic_jobs", fake_enqueue)

    response = TestClient(app).post("/api/demo/load")

    assert response.status_code == 200
    assert response.json() == {"started": True, "enqueued": 300, "retry_after_s": 300}
    assert calls == {"count": 300, "delay": 200}
    assert client.store[demo.LOCK_KEY] == "1"
    assert client.ttls[demo.LOCK_KEY] == 300


def test_second_request_within_cooldown_does_not_enqueue(monkeypatch):
    from services.glassbox.api import demo

    client = FakeRedis()
    client.store[demo.LOCK_KEY] = "1"
    client.ttls[demo.LOCK_KEY] = 214

    async def fail_enqueue(*args, **kwargs):
        pytest.fail("a locked stress-test request must not enqueue any jobs")

    monkeypatch.setenv("REDIS_URL", "redis://unused")
    monkeypatch.setattr(demo.redis, "from_url", lambda url: client)
    monkeypatch.setattr(demo, "enqueue_synthetic_jobs", fail_enqueue)

    response = TestClient(app).post("/api/demo/load")

    assert response.status_code == 200
    body = response.json()
    assert body["started"] is False
    assert body["retry_after_s"] == 214
    assert body["enqueued"] == 0


def test_real_redis_lock_blocks_concurrent_requests():
    """Real NX/EX semantics against local Redis, not a hand-rolled fake."""
    import redis.asyncio as redis

    async def scenario():
        client = redis.from_url("redis://127.0.0.1:6379/0")
        try:
            await client.ping()
        except Exception as exc:
            await client.aclose()
            pytest.skip(f"local Redis unavailable: {exc}")
        try:
            from services.glassbox.api import demo

            await client.delete(demo.LOCK_KEY)
            first = await client.set(demo.LOCK_KEY, "1", nx=True, ex=demo.LOCK_TTL_S)
            second = await client.set(demo.LOCK_KEY, "1", nx=True, ex=demo.LOCK_TTL_S)
            ttl = await client.ttl(demo.LOCK_KEY)
            assert first is True
            assert not second  # redis-py returns None (not False) on a failed NX
            assert 0 < ttl <= demo.LOCK_TTL_S
        finally:
            await client.delete(demo.LOCK_KEY)
            await client.aclose()

    import asyncio

    asyncio.run(scenario())
