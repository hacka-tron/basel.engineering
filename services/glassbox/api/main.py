from fastapi import FastAPI, Response

from services.glassbox.api.cache import ping_redis
from services.glassbox.api.db import ping_mysql

app = FastAPI(title="glassbox-api")


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
