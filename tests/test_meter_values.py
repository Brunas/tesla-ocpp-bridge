from ocpp.v16.enums import Measurand as M
from ocpp.v16.enums import ReadingContext, UnitOfMeasure

from helpers import lifetime, vitals
from tesla_ocpp_bridge import bridge as br
from tesla_ocpp_bridge.state import State


def by_measurand(mv):
    out = {}
    for s in mv["sampled_value"]:
        out.setdefault(s["measurand"], []).append(s)
    return out


def test_three_phase_charging(bridge):
    bridge.state.register_wh = 1234.9
    bridge.vitals = vitals(plugged=True, charging=True, amps=16.0, volts=(230.0, 231.0, 229.0))
    mv = bridge.build_meter_value("2026-01-01T00:00:00Z", ReadingContext.sample_periodic)
    m = by_measurand(mv)

    assert mv["timestamp"] == "2026-01-01T00:00:00Z"
    assert m[M.energy_active_import_register][0]["value"] == "1234"  # integer Wh, floored
    assert m[M.energy_active_import_register][0]["unit"] == UnitOfMeasure.wh
    assert [s["phase"] for s in m[M.current_import]] == ["L1", "L2", "L3"]
    assert [s["value"] for s in m[M.voltage]] == ["230.0", "231.0", "229.0"]
    assert [s["phase"] for s in m[M.voltage]] == ["L1-N", "L2-N", "L3-N"]
    assert m[M.power_active_import][0]["value"] == str(round(16 * (230 + 231 + 229)))
    assert all(s["context"] == "Sample.Periodic" for s in mv["sampled_value"])


def test_single_phase_skips_unwired_phases(bridge):
    bridge.vitals = vitals(plugged=True, charging=True, amps=32.0, volts=(230.0, 1.2, 0.0))
    m = by_measurand(bridge.build_meter_value("t", ReadingContext.sample_periodic))
    assert [s["phase"] for s in m[M.current_import]] == ["L1"]
    assert [s["phase"] for s in m[M.voltage]] == ["L1-N"]
    assert m[M.power_active_import][0]["value"] == "7360"


def test_power_is_zero_when_contactor_open(bridge):
    bridge.vitals = vitals(plugged=True, charging=False, amps=0.4)
    m = by_measurand(bridge.build_meter_value("t", ReadingContext.sample_periodic))
    assert m[M.power_active_import][0]["value"] == "0"


def test_missing_vitals_still_report_energy(bridge):
    bridge.state.register_wh = 42
    bridge.vitals = {}
    m = by_measurand(bridge.build_meter_value("t", ReadingContext.trigger))
    assert set(m) == {M.energy_active_import_register, M.power_active_import}
    assert m[M.energy_active_import_register][0]["value"] == "42"


def test_queue_meter_is_bounded_and_keeps_transaction_messages(bridge, monkeypatch):
    monkeypatch.setattr(br, "MAX_QUEUED_METER_VALUES", 3)
    bridge.process(vitals(plugged=True), lifetime(1000))
    local_id = bridge.state.tx["local_id"]
    bridge.vitals = vitals(plugged=True, charging=True)
    for i in range(6):
        bridge.state.register_wh = 1000 + i
        bridge.queue_meter(local_id, ReadingContext.sample_periodic)

    outbox = bridge.state.outbox
    assert [m["kind"] for m in outbox] == ["start", "meter", "meter", "meter"]
    energies = [m["meter_value"][0]["sampled_value"][0]["value"] for m in outbox[1:]]
    assert energies == ["1003", "1004", "1005"]  # oldest dropped, newest kept
    assert State(bridge.cfg["state_file"]).outbox == outbox  # persisted
