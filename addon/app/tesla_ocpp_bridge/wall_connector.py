"""Client for the Wall Connector Gen 3 local HTTP API (read-only)."""

from __future__ import annotations

import aiohttp


class WallConnector:
    def __init__(self, host: str, session: aiohttp.ClientSession):
        self.base = host.rstrip("/") if host.startswith("http") else f"http://{host}"
        self.session = session

    async def get(self, endpoint: str) -> dict:
        url = f"{self.base}/api/1/{endpoint}"
        async with self.session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
            resp.raise_for_status()
            return await resp.json(content_type=None)
