"""Bridge: polls the Wall Connector, tracks sessions, talks to the central system."""

from __future__ import annotations

import asyncio
import base64
import logging
import ssl
import uuid
from datetime import UTC, datetime
from typing import Any

import aiohttp
from ocpp.v16 import call
from ocpp.v16.enums import (
    ChargePointErrorCode,
    ChargePointStatus,
    ConfigurationStatus,
    Measurand,
    Phase,
    ReadingContext,
    Reason,
    RegistrationStatus,
    UnitOfMeasure,
)
from websockets.asyncio.client import connect

from .charge_point import BridgeChargePoint
from .state import State
from .wall_connector import WallConnector

log = logging.getLogger(__name__)

CONNECTOR_ID = 1
MAX_QUEUED_METER_VALUES = 500
TWC_OFFLINE_AFTER_S = 60
TWC_WARN_AFTER_S = 15  # outages up to this long (one timed-out poll) are logged at debug level


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


class Bridge:
    def __init__(self, cfg: dict[str, Any]):
        self.cfg = cfg
        self.state = State(cfg["state_file"])
        self.cp: BridgeChargePoint | None = None  # set while connected and booted
        self.twc: WallConnector | None = None

        self.vitals: dict = {}
        self.lifetime: dict = {}
        self.version: dict = {}
        self.twc_ok = False  # becomes True after first successful poll
        self.twc_seen = False

        self.status: str | None = None
        self.last_sent_status: str | None = None

        self.outbox_event = asyncio.Event()
        self.status_event = asyncio.Event()
        self.first_poll = asyncio.Event()
        self.boot_accepted = asyncio.Event()  # per connection; set by any accepted boot
        self.boot_retry_after = 60

    # ---- configuration exposed via GetConfiguration -------------------------

    @property
    def heartbeat_interval(self) -> int:
        return int(self.state.config.get("HeartbeatInterval", self.cfg["heartbeat_interval"]))

    @property
    def meter_interval(self) -> int:
        return int(
            self.state.config.get("MeterValueSampleInterval", self.cfg["meter_value_interval"])
        )

    def configuration_keys(self) -> dict[str, dict]:
        def k(key, value, readonly=True):
            return {"key": key, "readonly": readonly, "value": str(value)}

        keys = [
            k("HeartbeatInterval", self.heartbeat_interval, readonly=False),
            k("MeterValueSampleInterval", self.meter_interval, readonly=False),
            k("MeterValuesSampledData",
              "Energy.Active.Import.Register,Power.Active.Import,Current.Import,Voltage"),
            k("NumberOfConnectors", 1),
            k("SupportedFeatureProfiles", "Core,RemoteTrigger"),
            k("AuthorizeRemoteTxRequests", "false"),
            k("LocalPreAuthorize", "false"),
            k("StopTransactionOnEVSideDisconnect", "true"),
            k("ConnectionTimeOut", 60),
        ]
        return {item["key"]: item for item in keys}

    def change_configuration(self, key: str, value: str) -> str:
        if key not in ("HeartbeatInterval", "MeterValueSampleInterval"):
            if key in self.configuration_keys():
                return ConfigurationStatus.rejected  # known but read-only
            return ConfigurationStatus.not_supported
        try:
            ival = int(value)
            if ival < 0:
                raise ValueError
        except ValueError:
            return ConfigurationStatus.rejected
        self.state.config[key] = ival
        self.state.save()
        log.info("Configuration %s set to %s by central system", key, ival)
        return ConfigurationStatus.accepted

    # ---- Wall Connector polling ---------------------------------------------

    async def poll_loop(self) -> None:
        loop = asyncio.get_running_loop()
        last_ok = loop.time()
        fail_count = 0
        down_since = 0.0  # start of the first failed poll of the current outage
        warned = False
        while True:
            started = loop.time()
            try:
                vitals = await self.twc.get("vitals")
                lifetime = await self.twc.get("lifetime")
                if not self.version:
                    self.version = await self.twc.get("version")
                    log.info(
                        "Wall Connector: serial=%s firmware=%s part=%s",
                        self.version.get("serial_number"),
                        self.version.get("firmware_version"),
                        self.version.get("part_number"),
                    )
                if fail_count:
                    log.log(logging.INFO if warned else logging.DEBUG,
                            "Wall Connector reachable again after %.0fs",
                            loop.time() - down_since)
                fail_count = 0
                warned = False
                last_ok = loop.time()
                self.twc_ok = True
                self.twc_seen = True
                self.process(vitals, lifetime)
            except Exception as exc:
                fail_count += 1
                if fail_count == 1:
                    down_since = started
                down = loop.time() - down_since
                log.debug("Wall Connector poll failed (%d): %r", fail_count, exc)
                # Wi-Fi blips of a few seconds are common: only warn once it stays down,
                # but right away if it was never reachable (wrong address?).
                if (not warned and (down > TWC_WARN_AFTER_S or not self.twc_seen)) or (
                    warned and fail_count % 60 == 0
                ):
                    log.warning("Wall Connector unreachable for %.0fs, %d failed poll(s): %r",
                                down, fail_count, exc)
                    warned = True
                if loop.time() - last_ok > TWC_OFFLINE_AFTER_S:
                    self.twc_ok = False
                    self.update_status()
            self.first_poll.set()
            await asyncio.sleep(self.cfg["poll_interval"])

    def process(self, vitals: dict, lifetime: dict) -> None:
        self.vitals, self.lifetime = vitals, lifetime
        plugged = bool(vitals.get("vehicle_connected"))
        lifetime_wh = float(lifetime.get("energy_wh") or 0)
        session_wh = float(vitals.get("session_energy_wh") or 0)
        st = self.state

        # Energy register: never decreases. Lifetime counter is authoritative, but
        # may lag during a session, so we also track base + session energy.
        st.register_wh = max(st.register_wh, lifetime_wh)
        tx = st.tx
        if tx is not None:
            if session_wh < tx.get("last_session_wh", 0):
                # Wall Connector started a new internal session without unplug.
                tx["session_base_wh"] = st.register_wh
                tx["session_offset_wh"] = 0.0
            tx["last_session_wh"] = session_wh
            st.register_wh = max(
                st.register_wh,
                tx["session_base_wh"] + session_wh - tx.get("session_offset_wh", 0.0),
            )

        if plugged and tx is None:
            self.start_transaction(session_wh)
        elif not plugged and tx is not None:
            self.stop_transaction(Reason.ev_disconnected)

        st.save()
        self.update_status()

    def start_transaction(self, session_wh: float) -> None:
        st = self.state
        local_id = uuid.uuid4().hex[:12]
        meter_start = int(st.register_wh)
        st.tx = {
            "local_id": local_id,
            "id_tag": self.cfg["id_tag"],
            "meter_start": meter_start,
            "start_ts": now_iso(),
            "session_base_wh": st.register_wh,
            "session_offset_wh": session_wh,
            "last_session_wh": session_wh,
        }
        st.outbox.append(
            {
                "kind": "start",
                "local_id": local_id,
                "id_tag": self.cfg["id_tag"],
                "meter_start": meter_start,
                "timestamp": st.tx["start_ts"],
            }
        )
        log.info("Vehicle plugged in -> starting transaction (meter %d Wh)", meter_start)
        self.outbox_event.set()

    def stop_transaction(self, reason: str) -> None:
        st = self.state
        tx = st.tx
        meter_stop = max(int(st.register_wh), tx["meter_start"])
        ts = now_iso()
        st.outbox.append(
            {
                "kind": "meter",
                "local_id": tx["local_id"],
                "meter_value": [self.build_meter_value(ts, ReadingContext.transaction_end)],
            }
        )
        st.outbox.append(
            {
                "kind": "stop",
                "local_id": tx["local_id"],
                "id_tag": tx["id_tag"],
                "meter_stop": meter_stop,
                "timestamp": ts,
                "reason": reason,
            }
        )
        log.info(
            "Transaction ended (%s): %.3f kWh",
            reason,
            (meter_stop - tx["meter_start"]) / 1000,
        )
        st.tx = None
        self.outbox_event.set()

    # ---- connector status ----------------------------------------------------

    def compute_status(self) -> str:
        if not self.twc_ok:
            return ChargePointStatus.unavailable
        if not self.vitals.get("vehicle_connected"):
            return ChargePointStatus.available
        if self.vitals.get("contactor_closed"):
            return ChargePointStatus.charging
        if self.state.tx is None or not self.vitals.get("session_energy_wh"):
            return ChargePointStatus.preparing  # plugged in, nothing delivered yet
        return ChargePointStatus.suspended_ev  # paused / finished (car stopped drawing)

    def update_status(self) -> None:
        new = self.compute_status()
        if new != self.status:
            log.info("Connector status: %s -> %s", self.status, new)
            self.status = new
            self.status_event.set()

    async def send_status(self, force: bool = False) -> None:
        cp = self.cp
        if cp is None or self.status is None:
            return
        if not force and self.status == self.last_sent_status:
            return
        alerts = self.vitals.get("current_alerts") or []
        info = ",".join(str(a) for a in alerts)[:50] or None
        await cp.call(
            call.StatusNotification(
                connector_id=CONNECTOR_ID,
                error_code=ChargePointErrorCode.no_error,
                status=self.status,
                timestamp=now_iso(),
                info=info,
            )
        )
        self.last_sent_status = self.status

    # ---- meter values -----------------------------------------------------------

    def build_meter_value(self, ts: str, context: str) -> dict:
        v = self.vitals
        sampled = [
            {
                "value": str(int(self.state.register_wh)),
                "context": context,
                "measurand": Measurand.energy_active_import_register,
                "unit": UnitOfMeasure.wh,
            }
        ]
        phases = [
            (Phase.l1_n, Phase.l1, v.get("voltageA_v"), v.get("currentA_a")),
            (Phase.l2_n, Phase.l2, v.get("voltageB_v"), v.get("currentB_a")),
            (Phase.l3_n, Phase.l3, v.get("voltageC_v"), v.get("currentC_a")),
        ]
        power_w = 0.0
        for vphase, iphase, volts, amps in phases:
            if volts is None or amps is None:
                continue
            volts, amps = float(volts), float(amps)
            if volts < 50:  # phase not wired (single-phase install)
                continue
            power_w += volts * amps
            sampled.append({"value": f"{amps:.1f}", "context": context,
                            "measurand": Measurand.current_import, "phase": iphase,
                            "unit": UnitOfMeasure.a})
            sampled.append({"value": f"{volts:.1f}", "context": context,
                            "measurand": Measurand.voltage, "phase": vphase,
                            "unit": UnitOfMeasure.v})
        if not v.get("contactor_closed"):
            power_w = 0.0
        sampled.append({"value": f"{power_w:.0f}", "context": context,
                        "measurand": Measurand.power_active_import, "unit": UnitOfMeasure.w})
        return {"timestamp": ts, "sampled_value": sampled}

    async def meter_loop(self) -> None:
        await self.first_poll.wait()
        while True:
            interval = self.meter_interval
            await asyncio.sleep(interval if interval > 0 else 60)
            tx = self.state.tx
            if interval <= 0 or tx is None or not self.twc_ok:
                continue
            self.queue_meter(tx["local_id"], ReadingContext.sample_periodic)

    def queue_meter(self, local_id: str, context: str) -> None:
        st = self.state
        st.outbox.append(
            {"kind": "meter", "local_id": local_id,
             "meter_value": [self.build_meter_value(now_iso(), context)]}
        )
        # While offline, keep the queue bounded by dropping the oldest samples.
        meters = [i for i, m in enumerate(st.outbox) if m["kind"] == "meter"]
        for idx in reversed(meters[: max(0, len(meters) - MAX_QUEUED_METER_VALUES)]):
            del st.outbox[idx]
        st.save()
        self.outbox_event.set()

    async def send_triggered_meter_values(self) -> None:
        tx = self.state.tx
        mv = [self.build_meter_value(now_iso(), ReadingContext.trigger)]
        if tx is not None and tx["local_id"] in self.state.tx_ids:
            await self.cp.call(call.MeterValues(
                connector_id=CONNECTOR_ID, meter_value=mv,
                transaction_id=self.state.tx_ids[tx["local_id"]]))
        else:
            await self.cp.call(call.MeterValues(connector_id=CONNECTOR_ID, meter_value=mv))

    # ---- delivering queued transaction messages ----------------------------------

    async def deliver(self, cp: BridgeChargePoint, item: dict) -> None:
        st = self.state
        kind = item["kind"]
        if kind == "start":
            res = await cp.call(call.StartTransaction(
                connector_id=CONNECTOR_ID, id_tag=item["id_tag"],
                meter_start=item["meter_start"], timestamp=item["timestamp"]))
            if res is None:
                # Don't block the queue forever; following messages for it get dropped.
                log.error("StartTransaction refused with CallError; dropping transaction")
                return
            st.tx_ids[item["local_id"]] = res.transaction_id
            status = (res.id_tag_info or {}).get("status")
            log.info("Transaction id %s assigned (idTag status: %s)", res.transaction_id, status)
            if status and status != "Accepted":
                log.warning("Central system did not accept idTag '%s': %s", item["id_tag"], status)
            return

        tx_id = st.tx_ids.get(item["local_id"])
        if tx_id is None:
            log.warning("Dropping %s for unknown transaction %s", kind, item["local_id"])
            return
        if kind == "meter":
            await cp.call(call.MeterValues(
                connector_id=CONNECTOR_ID, meter_value=item["meter_value"],
                transaction_id=tx_id))
        elif kind == "stop":
            await cp.call(call.StopTransaction(
                meter_stop=item["meter_stop"], timestamp=item["timestamp"],
                transaction_id=tx_id, reason=item["reason"], id_tag=item["id_tag"]))
            st.tx_ids.pop(item["local_id"], None)

    async def outbox_loop(self, cp: BridgeChargePoint) -> None:
        st = self.state
        while True:
            while st.outbox:
                item = st.outbox[0]
                await self.deliver(cp, item)  # raises on timeout/disconnect -> reconnect
                st.outbox.pop(0)
                st.save()
            self.outbox_event.clear()
            await self.outbox_event.wait()

    async def status_loop(self) -> None:
        while True:
            await self.status_event.wait()
            self.status_event.clear()
            await self.send_status()

    async def heartbeat_loop(self, cp: BridgeChargePoint) -> None:
        while True:
            await asyncio.sleep(max(self.heartbeat_interval, 10))
            await cp.call(call.Heartbeat())

    # ---- connection management -----------------------------------------------------

    async def send_boot(self, cp: BridgeChargePoint) -> bool:
        res = await cp.call(call.BootNotification(
            charge_point_vendor=self.cfg["vendor"][:20],
            charge_point_model=self.cfg["model"][:20],
            charge_point_serial_number=(self.version.get("serial_number") or None),
            firmware_version=(self.version.get("firmware_version") or None),
        ))
        if res is None:
            raise RuntimeError("BootNotification returned CallError")
        if res.status == RegistrationStatus.accepted:
            if res.interval and "HeartbeatInterval" not in self.state.config:
                self.cfg["heartbeat_interval"] = int(res.interval)
            log.info("BootNotification accepted (heartbeat %ss)", self.heartbeat_interval)
            self.boot_accepted.set()
            return True
        self.boot_retry_after = res.interval or 60
        log.warning("BootNotification %s, retrying in %ss", res.status, self.boot_retry_after)
        return False

    async def boot(self, cp: BridgeChargePoint) -> None:
        """Send BootNotification until it is accepted.

        While Pending, the central system may send TriggerMessage(BootNotification).
        If that triggered boot is accepted, stop waiting instead of booting twice.
        """
        self.boot_accepted.clear()
        while not await self.send_boot(cp):
            try:
                await asyncio.wait_for(self.boot_accepted.wait(), self.boot_retry_after)
                return
            except TimeoutError:
                pass

    async def session(self, cp: BridgeChargePoint) -> None:
        await self.boot(cp)
        self.cp = cp
        self.last_sent_status = None
        self.status_event.set()
        await asyncio.gather(
            self.heartbeat_loop(cp), self.outbox_loop(cp), self.status_loop()
        )

    def ws_url(self) -> str:
        base = self.cfg["ocpp_url"].rstrip("/")
        cp_id = self.cfg["charge_point_id"]
        return base if base.endswith("/" + cp_id) else f"{base}/{cp_id}"

    def auth_headers(self) -> dict[str, str]:
        if not self.cfg["ocpp_password"]:
            return {}
        token = f"{self.cfg['charge_point_id']}:{self.cfg['ocpp_password']}"
        return {"Authorization": "Basic " + base64.b64encode(token.encode()).decode()}

    def ssl_context(self, url: str) -> ssl.SSLContext | None:
        if not url.startswith("wss://"):
            return None
        ctx = ssl.create_default_context()
        if not self.cfg["verify_tls"]:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        return ctx

    async def connection_loop(self) -> None:
        url = self.ws_url()
        headers = self.auth_headers()
        kwargs: dict[str, Any] = {}
        ctx = self.ssl_context(url)
        if ctx is not None:
            kwargs["ssl"] = ctx

        backoff = 5
        while True:
            log.info("Connecting to %s", url)
            try:
                async with connect(
                    url,
                    subprotocols=["ocpp1.6"],
                    additional_headers=headers,
                    open_timeout=20,
                    ping_interval=60,
                    ping_timeout=30,
                    **kwargs,
                ) as ws:
                    if ws.subprotocol != "ocpp1.6":
                        log.warning("Server did not confirm ocpp1.6 subprotocol (%s)",
                                    ws.subprotocol)
                    log.info("Connected")
                    backoff = 5
                    cp = BridgeChargePoint(self.cfg["charge_point_id"], ws, self)
                    tasks = [asyncio.create_task(cp.start()), asyncio.create_task(self.session(cp))]
                    try:
                        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                        for t in done:
                            if t.exception():
                                raise t.exception()
                    finally:
                        for t in tasks:
                            t.cancel()
                        await asyncio.gather(*tasks, return_exceptions=True)
                log.warning("Connection closed")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("Connection error: %r", exc)
            finally:
                self.cp = None
            log.info("Reconnecting in %ss", backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 300)

    async def run(self) -> None:
        async with aiohttp.ClientSession() as http:
            self.twc = WallConnector(self.cfg["twc_host"], http)
            if self.state.tx:
                log.info("Resuming open transaction %s from state file", self.state.tx["local_id"])
            if self.state.outbox:
                log.info("%d queued message(s) waiting for delivery", len(self.state.outbox))
            poller = asyncio.create_task(self.poll_loop())
            meter = asyncio.create_task(self.meter_loop())
            # Wait briefly for the first poll so BootNotification includes serial/firmware.
            try:
                await asyncio.wait_for(self.first_poll.wait(), timeout=15)
            except TimeoutError:
                pass
            await asyncio.gather(poller, meter, self.connection_loop())
