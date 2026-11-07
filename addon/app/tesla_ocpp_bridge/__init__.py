"""Tesla Wall Connector Gen 3 -> OCPP 1.6J bridge.

The Wall Connector Gen 3 has no OCPP support, only a read-only local HTTP API
(/api/1/vitals, /api/1/lifetime, /api/1/version). This package pretends to be
an OCPP 1.6J charge point, connects to a central system (e.g. CircleLink), and
reports plug-in sessions, status and energy derived from that local API.

It cannot start/stop charging or change current: the Wall Connector API is
read-only. Remote start/stop and charging profiles are therefore rejected.

Modules:
    config          defaults <- Home Assistant options.json <- environment
    state           persistent state (open transaction, undelivered messages)
    wall_connector  Wall Connector local HTTP API client
    charge_point    requests coming from the central system
    bridge          session tracking, energy register, outbox, connection loop
"""
