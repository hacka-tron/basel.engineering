import os

import aiomysql


async def ping_mysql() -> bool:
    try:
        conn = await aiomysql.connect(
            host=os.environ["MYSQL_HOST"],
            port=int(os.environ.get("MYSQL_PORT", "3306")),
            user=os.environ["MYSQL_USER"],
            password=os.environ["MYSQL_PASSWORD"],
            db=os.environ["MYSQL_DATABASE"],
            connect_timeout=3,
        )
        try:
            async with conn.cursor() as cur:
                await cur.execute("SELECT 1")
                await cur.fetchone()
        finally:
            conn.close()
        return True
    except Exception:
        return False
