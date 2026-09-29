from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from services.glassbox.api.cache import ping_redis
from services.glassbox.api.db import ping_mysql
from services.glassbox.api.main import app

client = TestClient(app)


def test_healthz_returns_ok():
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readyz_returns_200_when_dependencies_up():
    with (
        patch("services.glassbox.api.main.ping_mysql", new=AsyncMock(return_value=True)),
        patch("services.glassbox.api.main.ping_redis", new=AsyncMock(return_value=True)),
    ):
        response = client.get("/readyz")
    assert response.status_code == 200
    assert response.json() == {"mysql": True, "redis": True, "ready": True}


def test_readyz_returns_503_when_a_dependency_is_down():
    with (
        patch("services.glassbox.api.main.ping_mysql", new=AsyncMock(return_value=True)),
        patch("services.glassbox.api.main.ping_redis", new=AsyncMock(return_value=False)),
    ):
        response = client.get("/readyz")
    assert response.status_code == 503
    assert response.json() == {"mysql": True, "redis": False, "ready": False}


@pytest.mark.asyncio
async def test_ping_mysql_returns_false_when_connect_raises(monkeypatch):
    monkeypatch.setenv("MYSQL_HOST", "localhost")
    monkeypatch.setenv("MYSQL_USER", "user")
    monkeypatch.setenv("MYSQL_PASSWORD", "password")
    monkeypatch.setenv("MYSQL_DATABASE", "glassbox")
    with patch(
        "services.glassbox.api.db.aiomysql.connect",
        new=AsyncMock(side_effect=OSError("connection refused")),
    ):
        assert await ping_mysql() is False


@pytest.mark.asyncio
async def test_ping_redis_returns_false_when_from_url_raises(monkeypatch):
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    with patch(
        "services.glassbox.api.cache.redis.from_url",
        side_effect=OSError("connection refused"),
    ):
        assert await ping_redis() is False
