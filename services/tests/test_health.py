from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

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
