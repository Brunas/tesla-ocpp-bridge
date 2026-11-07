"""Messages the bridge sends to the central system (schema-checked by the ocpp library)."""

import asyncio
import logging

import pytest
from ocpp.v16.enums import ReadingContext

from helpers import CALL_ERROR, NO_REPLY, attach, drain, lifetime, vitals
from tesla_ocpp_bridge.state import State


def charge_session(bridge, end=True):
    bridge.process(vitals(plugged=True), lifetime(1000))
    bridge.process(vitals(plugged=True, charging=True, session_wh=500), lifetime(1000))
    bridge.queue_meter(bridge.state.tx["local_id"], ReadingContext.sample_periodic)
    if end:
        bridge.process(vitals(), lifetime(1500))


def test_full_session_is_delivered_in_order(bridge, cfg):
    async def scenario():
        charge_session(bridge)  # queued while "offline"
        cp, conn = attach(bridge)
        await drain(bridge, cp)
        return conn

    conn = asyncio.run(scenario())
    assert conn.actions() == ["StartTransaction", "MeterValues", "MeterValues", "StopTransaction"]

    start = conn.payloads("StartTransaction")[0]
    assert start["connectorId"] == 1 and start["meterStart"] == 1000
    assert start["idTag"] == "TESLA"

    assert all(p["transactionId"] == 42 for p in conn.payloads("MeterValues"))
    stop = conn.payloads("StopTransaction")[0]
    assert stop == {"meterStop": 1500, "timestamp": stop["timestamp"], "transactionId": 42,
                    "reason": "EVDisconnected", "idTag": "TESLA"}

    assert bridge.state.outbox == [] and bridge.state.tx_ids == {}
    assert State(cfg["state_file"]).outbox == []  # delivery persisted


def test_transaction_id_is_kept_while_session_is_open(bridge):
    async def scenario():
        charge_session(bridge, end=False)
        cp, conn = attach(bridge)
        await drain(bridge, cp)

    asyncio.run(scenario())
    assert bridge.state.tx_ids == {bridge.state.tx["local_id"]: 42}


def test_rejected_start_drops_whole_transaction(bridge, caplog):
    async def scenario():
        charge_session(bridge)
        cp, conn = attach(bridge, StartTransaction=CALL_ERROR)
        await drain(bridge, cp)
        return conn

    with caplog.at_level(logging.WARNING):
        conn = asyncio.run(scenario())
    assert conn.actions() == ["StartTransaction"]  # meter/stop not sent without an id
    assert bridge.state.outbox == []
    assert "dropping transaction" in caplog.text


def test_unanswered_message_stays_queued(bridge):
    async def scenario():
        charge_session(bridge)
        cp, conn = attach(bridge, StartTransaction=NO_REPLY)
        cp._response_timeout = 0.05
        with pytest.raises(asyncio.TimeoutError):
            await drain(bridge, cp)

    asyncio.run(scenario())
    assert [m["kind"] for m in bridge.state.outbox][0] == "start"
    assert len(bridge.state.outbox) == 4


def test_retry_after_reconnect_delivers_everything(bridge):
    async def scenario():
        charge_session(bridge)
        cp, _ = attach(bridge, StartTransaction=NO_REPLY)
        cp._response_timeout = 0.05
        with pytest.raises(asyncio.TimeoutError):
            await drain(bridge, cp)
        cp2, conn2 = attach(bridge)  # new connection
        await drain(bridge, cp2)
        return conn2

    conn = asyncio.run(scenario())
    assert conn.actions() == ["StartTransaction", "MeterValues", "MeterValues", "StopTransaction"]


def test_not_accepted_id_tag_is_logged_but_tracked(bridge, caplog):
    async def scenario():
        charge_session(bridge, end=False)
        cp, _ = attach(bridge, StartTransaction={"transactionId": 9,
                                                 "idTagInfo": {"status": "Invalid"}})
        await drain(bridge, cp)

    with caplog.at_level(logging.WARNING):
        asyncio.run(scenario())
    assert list(bridge.state.tx_ids.values()) == [9]
    assert "did not accept idTag" in caplog.text


def test_status_notification_sent_once_unless_forced(bridge):
    async def scenario():
        cp, conn = attach(bridge)
        bridge.vitals = vitals(alerts=["alert-a" * 10])
        bridge.update_status()
        await bridge.send_status()
        await bridge.send_status()
        await bridge.send_status(force=True)
        return conn

    conn = asyncio.run(scenario())
    sent = conn.payloads("StatusNotification")
    assert len(sent) == 2
    assert sent[0]["status"] == "Available" and sent[0]["errorCode"] == "NoError"
    assert sent[0]["connectorId"] == 1
    assert len(sent[0]["info"]) == 50  # truncated to the OCPP limit


def test_send_status_without_connection_is_noop(bridge):
    bridge.status = "Available"
    asyncio.run(bridge.send_status())  # must not raise
    assert bridge.last_sent_status is None


def test_boot_notification_payload_and_interval(bridge, cfg):
    bridge.version = {"serial_number": "PGT123", "firmware_version": "24.44.0"}
    cfg["model"] = "A very long model name that exceeds twenty"

    async def scenario():
        cp, conn = attach(bridge)
        assert await bridge.send_boot(cp) is True
        return conn

    conn = asyncio.run(scenario())
    boot = conn.payloads("BootNotification")[0]
    assert boot["chargePointVendor"] == "Tesla"
    assert boot["chargePointModel"] == cfg["model"][:20]
    assert boot["chargePointSerialNumber"] == "PGT123"
    assert boot["firmwareVersion"] == "24.44.0"
    assert bridge.heartbeat_interval == 30  # adopted from the response


def test_boot_without_version_info(bridge):
    async def scenario():
        cp, conn = attach(bridge)
        await bridge.send_boot(cp)
        return conn

    boot = asyncio.run(scenario()).payloads("BootNotification")[0]
    assert "chargePointSerialNumber" not in boot and "firmwareVersion" not in boot


def test_boot_interval_does_not_override_csms_configuration(bridge):
    bridge.change_configuration("HeartbeatInterval", "120")

    async def scenario():
        cp, _ = attach(bridge)
        await bridge.send_boot(cp)

    asyncio.run(scenario())
    assert bridge.heartbeat_interval == 120


def test_boot_pending_waits_and_reports_failure(bridge, monkeypatch):
    waits = []
    real_sleep = asyncio.sleep

    async def fake_sleep(seconds):
        waits.append(seconds)
        await real_sleep(0)

    async def scenario():
        cp, _ = attach(bridge, BootNotification={"currentTime": "2026-01-01T00:00:00Z",
                                                 "interval": 17, "status": "Pending"})
        monkeypatch.setattr(asyncio, "sleep", fake_sleep)
        try:
            return await bridge.send_boot(cp)
        finally:
            monkeypatch.undo()

    assert asyncio.run(scenario()) is False
    assert 17 in waits


def test_triggered_meter_values_with_and_without_transaction(bridge):
    async def scenario():
        cp, conn = attach(bridge)
        bridge.vitals = vitals()
        await bridge.send_triggered_meter_values()
        charge_session(bridge, end=False)
        await drain(bridge, cp)
        await bridge.send_triggered_meter_values()
        return conn

    conn = asyncio.run(scenario())
    triggered = [p for p in conn.payloads("MeterValues")
                 if p["meterValue"][0]["sampledValue"][0]["context"] == "Trigger"]
    assert "transactionId" not in triggered[0]
    assert triggered[1]["transactionId"] == 42
