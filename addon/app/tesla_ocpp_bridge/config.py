"""Configuration: defaults <- /data/options.json (Home Assistant) <- environment."""

from __future__ import annotations

import json
import os
from typing import Any

DEFAULTS: dict[str, Any] = {
    "ocpp_url": "wss://ocpp.circlelink.app",
    "charge_point_id": "",
    "ocpp_password": "",  # optional, OCPP security profile 1/2 (HTTP Basic auth)
    "twc_host": "",  # IP or hostname of the Wall Connector
    "id_tag": "TESLA",
    "poll_interval": 5,  # seconds between Wall Connector polls
    "meter_value_interval": 60,  # seconds; CSMS may override via ChangeConfiguration
    "heartbeat_interval": 300,  # seconds; CSMS normally overrides via BootNotification
    "vendor": "Tesla",
    "model": "Wall Connector Gen 3",
    "verify_tls": True,
    "state_file": "/data/state.json",
    "log_level": "info",
}


def load_config() -> dict[str, Any]:
    """Defaults <- /data/options.json (Home Assistant app) <- environment."""
    cfg = dict(DEFAULTS)
    options_file = os.environ.get("OPTIONS_FILE", "/data/options.json")
    if os.path.exists(options_file):
        with open(options_file, encoding="utf-8") as f:
            cfg.update({k: v for k, v in json.load(f).items() if v not in (None, "")})
    for key, default in DEFAULTS.items():
        raw = os.environ.get(key.upper())
        if raw is None or raw == "":
            continue
        if isinstance(default, bool):
            cfg[key] = raw.strip().lower() in ("1", "true", "yes", "on")
        elif isinstance(default, int):
            cfg[key] = int(raw)
        else:
            cfg[key] = raw
    missing = [k for k in ("charge_point_id", "twc_host") if not cfg[k]]
    if missing:
        raise SystemExit(f"Missing required configuration: {', '.join(missing)}")
    return cfg
