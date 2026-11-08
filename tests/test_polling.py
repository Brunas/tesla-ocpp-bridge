"""Bridge.poll_loop against a fake Wall Connector client."""

import asyncio
import logging
import re

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



def warnings(caplog):
    return [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]


async def poll_through(bridge, phases):
    """Run one poller through (fail, polls) phases: set FakeTWC.fail, then wait for that
    many more polls."""
    task = asyncio.create_task(bridge.poll_loop())
    try:
        for fail, polls in phases:
            bridge.twc.fail = fail
            target = bridge.twc.calls.count("vitals") + polls
            await wait_until(lambda t=target: bridge.twc.calls.count("vitals") >= t)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


def outage_then_recovery(failed_polls):
    return [(False, 1), (True, failed_polls), (False, 2)]


def test_blip_is_logged_at_debug_only(cfg, caplog):
    cfg["poll_interval"] = 0
    b = Bridge(cfg)
    b.twc = FakeTWC()
    caplog.set_level(logging.DEBUG, logger="tesla_ocpp_bridge")
    asyncio.run(poll_through(b, outage_then_recovery(failed_polls=3)))

    assert warnings(caplog) == []
    recovered = [r for r in caplog.records if "reachable again" in r.getMessage()]
    assert len(recovered) == 1 and recovered[0].levelno == logging.DEBUG


def test_longer_outage_warns_and_logs_recovery(cfg, caplog, monkeypatch):
    monkeypatch.setattr(br, "TWC_WARN_AFTER_S", -1)
    cfg["poll_interval"] = 0
    b = Bridge(cfg)
    b.twc = FakeTWC()
    caplog.set_level(logging.INFO, logger="tesla_ocpp_bridge")
    asyncio.run(poll_through(b, outage_then_recovery(failed_polls=3)))

    # Warned on the first failure past TWC_WARN_AFTER_S, then only every 60th one.
    counts = [int(re.search(r"(\d+) failed poll", w).group(1)) for w in warnings(caplog)]
    assert counts[0] == 1
    assert all(c % 60 == 0 for c in counts[1:])
    recovered = [r for r in caplog.records if "reachable again after" in r.getMessage()]
    assert len(recovered) == 1 and recovered[0].levelno == logging.INFO


def test_never_reachable_warns_on_first_failure(cfg, caplog):
    cfg["poll_interval"] = 0
    b = Bridge(cfg)
    b.twc = FakeTWC(fail=True)
    asyncio.run(run_poller(b, lambda: b.twc.calls.count("vitals") >= 1))
    # A wrong address shows up right away, without waiting TWC_WARN_AFTER_S.
    assert "1 failed poll" in warnings(caplog)[0]


def test_outage_is_measured_from_the_failed_poll_not_the_last_success(cfg, caplog, monkeypatch):
    # A poll that times out starts poll_interval after the last success and fails
    # after the HTTP timeout. That single failure must not count as a long outage.
    monkeypatch.setattr(br, "TWC_WARN_AFTER_S", 0.2)
    cfg["poll_interval"] = 0.15
    b = Bridge(cfg)
    b.twc = FakeTWC()
    real_get = b.twc.get

    async def slow_get(endpoint):
        if b.twc.fail:
            await asyncio.sleep(0.1)  # stands in for the HTTP timeout
        return await real_get(endpoint)

    b.twc.get = slow_get
    caplog.set_level(logging.DEBUG, logger="tesla_ocpp_bridge")
    asyncio.run(poll_through(b, outage_then_recovery(failed_polls=1)))
    assert warnings(caplog) == []  # 0.15 s + 0.1 s since last success, but 0.1 s down
