"""Persistent state, so a restart or outage does not lose an open session."""

from __future__ import annotations

import json
import logging
import os

log = logging.getLogger(__name__)


class State:
    def __init__(self, path: str):
        self.path = path
        self.register_wh: float = 0.0  # monotonic energy register reported to OCPP
        self.tx: dict | None = None  # currently open transaction
        self.outbox: list[dict] = []  # transaction messages not yet delivered
        self.tx_ids: dict[str, int] = {}  # local tx id -> CSMS transaction id
        self.config: dict[str, int] = {}  # values changed by CSMS
        self._load()

    def _load(self) -> None:
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError:
            return
        except Exception as exc:  # corrupted file: start clean but keep a copy
            log.error("Could not read state file %s: %s", self.path, exc)
            os.replace(self.path, self.path + ".broken")
            return
        self.register_wh = float(data.get("register_wh", 0))
        self.tx = data.get("tx")
        self.outbox = data.get("outbox", [])
        self.tx_ids = data.get("tx_ids", {})
        self.config = data.get("config", {})

    def save(self) -> None:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "register_wh": self.register_wh,
                    "tx": self.tx,
                    "outbox": self.outbox,
                    "tx_ids": self.tx_ids,
                    "config": self.config,
                },
                f,
                indent=1,
            )
        os.replace(tmp, self.path)
