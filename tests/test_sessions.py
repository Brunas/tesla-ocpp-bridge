"""Session tracking and the energy register (Bridge.process)."""

from ocpp.v16.enums import ReadingContext, Reason

from helpers import lifetime, vitals
from tesla_ocpp_bridge.bridge import Bridge
from tesla_ocpp_bridge.state import State


def kinds(bridge):
    return [m["kind"] for m in bridge.state.outbox]


def test_idle_does_nothing(bridge):
    bridge.process(vitals(), lifetime(1000))
    assert bridge.state.tx is None
    assert bridge.state.outbox == []
    assert bridge.state.register_wh == 1000


def test_plug_in_starts_transaction(bridge, cfg):
    bridge.process(vitals(plugged=True), lifetime(1000))
    tx = bridge.state.tx
    assert tx is not None
    assert kinds(bridge) == ["start"]
    start = bridge.state.outbox[0]
    assert start["meter_start"] == 1000
    assert start["id_tag"] == cfg["id_tag"]
    assert start["local_id"] == tx["local_id"]
    assert bridge.outbox_event.is_set()


def test_staying_plugged_does_not_start_again(bridge):
    bridge.process(vitals(plugged=True), lifetime(1000))
    bridge.process(vitals(plugged=True, charging=True, session_wh=100), lifetime(1000))
    bridge.process(vitals(plugged=True, session_wh=200), lifetime(1000))
    assert kinds(bridge) == ["start"]


def test_unplug_stops_transaction(bridge):
    bridge.process(vitals(plugged=True), lifetime(1000))
    local_id = bridge.state.tx["local_id"]
    bridge.process(vitals(plugged=True, charging=True, session_wh=500), lifetime(1000))
    bridge.process(vitals(plugged=False), lifetime(1000))

    assert bridge.state.tx is None
    assert kinds(bridge) == ["start", "meter", "stop"]
    end_meter, stop = bridge.state.outbox[1], bridge.state.outbox[2]
    assert stop["local_id"] == local_id
    assert stop["meter_stop"] == 1500
    assert stop["reason"] == Reason.ev_disconnected
    sv = end_meter["meter_value"][0]["sampled_value"][0]
    assert sv["context"] == ReadingContext.transaction_end
    assert sv["value"] == "1500"


def test_register_follows_session_energy_when_lifetime_lags(bridge):
    bridge.process(vitals(plugged=True), lifetime(1000))
    bridge.process(vitals(plugged=True, charging=True, session_wh=750), lifetime(1000))
    assert bridge.state.register_wh == 1750


def test_register_follows_lifetime_when_it_is_ahead(bridge):
    bridge.process(vitals(plugged=True), lifetime(1000))
    bridge.process(vitals(plugged=True, charging=True, session_wh=500), lifetime(2000))
    assert bridge.state.register_wh == 2000


def test_no_double_counting_when_lifetime_catches_up(bridge):
    bridge.process(vitals(plugged=True), lifetime(1000))
    bridge.process(vitals(plugged=True, charging=True, session_wh=500), lifetime(1000))
    bridge.process(vitals(plugged=True, charging=True, session_wh=600), lifetime(1600))
    assert bridge.state.register_wh == 1600


def test_register_never_decreases(bridge):
    bridge.state.register_wh = 5000
    bridge.process(vitals(), lifetime(1000))
    assert bridge.state.register_wh == 5000
    bridge.process(vitals(plugged=True), lifetime(1000))
    assert bridge.state.outbox[0]["meter_start"] == 5000


def test_bridge_started_mid_session_counts_only_new_energy(bridge):
    # Wall Connector already reports 300 Wh for this session when we first see it.
    bridge.process(vitals(plugged=True, charging=True, session_wh=300), lifetime(1000))
    assert bridge.state.outbox[0]["meter_start"] == 1000
    bridge.process(vitals(plugged=True, charging=True, session_wh=400), lifetime(1000))
    assert bridge.state.register_wh == 1100


def test_session_counter_reset_without_unplug(bridge):
    bridge.process(vitals(plugged=True), lifetime(1000))
    bridge.process(vitals(plugged=True, charging=True, session_wh=500), lifetime(1000))
    # Wall Connector resets its session counter while the car stays plugged in.
    bridge.process(vitals(plugged=True, charging=True, session_wh=100), lifetime(1000))
    assert bridge.state.register_wh == 1600
    bridge.process(vitals(plugged=True, charging=True, session_wh=200), lifetime(1000))
    assert bridge.state.register_wh == 1700
    assert kinds(bridge) == ["start"]


def test_state_is_persisted_after_each_poll(bridge, cfg):
    bridge.process(vitals(plugged=True, charging=True, session_wh=0), lifetime(1000))
    bridge.process(vitals(plugged=True, charging=True, session_wh=250), lifetime(1000))
    saved = State(cfg["state_file"])
    assert saved.tx["local_id"] == bridge.state.tx["local_id"]
    assert saved.register_wh == 1250
    assert [m["kind"] for m in saved.outbox] == ["start"]


def test_restart_resumes_open_transaction(cfg):
    first = Bridge(cfg)
    first.twc_ok = True
    first.process(vitals(plugged=True), lifetime(1000))
    first.process(vitals(plugged=True, charging=True, session_wh=500), lifetime(1000))
    local_id = first.state.tx["local_id"]

    second = Bridge(cfg)  # same state file, e.g. after an app restart
    second.twc_ok = True
    second.process(vitals(plugged=True, charging=True, session_wh=600), lifetime(1000))
    assert second.state.tx["local_id"] == local_id
    assert second.state.register_wh == 1600
    assert kinds(second) == ["start"]  # no second StartTransaction

    second.process(vitals(plugged=False), lifetime(1000))
    stop = second.state.outbox[-1]
    assert stop["kind"] == "stop" and stop["local_id"] == local_id
    assert stop["meter_stop"] == 1600


def test_missing_fields_are_treated_as_zero(bridge):
    bridge.process({"vehicle_connected": True}, {})
    assert bridge.state.tx["meter_start"] == 0
    bridge.process({"vehicle_connected": False}, {})
    assert kinds(bridge) == ["start", "meter", "stop"]
