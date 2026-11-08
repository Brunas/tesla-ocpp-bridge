import pytest
from ocpp.v16.enums import ConfigurationStatus as C

from tesla_ocpp_bridge.state import State


def test_defaults_exposed(bridge):
    keys = bridge.configuration_keys()
    assert keys["HeartbeatInterval"] == {"key": "HeartbeatInterval", "readonly": False, "value": "300"}
    assert keys["MeterValueSampleInterval"]["value"] == "60"
    assert keys["NumberOfConnectors"]["value"] == "1"
    assert keys["NumberOfConnectors"]["readonly"] is True
    assert all(isinstance(k["value"], str) for k in keys.values())


@pytest.mark.parametrize("key", ["HeartbeatInterval", "MeterValueSampleInterval"])
def test_writable_keys(bridge, cfg, key):
    assert bridge.change_configuration(key, "15") == C.accepted
    assert bridge.configuration_keys()[key]["value"] == "15"
    assert State(cfg["state_file"]).config[key] == 15  # survives restart


def test_interval_properties_use_override(bridge):
    bridge.change_configuration("HeartbeatInterval", "45")
    bridge.change_configuration("MeterValueSampleInterval", "0")
    assert bridge.heartbeat_interval == 45
    assert bridge.meter_interval == 0


@pytest.mark.parametrize("value", ["-1", "abc", "1.5", ""])
def test_invalid_values_rejected(bridge, value):
    assert bridge.change_configuration("HeartbeatInterval", value) == C.rejected
    assert bridge.heartbeat_interval == 300


def test_readonly_key_rejected(bridge):
    assert bridge.change_configuration("NumberOfConnectors", "2") == C.rejected


def test_unknown_key_not_supported(bridge):
    assert bridge.change_configuration("FooBar", "1") == C.not_supported
