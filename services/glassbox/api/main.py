import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Response
from fastapi.staticfiles import StaticFiles

from services.glassbox.api import cluster
from services.glassbox.api.ask import router as ask_router
from services.glassbox.api.cache import ping_redis
from services.glassbox.api.capacity import router as capacity_router
from services.glassbox.api.cluster import router as cluster_router
from services.glassbox.api.csp_report import router as csp_report_router
from services.glassbox.api.db import ping_mysql
from services.glassbox.api.demo import router as demo_router
from services.glassbox.api.security_headers import SecurityHeadersMiddleware
from services.glassbox.limits import validate_ip_hash_salt
from services.glassbox.providers.factory import validate_provider_config


@asynccontextmanager
async def lifespan(app: FastAPI):
    validate_provider_config()
    validate_ip_hash_salt()
    yield
    await cluster.HUB.aclose()


app = FastAPI(title="glassbox-api", lifespan=lifespan)
# Wraps every route and the static frontend mount below, SSE included.
app.add_middleware(SecurityHeadersMiddleware)
app.include_router(ask_router)
app.include_router(demo_router)
app.include_router(capacity_router)
app.include_router(cluster_router)
app.include_router(csp_report_router)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/version")
async def version(response: Response) -> dict[str, str]:
    """The image's release tag (`build-N`), baked in by release.yml as GLASSBOX_BUILD.

    The post-deploy stream check (.github/workflows/stream-check.yml) polls this
    to know when a release is live. Local and test images report "dev".
    """
    response.headers["Cache-Control"] = "no-store"
    return {"build": os.environ.get("GLASSBOX_BUILD") or "dev"}


@app.get("/readyz")
async def readyz(response: Response) -> dict[str, bool]:
    mysql_ok = await ping_mysql()
    redis_ok = await ping_redis()
    ready = mysql_ok and redis_ok
    if not ready:
        response.status_code = 503
    return {"mysql": mysql_ok, "redis": redis_ok, "ready": ready}


_frontend_dist = Path(__file__).resolve().parents[3] / "frontend" / "dist"
if _frontend_dist.is_dir():
    app.mount("/", StaticFiles(directory=_frontend_dist, html=True), name="frontend")
