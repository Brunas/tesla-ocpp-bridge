import pytest

from helpers import CP_ID
from tesla_ocpp_bridge.bridge import Bridge
from tesla_ocpp_bridge.config import DEFAULTS


@pytest.fixture
def cfg(tmp_path):
    c = dict(DEFAULTS)
    c.update(charge_point_id=CP_ID, twc_host="192.0.2.10",
             state_file=str(tmp_path / "state.json"))
    return c


@pytest.fixture
def bridge(cfg):
    b = Bridge(cfg)
    b.twc_ok = True
    return b
