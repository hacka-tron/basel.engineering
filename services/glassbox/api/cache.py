import os

import redis.asyncio as redis


async def ping_redis() -> bool:
    try:
        client = redis.from_url(os.environ["REDIS_URL"], socket_connect_timeout=3)
        try:
            pong = await client.ping()
        finally:
            await client.aclose()
        return bool(pong)
    except Exception:
        return False
