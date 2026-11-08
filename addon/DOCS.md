# Tesla Wall Connector OCPP Bridge

Makes a Tesla Wall Connector Gen 3 appear as an OCPP 1.6J charge point to a
backend such as CircleLink. Sessions (plug-in to unplug), energy and status
are read from the Wall Connector's local API (`http://<ip>/api/1/vitals`).

Other chargers on your local network that answer the same API (`/api/1/vitals`,
`/api/1/lifetime`) may work too, but only the Gen 3 has been tested. The
[README](https://github.com/Brunas/tesla-ocpp-bridge#other-chargers) lists the
fields the app needs.

## Options

Set these on the *Configuration* tab. **Charger ID**, **Wall Connector address**
and **ID tag** are required. The app won't start until the first two are
filled in, and the ID tag must match your CircleLink station name.

| Option | Meaning |
|---|---|
| `ocpp_url` | OCPP server, without the charger ID (`wss://ocpp.circlelink.app`) |
| `charge_point_id` | **Required.** Charger ID from the CircleLink app (appended to the URL) |
| `ocpp_password` | Only if the backend gave you a password (HTTP Basic auth) |
| `twc_host` | **Required.** IP address of the Wall Connector (give it a DHCP reservation) |
| `id_tag` | **Required.** Exactly the station name you gave the charger in CircleLink. Reported as the idTag of every session |
| `meter_value_interval` | Seconds between meter readings during a session (0 = off) |
| `poll_interval` | Seconds between Wall Connector polls |
| `verify_tls` | Set `false` only if the backend has a broken certificate |

## Limitations

The Wall Connector API is read-only. Remote start/stop and smart-charging
profiles from the backend are rejected. Use the car (or the Tesla integration
in Home Assistant) to control charging.

State (open session, unsent messages) is kept in `/data/state.json`, so a
restart or an internet outage does not lose a session.
