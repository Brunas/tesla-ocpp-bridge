# Tesla Wall Connector Gen 3 → OCPP bridge

[![Add repository to my Home Assistant](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2FBrunas%2Ftesla-ocpp-bridge)

The Tesla Wall Connector Gen 3 has no OCPP support, so there is nothing to
proxy. This bridge **acts as the charge point instead**. It polls the Wall
Connector's local HTTP API and talks OCPP 1.6J to the backend:

```
Wall Connector  --HTTP poll-->  bridge  --OCPP 1.6J / wss-->  ocpp.circlelink.app/<charger ID>
 /api/1/vitals                (HA app or
 /api/1/lifetime                 Docker)
```

| Wall Connector | OCPP sent |
|---|---|
| vehicle plugged in | `StatusNotification Preparing` + `StartTransaction` (meterStart = lifetime Wh) |
| contactor closed | `StatusNotification Charging` |
| charging paused/finished, still plugged | `StatusNotification SuspendedEV` |
| every `meter_value_interval` s | `MeterValues`: Energy.Active.Import.Register, Power, Current, Voltage per phase |
| vehicle unplugged | `MeterValues (Transaction.End)` + `StopTransaction` (reason EVDisconnected) |
| Wall Connector unreachable > 60 s | `StatusNotification Unavailable` |

Transaction messages are persisted in `/data/state.json` and replayed in order
after an internet outage or a restart, so sessions are not lost.

The bridge does **not** control the charger, because the Wall Connector API is
read-only. It rejects RemoteStart/Stop and SetChargingProfile.

## Before you start

1. Give the Wall Connector a fixed IP (DHCP reservation on the Asus router).
   Check that `http://<ip>/api/1/vitals` opens from the machine that will run
   the bridge.
2. In the CircleLink app, register the charger. It gives you the server URL
   `wss://ocpp.circlelink.app` and a charger ID. If it also shows a password,
   set `ocpp_password`. Keep the charger ID out of git: it lives only in the
   app's Configuration tab or your local `.env`.

## Option A: Home Assistant app (recommended)

Home Assistant runs as a VM (HA OS), so it can run apps (formerly called add-ons).

**Local app (no GitHub needed):**
1. Install the *Samba share* or *Advanced SSH & Web Terminal* app.
2. Copy the `addon/` folder to `local_apps/tesla_ocpp_bridge/` on HA (older
   apps show that share as `addons`). It is self-contained: HA builds the
   image from it.
3. Settings → Apps → *Install app* → ⋮ → *Check for updates*. The app
   appears under *Local apps*.
4. Install it, then on the *Configuration* tab fill in **Charger ID** and
   **Wall Connector address** (required) and save. Start it and watch the
   *Log* tab.

**From GitHub (one click):**

[![Add repository to my Home Assistant](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2FBrunas%2Ftesla-ocpp-bridge)

The button opens your own Home Assistant with this repository pre-filled.
Confirm, then install *Tesla Wall Connector OCPP Bridge* from the app store
and continue with step 4 above.

Without the button: Settings → Apps → *Install app* → ⋮ → *Repositories*,
then add `https://github.com/Brunas/tesla-ocpp-bridge`. The GitHub repo must
be public, because HA clones it.

## Option B: Docker on TrueNAS SCALE

Settings live in a local `.env` file, which git ignores:

```sh
cp .env.example .env       # then set CHARGE_POINT_ID and TWC_HOST
docker compose up -d --build
docker compose logs -f
```

On TrueNAS SCALE 24.10+ you can paste the compose file as a *Custom App*
(Apps → Discover → ⋮ → Install via YAML). Remove the `build:` line there. The
app then pulls `ghcr.io/<owner>/tesla-ocpp-bridge` built by GitHub Actions
(see below). Make the GHCR package public, or add registry credentials in
TrueNAS. Relative paths don't resolve in a pasted YAML. Put `.env` and `data/`
on a dataset and use absolute paths, e.g.
`env_file: /mnt/<pool>/apps/tesla-ocpp-bridge/.env`.

The Asus router (Gnuton/Merlin) could run it via Entware Python, but it's the
weakest option, so prefer HA or TrueNAS.

## Configuration

- **Home Assistant:** the app's *Configuration* tab. HA stores it in
  `/data/options.json`, which the bridge reads.
- **Docker / local runs:** environment variables, normally from `.env`
  (template with comments: [`.env.example`](.env.example)). They override
  `options.json` if both exist.

| Env | HA option | Default |
|---|---|---|
| `OCPP_URL` | `ocpp_url` | `wss://ocpp.circlelink.app` |
| `CHARGE_POINT_ID` | `charge_point_id` | – (required) |
| `OCPP_PASSWORD` | `ocpp_password` | empty |
| `TWC_HOST` | `twc_host` | – (required) |
| `ID_TAG` | `id_tag` | `TESLA` |
| `METER_VALUE_INTERVAL` | `meter_value_interval` | `60` |
| `POLL_INTERVAL` | `poll_interval` | `5` |
| `VERIFY_TLS` | `verify_tls` | `true` |
| `LOG_LEVEL` | `log_level` | `info` (`debug` prints raw OCPP frames) |
| `STATE_FILE` | – | `/data/state.json` |

## Development

### Layout

```
tesla-ocpp-bridge/
├── addon/                      # Home Assistant app = Docker build context
│   ├── config.yaml             #   app manifest (version, options schema)
│   ├── DOCS.md                 #   shown on the app page in HA
│   ├── Dockerfile              #   also used for TrueNAS / plain Docker
│   ├── requirements.txt        #   runtime dependencies (pinned)
│   └── app/tesla_ocpp_bridge/  #   the Python package
│       ├── __main__.py         #     entry point: python -m tesla_ocpp_bridge
│       ├── config.py           #     defaults <- options.json <- env vars
│       ├── state.py            #     state.json: open session + undelivered messages
│       ├── wall_connector.py   #     Wall Connector local HTTP API client
│       ├── charge_point.py     #     requests from the central system
│       └── bridge.py           #     sessions, energy register, outbox, connection
├── tests/                      # pytest unit tests (no network needed)
├── tools/                      # fake_twc.py, fake_csms.py simulators, smoke_test.sh
├── .devcontainer/ .github/ .vscode/
├── docker-compose.yml          # TrueNAS / plain Docker
├── pyproject.toml              # pytest + ruff config (dev only)
├── requirements-dev.txt
└── repository.yaml             # makes this repo an HA app repository
```

Open the folder in VS Code → *Reopen in Container* (`.devcontainer/`, Python
3.14). Or run `pip install -r requirements-dev.txt` locally (Python ≥ 3.12).

### Run against simulators

```sh
python tools/fake_twc.py --port 8081 --speed 30 &   # fake Wall Connector
python tools/fake_csms.py --port 9000 &              # fake OCPP backend
cd addon/app && OCPP_URL=ws://127.0.0.1:9000 CHARGE_POINT_ID=SIM-CP-0001 \
  TWC_HOST=127.0.0.1:8081 STATE_FILE=../../.data/state.json OPTIONS_FILE=/nonexistent \
  METER_VALUE_INTERVAL=5 python -m tesla_ocpp_bridge

curl 'localhost:8081/control?plug=1'      # plug in
curl 'localhost:8081/control?charge=1'    # start charging
curl 'localhost:8081/control?plug=0'      # unplug -> StopTransaction
```

`.vscode/launch.json` has debug configurations for all three. One of them
points the bridge at the real CircleLink and a real Wall Connector.

### Tests and lint

```sh
python -m pytest
ruff check .
```

Unit tests need no network or charger. OCPP traffic goes through an in-memory
peer (`tests/helpers.py`). Every frame is still schema-validated by the `ocpp`
library in both directions. Coverage:

| File | Covers |
|---|---|
| `test_sessions.py` | plug/unplug → Start/StopTransaction, energy register (lifetime lag, counter reset, never decreasing), restart mid-session |
| `test_ocpp_outgoing.py` | ordered offline replay, CallError/timeouts, BootNotification, StatusNotification dedupe |
| `test_ocpp_incoming.py` | GetConfiguration, ChangeConfiguration, TriggerMessage, rejected remote control, Reset |
| `test_status.py`, `test_meter_values.py` | connector status mapping, sampled values (1- and 3-phase), queue bound |
| `test_polling.py` | Wall Connector polling, outage grace period → Unavailable |
| `test_config.py`, `test_state.py`, `test_configuration_keys.py` | config sources, auth/TLS, state file, OCPP config keys |

### Container smoke test

```sh
docker build -t tesla-ocpp-bridge:smoke addon && tools/smoke_test.sh
```

Runs the built image against both simulators, each in its own container on a
private Docker network, and drives one session through it: boot, plug in,
meter values, unplug. Needs only Docker and takes about 10 s.

### CI / images

`.github/workflows/build.yml` runs on pushes to `main`, PRs, `v*` tags and
weekly (Monday 04:23 UTC):

1. `ruff check` + pytest on Python 3.12, 3.13 and 3.14;
2. an amd64 image build plus the container smoke test above. On PRs, CI stops
   here;
3. otherwise, an amd64 image is pushed to
   `ghcr.io/<owner>/tesla-ocpp-bridge` with tags `main`, `latest`, `sha-…`
   and, for tags, `1.2.3` / `1.2`. This happens only if 1 and 2 passed. On
   `v*` tags, it also requires the tag to match `version:` in
   `addon/config.yaml`. The weekly run rebuilds `latest` on the newest
   `python:3.14-alpine` to pick up its security patches;
4. GHCR pruning: release tags, `latest` and `main` are kept forever, plus the
   10 newest other tagged images (`sha-…`). Untagged and broken images
   are deleted. Nothing younger than a day is touched. If this job
   fails with a permission error, give the repository the **Admin** role in the
   package's *Manage Actions access* settings.

Dependabot (`.github/dependabot.yml`) opens weekly PRs for pip packages and
GitHub Actions. Minor and patch updates are grouped; major ones come one at a
time.

To release: bump `version` in `addon/config.yaml`, commit, then
`git tag v0.1.1 && git push --tags`.
