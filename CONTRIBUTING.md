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

## Development setup

Use the dev container (VS Code → *Reopen in Container*, Python 3.14), or a
local Python 3.12+:

```sh
pip install -r requirements-dev.txt
python -m pytest     # unit tests: about 1.5 s, no network or charger needed
ruff check .         # lint
```

For a manual run, the simulators stand in for the Wall Connector and the
backend. See *Run against simulators* in the [README](README.md#run-against-simulators).
To test the Docker image end to end (needs only Docker):

```sh
docker build -t tesla-ocpp-bridge:smoke addon && tools/smoke_test.sh
```

[AGENTS.md](AGENTS.md) explains the design decisions and the invariants the
code relies on. Please read it before changing `bridge.py`.

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

### Adding a configuration option

An option has to be added in seven places:

1. `DEFAULTS` in `addon/app/tesla_ocpp_bridge/config.py`
2. `options` (if it has a default) and `schema` in `addon/config.yaml`
3. `addon/translations/en.yaml` and `lt.yaml` (labels on the HA
   *Configuration* tab)
4. `.env.example`
5. `addon/DOCS.md`
6. the configuration table in `README.md`
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

Dependabot opens weekly pull requests for dependency updates. They go through
the same checks and review.

## Releases (maintainers)

1. In a pull request, bump `version` in `addon/config.yaml` (e.g. `0.2.0`).
2. After merging, tag that commit on `main` and push the tag:
   `git tag v0.2.0 && git push origin v0.2.0`.
3. CI checks that the tag matches `config.yaml` and publishes
   `ghcr.io/brunas/tesla-ocpp-bridge:0.2.0`. Home Assistant offers the update
   to app users.

## License

By contributing, you agree that your contributions are licensed under the
[MIT License](LICENSE), like the rest of the project.
