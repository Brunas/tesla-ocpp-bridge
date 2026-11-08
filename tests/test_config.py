import json
import ssl

import pytest

from tesla_ocpp_bridge.bridge import Bridge
from tesla_ocpp_bridge.config import DEFAULTS, load_config
from tesla_ocpp_bridge.wall_connector import WallConnector


@pytest.fixture
def env(monkeypatch, tmp_path):
    """Clean environment: no options file, no config env vars."""
    for key in DEFAULTS:
        monkeypatch.delenv(key.upper(), raising=False)
    monkeypatch.setenv("OPTIONS_FILE", str(tmp_path / "options.json"))
    return monkeypatch


def write_options(tmp_path, **options):
    (tmp_path / "options.json").write_text(json.dumps(options))


def test_required_values_missing(env):
    with pytest.raises(SystemExit, match="charge_point_id, twc_host"):
        load_config()


def test_env_values_are_cast_to_default_types(env):
    env.setenv("CHARGE_POINT_ID", "CP1")
    env.setenv("TWC_HOST", "10.0.0.5")
    env.setenv("POLL_INTERVAL", "2")
    env.setenv("VERIFY_TLS", "false")
    cfg = load_config()
    assert cfg["charge_point_id"] == "CP1"
    assert cfg["poll_interval"] == 2
    assert cfg["verify_tls"] is False
    assert cfg["ocpp_url"] == "wss://ocpp.circlelink.app"  # default kept


@pytest.mark.parametrize("raw,expected", [("1", True), ("YES", True), ("on", True), ("0", False), ("no", False)])
def test_bool_env_parsing(env, raw, expected):
    env.setenv("CHARGE_POINT_ID", "CP1")
    env.setenv("TWC_HOST", "h")
    env.setenv("VERIFY_TLS", raw)
    assert load_config()["verify_tls"] is expected


def test_home_assistant_options_file(env, tmp_path):
    write_options(tmp_path, charge_point_id="CP1", twc_host="10.0.0.5", meter_value_interval=30, ocpp_password="")
    cfg = load_config()
    assert cfg["twc_host"] == "10.0.0.5"
    assert cfg["meter_value_interval"] == 30
    assert cfg["ocpp_password"] == ""  # empty option does not override default


def test_env_overrides_options_file(env, tmp_path):
    write_options(tmp_path, charge_point_id="CP1", twc_host="10.0.0.5")
    env.setenv("TWC_HOST", "10.0.0.9")
    assert load_config()["twc_host"] == "10.0.0.9"


def test_empty_env_var_is_ignored(env, tmp_path):
    write_options(tmp_path, charge_point_id="CP1", twc_host="10.0.0.5")
    env.setenv("TWC_HOST", "")
    assert load_config()["twc_host"] == "10.0.0.5"


@pytest.mark.parametrize(
    "url,expected",
    [
        ("wss://ocpp.circlelink.app", "wss://ocpp.circlelink.app/CP1"),
        ("wss://ocpp.circlelink.app/", "wss://ocpp.circlelink.app/CP1"),
        ("wss://ocpp.circlelink.app/CP1", "wss://ocpp.circlelink.app/CP1"),
        ("ws://host:9000/ocpp/", "ws://host:9000/ocpp/CP1"),
    ],
)
def test_ws_url(cfg, url, expected):
    cfg.update(ocpp_url=url, charge_point_id="CP1")
    assert Bridge(cfg).ws_url() == expected


def test_no_auth_header_without_password(bridge):
    assert bridge.auth_headers() == {}


def test_basic_auth_header(cfg):
    cfg.update(charge_point_id="CP1", ocpp_password="secret")
    # base64("CP1:secret")
    assert Bridge(cfg).auth_headers() == {"Authorization": "Basic Q1AxOnNlY3JldA=="}


def test_ssl_context(cfg):
    b = Bridge(cfg)
    assert b.ssl_context("ws://host") is None
    ctx = b.ssl_context("wss://host")
    assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname

    cfg["verify_tls"] = False
    ctx = Bridge(cfg).ssl_context("wss://host")
    assert ctx.verify_mode == ssl.CERT_NONE and not ctx.check_hostname


@pytest.mark.parametrize(
    "host,base",
    [
        ("192.168.1.5", "http://192.168.1.5"),
        ("twc.lan:8080", "http://twc.lan:8080"),
        ("http://192.168.1.5/", "http://192.168.1.5"),
        ("https://twc.lan", "https://twc.lan"),
    ],
)
def test_wall_connector_base_url(host, base):
    assert WallConnector(host, session=None).base == base
