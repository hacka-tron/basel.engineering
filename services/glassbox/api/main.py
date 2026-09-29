from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Response
from fastapi.staticfiles import StaticFiles

from services.glassbox.api.ask import router as ask_router
from services.glassbox.api.cache import ping_redis
from services.glassbox.api.db import ping_mysql
from services.glassbox.providers.factory import validate_provider_config


@asynccontextmanager
async def lifespan(app: FastAPI):
    validate_provider_config()
    yield


app = FastAPI(title="glassbox-api", lifespan=lifespan)
app.include_router(ask_router)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


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
