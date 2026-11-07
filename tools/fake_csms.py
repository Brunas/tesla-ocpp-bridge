#!/usr/bin/env python3
"""Minimal OCPP 1.6J central system that logs everything the bridge sends.

    python tools/fake_csms.py --port 9000
    # then run the bridge with OCPP_URL=ws://localhost:9000
"""
import argparse
import asyncio
import itertools
import logging
from datetime import UTC, datetime

import websockets
from ocpp.routing import on
from ocpp.v16 import ChargePoint, call_result
from ocpp.v16.enums import Action, AuthorizationStatus, RegistrationStatus

logging.basicConfig(level=logging.INFO, format="%(asctime)s CSMS %(message)s")
log = logging.getLogger("csms")
tx_counter = itertools.count(1001)


def now():
    return datetime.now(UTC).isoformat()


class CentralChargePoint(ChargePoint):
    @on(Action.boot_notification)
    def on_boot(self, **kw):
        log.info("BootNotification %s", kw)
        return call_result.BootNotification(current_time=now(), interval=30,
                                            status=RegistrationStatus.accepted)

    @on(Action.heartbeat)
    def on_heartbeat(self):
        log.info("Heartbeat")
        return call_result.Heartbeat(current_time=now())

    @on(Action.status_notification)
    def on_status(self, **kw):
        log.info("StatusNotification %s %s", kw["status"], kw.get("info") or "")
        return call_result.StatusNotification()

    @on(Action.start_transaction)
    def on_start_transaction(self, **kw):
        tid = next(tx_counter)
        log.info("StartTransaction -> id %s %s", tid, kw)
        return call_result.StartTransaction(transaction_id=tid,
                                            id_tag_info={"status": AuthorizationStatus.accepted})

    @on(Action.meter_values)
    def on_meter(self, **kw):
        sv = {s.get("measurand"): s["value"] for s in kw["meter_value"][0]["sampled_value"]
              if s.get("phase") in (None,)}
        log.info("MeterValues tx=%s %s", kw.get("transaction_id"), sv)
        return call_result.MeterValues()

    @on(Action.stop_transaction)
    def on_stop_transaction(self, **kw):
        log.info("StopTransaction %s", kw)
        return call_result.StopTransaction()


async def handler(ws):
    cp_id = ws.request.path.strip("/")
    log.info("Charge point connected: %s (subprotocol %s, auth %s)", cp_id, ws.subprotocol,
             ws.request.headers.get("Authorization"))
    await CentralChargePoint(cp_id, ws).start()


async def main(port):
    async with websockets.serve(handler, "0.0.0.0", port, subprotocols=["ocpp1.6"]):
        await asyncio.Future()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=9000)
    asyncio.run(main(p.parse_args().port))
