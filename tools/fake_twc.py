#!/usr/bin/env python3
"""Fake Tesla Wall Connector Gen 3 local API for testing the bridge.

    python tools/fake_twc.py --port 8081
    curl 'localhost:8081/control?plug=1&charge=1'   # plug in and start charging
    curl 'localhost:8081/control?charge=0'          # pause
    curl 'localhost:8081/control?plug=0'            # unplug

While charging it adds ~11 kW worth of energy per real second x --speed.
"""

import argparse
import time

from aiohttp import web

state = {"plug": False, "charge": False, "session_wh": 0.0, "lifetime_wh": 1_234_000.0, "last": time.monotonic()}
SPEED = 1.0
POWER_W = 11_000


def tick():
    now = time.monotonic()
    if state["plug"] and state["charge"]:
        wh = POWER_W * (now - state["last"]) / 3600 * SPEED
        state["session_wh"] += wh
        state["lifetime_wh"] += wh
    state["last"] = now


async def vitals(_):
    tick()
    amps = 16.0 if state["plug"] and state["charge"] else 0.0
    return web.json_response(
        {
            "contactor_closed": state["plug"] and state["charge"],
            "vehicle_connected": state["plug"],
            "session_s": 0,
            "grid_v": 230.1,
            "grid_hz": 50.0,
            "vehicle_current_a": amps,
            "currentA_a": amps,
            "currentB_a": amps,
            "currentC_a": amps,
            "currentN_a": 0.0,
            "voltageA_v": 230.4,
            "voltageB_v": 231.0,
            "voltageC_v": 229.8,
            "session_energy_wh": round(state["session_wh"], 1),
            "evse_state": 11 if state["charge"] else 4,
            "current_alerts": [],
        }
    )


async def lifetime(_):
    tick()
    # Real units update this less often; round to 100 Wh to mimic lag.
    return web.json_response({"energy_wh": int(state["lifetime_wh"] // 100 * 100), "charge_starts": 42})


async def version(_):
    return web.json_response(
        {"firmware_version": "24.44.0+gfake", "part_number": "1529455-02-D", "serial_number": "PGT00000000000"}
    )


async def control(req):
    tick()
    if "plug" in req.query:
        state["plug"] = req.query["plug"] == "1"
        if not state["plug"]:
            state["charge"] = False
        else:
            state["session_wh"] = 0.0
    if "charge" in req.query:
        state["charge"] = req.query["charge"] == "1" and state["plug"]
    return web.json_response(state)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=8081)
    p.add_argument("--speed", type=float, default=1.0, help="energy time multiplier")
    a = p.parse_args()
    SPEED = a.speed
    app = web.Application()
    app.add_routes(
        [
            web.get("/api/1/vitals", vitals),
            web.get("/api/1/lifetime", lifetime),
            web.get("/api/1/version", version),
            web.get("/control", control),
        ]
    )
    web.run_app(app, port=a.port)
