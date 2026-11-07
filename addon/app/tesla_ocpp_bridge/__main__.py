"""Entry point: python -m tesla_ocpp_bridge"""

from __future__ import annotations

import asyncio
import logging
import signal

from .bridge import Bridge
from .config import load_config

log = logging.getLogger("tesla_ocpp_bridge")


def main() -> None:
    cfg = load_config()
    logging.basicConfig(
        level=getattr(logging, str(cfg["log_level"]).upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    if str(cfg["log_level"]).lower() != "debug":
        logging.getLogger("ocpp").setLevel(logging.WARNING)
    else:
        logging.getLogger("ocpp").setLevel(logging.DEBUG)
    log.info("Tesla Wall Connector OCPP bridge for charge point %s", cfg["charge_point_id"])

    async def runner() -> None:
        task = asyncio.current_task()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, task.cancel)
            except NotImplementedError:
                pass
        try:
            await Bridge(cfg).run()
        except asyncio.CancelledError:
            log.info("Stopped")

    asyncio.run(runner())


if __name__ == "__main__":
    main()
