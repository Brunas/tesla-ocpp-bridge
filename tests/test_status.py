import pytest
from ocpp.v16.enums import ChargePointStatus as S

from helpers import lifetime, vitals


@pytest.mark.parametrize(
    "twc_ok,v,has_tx,expected",
    [
        (False, vitals(plugged=True, charging=True), True, S.unavailable),
        (True, vitals(), False, S.available),
        (True, vitals(plugged=True), False, S.preparing),
        (True, vitals(plugged=True, session_wh=0), True, S.preparing),
        (True, vitals(plugged=True, charging=True, session_wh=10), True, S.charging),
        (True, vitals(plugged=True, charging=False, session_wh=10), True, S.suspended_ev),
    ],
)
def test_compute_status(bridge, twc_ok, v, has_tx, expected):
    bridge.twc_ok = twc_ok
    bridge.vitals = v
    bridge.state.tx = {"local_id": "x"} if has_tx else None
    assert bridge.compute_status() == expected


def test_status_sequence_over_a_session(bridge):
    seen = []

    def poll(v):
        bridge.process(v, lifetime(1000))
        if not seen or seen[-1] != bridge.status:
            seen.append(bridge.status)

    poll(vitals())
    poll(vitals(plugged=True))
    poll(vitals(plugged=True, charging=True, session_wh=0))
    poll(vitals(plugged=True, charging=True, session_wh=500))
    poll(vitals(plugged=True, charging=False, session_wh=800))  # car full / paused
    poll(vitals())
    assert seen == [S.available, S.preparing, S.charging, S.suspended_ev, S.available]


def test_update_status_signals_only_on_change(bridge):
    bridge.vitals = vitals()
    bridge.update_status()
    assert bridge.status == S.available and bridge.status_event.is_set()

    bridge.status_event.clear()
    bridge.update_status()
    assert not bridge.status_event.is_set()

    bridge.twc_ok = False
    bridge.update_status()
    assert bridge.status == S.unavailable and bridge.status_event.is_set()
