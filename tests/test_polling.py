"""Bridge.poll_loop against a fake Wall Connector client."""

import asyncio

from ocpp.v16.enums import ChargePointStatus as S

from helpers import lifetime, vitals, wait_until
from tesla_ocpp_bridge import bridge as br
from tesla_ocpp_bridge.bridge import Bridge


class FakeTWC:
    def __init__(self, fail=False):
        self.fail = fail
        self.calls: list[str] = []
        self.vitals = vitals(plugged=True, charging=True, session_wh=100)

    async def get(self, endpoint):
        self.calls.append(endpoint)
        if self.fail:
            raise OSError("unreachable")
        return {"vitals": self.vitals, "lifetime": lifetime(1000),
                "version": {"serial_number": "PGT1", "firmware_version": "1.2.3"}}[endpoint]


async def run_poller(bridge, until, timeout=2.0):
    task = asyncio.create_task(bridge.poll_loop())
    try:
        await wait_until(until, timeout)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


def test_successful_poll(cfg):
    cfg["poll_interval"] = 0
    b = Bridge(cfg)
    b.twc = FakeTWC()
    asyncio.run(run_poller(b, lambda: b.twc.calls.count("vitals") >= 3))

    assert b.twc.calls.count("version") == 1  # fetched once only
    assert b.version["serial_number"] == "PGT1"
    assert b.twc_ok and b.first_poll.is_set()
    assert b.status == S.charging
    assert b.state.tx is not None


def test_unreachable_wall_connector_becomes_unavailable(cfg, monkeypatch):
    monkeypatch.setattr(br, "TWC_OFFLINE_AFTER_S", 0)
    cfg["poll_interval"] = 0
    b = Bridge(cfg)
    b.twc = FakeTWC(fail=True)
    asyncio.run(run_poller(b, lambda: b.status == S.unavailable))
    assert not b.twc_ok
    assert b.first_poll.is_set()  # startup is not blocked by a dead charger


def test_short_outage_keeps_status(cfg):
    cfg["poll_interval"] = 0
    b = Bridge(cfg)
    b.twc = FakeTWC()

    async def scenario():
        await run_poller(b, lambda: b.status == S.charging)
        b.twc.fail = True
        await run_poller(b, lambda: b.twc.calls.count("vitals") >= 10)

    asyncio.run(scenario())
    assert b.status == S.charging  # 60 s grace period not yet over
    assert b.state.tx is not None  # session not ended by a network blip
