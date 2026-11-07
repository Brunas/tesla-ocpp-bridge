"""OCPP charge point: handles requests coming *from* the central system."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from ocpp.routing import after, on
from ocpp.v16 import ChargePoint as OcppChargePoint
from ocpp.v16 import call, call_result
from ocpp.v16.enums import (
    Action,
    AvailabilityStatus,
    ChargingProfileStatus,
    ClearCacheStatus,
    DataTransferStatus,
    MessageTrigger,
    RemoteStartStopStatus,
    ResetStatus,
    TriggerMessageStatus,
    UnlockStatus,
)

if TYPE_CHECKING:
    from .bridge import Bridge

log = logging.getLogger(__name__)


class BridgeChargePoint(OcppChargePoint):
    def __init__(self, cp_id: str, connection, bridge: Bridge):
        super().__init__(cp_id, connection, response_timeout=30)
        self.bridge = bridge

    @on(Action.get_configuration)
    def on_get_configuration(self, key: list[str] | None = None, **_):
        all_keys = self.bridge.configuration_keys()
        if not key:
            return call_result.GetConfiguration(configuration_key=list(all_keys.values()))
        known = [all_keys[k] for k in key if k in all_keys]
        unknown = [k for k in key if k not in all_keys]
        return call_result.GetConfiguration(configuration_key=known, unknown_key=unknown or None)

    @on(Action.change_configuration)
    def on_change_configuration(self, key: str, value: str, **_):
        status = self.bridge.change_configuration(key, value)
        return call_result.ChangeConfiguration(status=status)

    @on(Action.trigger_message)
    def on_trigger_message(self, requested_message: str, **_):
        supported = {
            MessageTrigger.boot_notification,
            MessageTrigger.heartbeat,
            MessageTrigger.meter_values,
            MessageTrigger.status_notification,
        }
        ok = requested_message in supported
        return call_result.TriggerMessage(
            status=TriggerMessageStatus.accepted if ok else TriggerMessageStatus.not_implemented
        )

    @after(Action.trigger_message)
    async def after_trigger_message(self, requested_message: str, **_):
        b = self.bridge
        try:
            if requested_message == MessageTrigger.boot_notification:
                await b.send_boot(self)
            elif requested_message == MessageTrigger.heartbeat:
                await self.call(call.Heartbeat())
            elif requested_message == MessageTrigger.status_notification:
                await b.send_status(force=True)
            elif requested_message == MessageTrigger.meter_values:
                await b.send_triggered_meter_values()
        except Exception as exc:
            log.warning("Failed to answer TriggerMessage %s: %s", requested_message, exc)

    @on(Action.remote_start_transaction)
    def on_remote_start_transaction(self, id_tag: str, **_):
        log.warning("RemoteStartTransaction rejected: Wall Connector cannot be controlled")
        return call_result.RemoteStartTransaction(status=RemoteStartStopStatus.rejected)

    @on(Action.remote_stop_transaction)
    def on_remote_stop_transaction(self, transaction_id: int, **_):
        log.warning("RemoteStopTransaction rejected: Wall Connector cannot be controlled")
        return call_result.RemoteStopTransaction(status=RemoteStartStopStatus.rejected)

    @on(Action.set_charging_profile)
    def on_set_charging_profile(self, **_):
        log.info("SetChargingProfile not supported")
        return call_result.SetChargingProfile(status=ChargingProfileStatus.not_supported)

    @on(Action.unlock_connector)
    def on_unlock_connector(self, **_):
        return call_result.UnlockConnector(status=UnlockStatus.not_supported)

    @on(Action.change_availability)
    def on_change_availability(self, **_):
        return call_result.ChangeAvailability(status=AvailabilityStatus.accepted)

    @on(Action.clear_cache)
    def on_clear_cache(self, **_):
        return call_result.ClearCache(status=ClearCacheStatus.accepted)

    @on(Action.reset)
    def on_reset(self, type: str, **_):
        log.info("Reset (%s) requested by central system; reconnecting", type)
        return call_result.Reset(status=ResetStatus.accepted)

    @after(Action.reset)
    async def after_reset(self, **_):
        await asyncio.sleep(1)
        await self._connection.close()

    @on(Action.data_transfer)
    def on_data_transfer(self, vendor_id: str, **_):
        return call_result.DataTransfer(status=DataTransferStatus.unknown_vendor_id)
