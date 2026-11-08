"""Shared test helpers: fake Wall Connector payloads and an in-memory OCPP peer."""

import asyncio
import json

from tesla_ocpp_bridge.bridge import Bridge
from tesla_ocpp_bridge.charge_point import BridgeChargePoint

_sleep = asyncio.sleep  # keep a reference in case a test patches asyncio.sleep

CP_ID = "TEST-CP-0001"
NOW = "2026-01-01T00:00:00Z"

# Sentinels for Loopback responses
CALL_ERROR = object()  # answer with an OCPP CallError
NO_REPLY = object()  # never answer (simulates a hung backend)

DEFAULT_RESPONSES = {
    "BootNotification": {"currentTime": NOW, "interval": 30, "status": "Accepted"},
    "Heartbeat": {"currentTime": NOW},
    "StatusNotification": {},
    "StartTransaction": {"transactionId": 42, "idTagInfo": {"status": "Accepted"}},
    "MeterValues": {},
    "StopTransaction": {},
}


def vitals(plugged=False, charging=False, session_wh=0.0, amps=None, volts=(230.0, 230.0, 230.0), alerts=()):
    if amps is None:
        amps = 16.0 if charging else 0.0
    return {
        "vehicle_connected": plugged,
        "contactor_closed": charging,
        "session_energy_wh": session_wh,
        "voltageA_v": volts[0],
        "voltageB_v": volts[1],
        "voltageC_v": volts[2],
        "currentA_a": amps,
        "currentB_a": amps,
        "currentC_a": amps,
        "current_alerts": list(alerts),
    }


def lifetime(wh):
    return {"energy_wh": wh}


async def wait_until(predicate, timeout=2.0):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() > deadline:
            raise AssertionError("condition not met in time")
        await _sleep(0.005)


class Loopback:
    """Stands in for the websocket. Answers the bridge's calls like a CSMS would.

    Every frame goes through the real ocpp library, so payloads are
    schema-validated in both directions.
    """

    def __init__(self, **responses):
        self.responses = {**DEFAULT_RESPONSES, **responses}
        self.sent: list[list] = []
        self.cp: BridgeChargePoint | None = None
        self.closed = False
        self._tasks: set[asyncio.Task] = set()

    async def send(self, raw: str) -> None:
        msg = json.loads(raw)
        self.sent.append(msg)
        if msg[0] != 2:  # only calls initiated by the bridge get an answer
            return
        _, uid, action, payload = msg
        resp = self.responses[action]
        if callable(resp):
            resp = resp(payload)
        if resp is NO_REPLY:
            return
        if resp is CALL_ERROR:
            frame = [4, uid, "InternalError", "test error", {}]
        else:
            frame = [3, uid, resp]
        task = asyncio.get_running_loop().create_task(self.cp.route_message(json.dumps(frame)))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def recv(self):
        await asyncio.Event().wait()

    async def close(self, *_, **__):
        self.closed = True

    def actions(self) -> list[str]:
        return [m[2] for m in self.sent if m[0] == 2]

    def payloads(self, action: str) -> list[dict]:
        return [m[3] for m in self.sent if m[0] == 2 and m[2] == action]


def attach(bridge: Bridge, **responses) -> tuple[BridgeChargePoint, Loopback]:
    """Create a connected charge point for `bridge`. Must run inside an event loop."""
    conn = Loopback(**responses)
    cp = BridgeChargePoint(CP_ID, conn, bridge)
    conn.cp = cp
    bridge.cp = cp
    return cp, conn


async def incoming(cp: BridgeChargePoint, conn: Loopback, action: str, payload: dict) -> list:
    """Deliver a call from the CSMS to the bridge and return the bridge's reply frame."""
    uid = f"csms-{len(conn.sent)}"
    await cp.route_message(json.dumps([2, uid, action, payload]))
    return next(m for m in conn.sent if m[1] == uid)


async def drain(bridge: Bridge, cp: BridgeChargePoint) -> None:
    """Run the outbox loop until everything queued has been delivered."""
    task = asyncio.create_task(bridge.outbox_loop(cp))
    try:
        await wait_until(lambda: not bridge.state.outbox or task.done())
        if task.done():
            task.result()  # re-raise delivery errors
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
