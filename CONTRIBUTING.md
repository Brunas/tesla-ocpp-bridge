# Contributing

Thanks for helping! This project makes a Tesla Wall Connector Gen 3 report
charging sessions to an OCPP 1.6J backend such as CircleLink. Bug reports,
fixes, translations and results from real hardware are all welcome.

## Scope

- **In scope:** reporting sessions, energy and status over OCPP 1.6J. Backends
  other than CircleLink. Running it as a Home Assistant app or on plain Docker.
- **Out of scope:** controlling the charger (remote start/stop, charging
  profiles). The Wall Connector's local API is read-only, so the bridge
  rejects those requests on purpose.

For anything bigger than a small fix, please open an issue first, so we can
agree on the approach before you write the code.

## Reporting a bug

Please include:

- how you run it (Home Assistant app or Docker) and the bridge version;
- the Wall Connector firmware (`http://<wall-connector-ip>/api/1/version`);
- the bridge log, ideally with `log_level: debug`.

**Remove your charger ID and any password from logs and screenshots before
posting them.** Anyone who has the charger ID may be able to send sessions to
your account.

## Development

### Setup

Use the dev container (VS Code → *Reopen in Container*, `.devcontainer/`,
Python 3.14), or a local Python 3.12+:

```sh
pip install -r requirements-dev.txt
```

[AGENTS.md](AGENTS.md) explains the design decisions and the invariants the
code relies on. Please read it before changing `bridge.py`.

### Layout

```
tesla-ocpp-bridge/
├── addon/                      # Home Assistant app = Docker build context
│   ├── config.yaml             #   app manifest (version, options schema)
│   ├── apparmor.txt            #   AppArmor profile (CI smoke test runs under it)
│   ├── CHANGELOG.md            #   shown in HA's update dialog
│   ├── translations/           #   Configuration tab labels (en, lt)
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

### Run against simulators

The simulators stand in for the Wall Connector and the OCPP backend:

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
python -m pytest     # unit tests: about 2 s, no network or charger needed
ruff check .         # lint
```

OCPP traffic goes through an in-memory peer (`tests/helpers.py`). Every frame
is still schema-validated by the `ocpp` library in both directions. Coverage:

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

### CI and images

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

## Ground rules

- **Never point a simulator at a real backend.** The fake Wall Connector
  combined with a real CircleLink account creates fake charging sessions in
  that account. Use `tools/fake_csms.py` instead.
- **Never commit personal data**: charger IDs, passwords, IP addresses or your
  `.env`. Tests use made-up IDs such as `TEST-CP-0001`.
- **Every behaviour change needs a unit test.** `tests/helpers.Loopback` is an
  in-memory OCPP peer. It runs every message through the real `ocpp` schema
  validation.
- Keep the energy register from ever decreasing, and deliver the outbox
  strictly in order. Many tests guard these two invariants. AGENTS.md
  explains why they matter.
- Code style is whatever `ruff check .` accepts (line length 100).
- On Home Assistant the app runs under the AppArmor profile
  `addon/apparmor.txt`. It may only read its code, read and write `/data`,
  and use the network. If your change needs other files, or starts another
  program, extend the profile. CI runs the smoke test under it and fails on
  any denial.

### Adding a configuration option

An option has to be added in seven places:

1. `DEFAULTS` in `addon/app/tesla_ocpp_bridge/config.py`
2. `options` (if it has a default) and `schema` in `addon/config.yaml`
3. `addon/translations/en.yaml` and `lt.yaml` (labels on the HA
   *Configuration* tab)
4. `.env.example`
5. `addon/DOCS.md`
6. the configuration table in [README.md](README.md#configuration)
7. a test in `tests/test_config.py`

### Translations

The labels on the Home Assistant *Configuration* tab live in
`addon/translations/<language>.yaml`. To add a language, copy `en.yaml` and
translate every `name` and `description`. Keep the keys unchanged. Home
Assistant now calls add-ons *apps*. Use the word your language's Home Assistant
interface uses (Lithuanian: *programėlė*).

## Pull requests

Every change goes through a pull request, including the maintainer's own.
Nobody can push to `main` directly.

1. Fork the repository and create a branch from `main`.
2. Keep each pull request to one topic, and update the docs if behaviour
   changes.
3. Fill in the pull request template: what changed, why, and how you tested
   it. Say whether you tried it on a real Wall Connector or backend, and which
   one. That is valuable, because CI only uses simulators.
4. CI must pass. It runs ruff and the tests on Python 3.12–3.14, and the
   container smoke test.
5. A pull request needs **one approving review** before it can be merged. The
   repository owner is asked for a review automatically (`.github/CODEOWNERS`).
   After the merge, GitHub deletes the pull request's branch automatically.

Dependabot pull requests go through the same checks and review.

## Releases (maintainers)

1. In a pull request, bump `version` in `addon/config.yaml` (e.g. `0.2.0`)
   and add a section for it at the top of `addon/CHANGELOG.md`. Home
   Assistant shows that section in its update dialog.
2. Merge it. Home Assistant offers the update as soon as the new version is on
   `main`: at its next store refresh, or right away with *Check for updates*.
   It builds the app locally from `addon/`, so it doesn't need the tag or the
   GHCR image.
3. Tag the merge commit on `main` and push the tag:
   `git tag v0.2.0 && git push origin v0.2.0`. CI checks that the tag matches
   `config.yaml` and publishes `ghcr.io/brunas/tesla-ocpp-bridge:0.2.0` for
   Docker users.

Changes merged without a version bump never reach Home Assistant: it only
compares versions.

## License

By contributing, you agree that your contributions are licensed under the
[MIT License](LICENSE), like the rest of the project.
