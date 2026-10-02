from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from services.glassbox.api.cache import ping_redis
from services.glassbox.api.db import ping_mysql
from services.glassbox.api.main import app

client = TestClient(app)


def test_invalid_provider_fails_app_startup(monkeypatch):
    monkeypatch.setenv("GLASSBOX_PROVIDER", "bedrok")
    with pytest.raises(ValueError, match="GLASSBOX_PROVIDER"):
        with TestClient(app):
            pass


@pytest.mark.parametrize("cap", ["abc", "0", "-1"])
def test_invalid_daily_cap_fails_app_startup(monkeypatch, cap):
    monkeypatch.setenv("GLASSBOX_PROVIDER", "fake")
    monkeypatch.setenv("GLASSBOX_DAILY_LLM_CAP", cap)
    with pytest.raises(ValueError, match="GLASSBOX_DAILY_LLM_CAP"):
        with TestClient(app):
            pass


def test_invalid_bedrock_model_id_fails_app_startup(monkeypatch):
    monkeypatch.setenv("GLASSBOX_PROVIDER", "bedrock")
    monkeypatch.setenv("BEDROCK_LLM_MODEL_ID", "bad model ID")
    with pytest.raises(ValueError, match="BEDROCK_LLM_MODEL_ID"):
        with TestClient(app):
            pass


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


def test_version_reports_the_baked_in_build_tag(monkeypatch):
    monkeypatch.setenv("GLASSBOX_BUILD", "build-123")
    response = client.get("/api/version")
    assert response.status_code == 200
    assert response.json() == {"build": "build-123"}
    assert response.headers["cache-control"] == "no-store"


def test_version_defaults_to_dev(monkeypatch):
    monkeypatch.delenv("GLASSBOX_BUILD", raising=False)
    assert client.get("/api/version").json() == {"build": "dev"}
