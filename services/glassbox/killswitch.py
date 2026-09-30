"""Redis-backed incident kill switch."""

from typing import Protocol


class KillSwitch(Protocol):
    async def llm_disabled(self) -> bool: ...


class RedisKillSwitch:
    KEY = "glassbox:kill:disable_llm"

    def __init__(self, client):
        self.client = client

    async def llm_disabled(self) -> bool:
        value = await self.client.get(self.KEY)
        value = value.decode() if isinstance(value, bytes) else value
        return value == "1"


def get_kill_switch(client) -> KillSwitch:
    return RedisKillSwitch(client)
