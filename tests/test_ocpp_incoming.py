"""Requests from the central system (CircleLink) to the bridge."""

import asyncio

import pytest

from helpers import attach, incoming, vitals, wait_until


def ask(bridge, action, payload):
    async def scenario():
        cp, conn = attach(bridge)
        return await incoming(cp, conn, action, payload)

    return asyncio.run(scenario())


def result(frame):
    assert frame[0] == 3, f"expected CallResult, got {frame}"
    return frame[2]


def test_get_configuration_all(bridge):
    keys = {k["key"]: k for k in result(ask(bridge, "GetConfiguration", {}))["configurationKey"]}
    assert keys["HeartbeatInterval"] == {"key": "HeartbeatInterval", "readonly": False,
                                         "value": "300"}
    assert "MeterValueSampleInterval" in keys


def test_get_configuration_specific_and_unknown(bridge):
    res = result(ask(bridge, "GetConfiguration", {"key": ["NumberOfConnectors", "Nope"]}))
    assert [k["key"] for k in res["configurationKey"]] == ["NumberOfConnectors"]
    assert res["unknownKey"] == ["Nope"]


def test_change_configuration(bridge):
    res = result(ask(bridge, "ChangeConfiguration",
                     {"key": "MeterValueSampleInterval", "value": "15"}))
    assert res == {"status": "Accepted"}
    assert bridge.meter_interval == 15


@pytest.mark.parametrize("action,payload,expected", [
    ("RemoteStartTransaction", {"idTag": "ABC"}, "Rejected"),
    ("RemoteStopTransaction", {"transactionId": 42}, "Rejected"),
    ("UnlockConnector", {"connectorId": 1}, "NotSupported"),
    ("ChangeAvailability", {"connectorId": 0, "type": "Operative"}, "Accepted"),
    ("ClearCache", {}, "Accepted"),
    ("DataTransfer", {"vendorId": "com.example"}, "UnknownVendorId"),
    ("SetChargingProfile", {
        "connectorId": 1,
        "csChargingProfiles": {
            "chargingProfileId": 1, "stackLevel": 0,
            "chargingProfilePurpose": "TxDefaultProfile", "chargingProfileKind": "Absolute",
            "chargingSchedule": {"chargingRateUnit": "A",
                                 "chargingSchedulePeriod": [{"startPeriod": 0, "limit": 16}]},
        },
    }, "NotSupported"),
])
def test_simple_requests(bridge, action, payload, expected):
    assert result(ask(bridge, action, payload))["status"] == expected


def test_unhandled_action_returns_call_error(bridge):
    frame = ask(bridge, "GetLocalListVersion", {})
    assert frame[0] == 4  # CallError, connection stays usable


def test_trigger_status_notification(bridge):
    bridge.vitals = vitals(plugged=True)
    bridge.update_status()

    async def scenario():
        cp, conn = attach(bridge)
        frame = await incoming(cp, conn, "TriggerMessage",
                               {"requestedMessage": "StatusNotification"})
        await wait_until(lambda: conn.payloads("StatusNotification"))
        return frame, conn

    frame, conn = asyncio.run(scenario())
    assert result(frame) == {"status": "Accepted"}
    assert conn.payloads("StatusNotification")[0]["status"] == "Preparing"


@pytest.mark.parametrize("requested,action", [
    ("Heartbeat", "Heartbeat"),
    ("BootNotification", "BootNotification"),
    ("MeterValues", "MeterValues"),
])
def test_trigger_other_messages(bridge, requested, action):
    bridge.vitals = vitals()

    async def scenario():
        cp, conn = attach(bridge)
        frame = await incoming(cp, conn, "TriggerMessage", {"requestedMessage": requested})
        await wait_until(lambda: conn.payloads(action))
        return frame

    assert result(asyncio.run(scenario())) == {"status": "Accepted"}


def test_trigger_unsupported_message(bridge):
    res = result(ask(bridge, "TriggerMessage",
                     {"requestedMessage": "FirmwareStatusNotification"}))
    assert res == {"status": "NotImplemented"}


def test_reset_closes_connection(bridge):
    async def scenario():
        cp, conn = attach(bridge)
        frame = await incoming(cp, conn, "Reset", {"type": "Soft"})
        await wait_until(lambda: conn.closed, timeout=3)
        return frame

    assert result(asyncio.run(scenario())) == {"status": "Accepted"}
