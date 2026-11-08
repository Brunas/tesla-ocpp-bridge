# tesla-ocpp-bridge

Makes a Tesla Wall Connector Gen 3 (TWC3) report to an OCPP 1.6J backend:
CircleLink, `wss://ocpp.circlelink.app`. The user's real charger ID is only
in the git-ignored `.env` and in HA's Configuration tab. Never commit it,
since anyone who has it might post sessions to the user's account.
CircleLink is a Lithuanian app that collects home-charging data for a
"Private Charging benefit". It needs sessions and kWh, not control.

## Why it is a bridge, not a proxy

The TWC3 has **no OCPP at all**. It only has a read-only local HTTP API:
`/api/1/vitals`, `/api/1/lifetime`, `/api/1/version`. The bridge *is* the
charge point. It polls that API and speaks OCPP. Remote start/stop and
SetChargingProfile are rejected on purpose: the API cannot control charging.

## User's setup

- Home Assistant OS as a VM on TrueNAS SCALE. The HA Tesla Wall Connector
  integration works, so the TWC3 is reachable on the LAN.
- Asus router with Gnuton firmware. It could run things, but it was judged the
  weakest host.
- Deployment targets: the HA app (primary) or Docker on TrueNAS
  (`docker-compose.yml`).

## Decisions already made (don't re-litigate)

- **Wording**: HA renamed add-ons to *apps*. User-facing text says "app"
  (Lithuanian: "programėlė"; the menu is *Programėlės*). Paths and names on
  disk keep the old spelling (`addon/`, `/addons` = `local_apps`).
- **Self-contained `addon/`**: the code lives inside the add-on folder, so HA
  builds it locally and "copy `addon/` to `/addons/`" works without GHCR. The
  alternative (app pulling a prebuilt GHCR image, `src/` at the root) was
  considered and rejected.
- The package is split into modules (`config`, `state`, `wall_connector`,
  `charge_point`, `bridge`, `__main__`).
- Python 3.14 in the devcontainer and Docker image. CI tests 3.12–3.14.
- Sessions follow plug-in → unplug (`vehicle_connected`), not contactor state.
- `id_tag` keeps the default `TESLA` (not `null`/required in HA). The docs say
  to set it to the CircleLink station name.

## Commands

```sh
pip install -r requirements-dev.txt
python -m pytest            # 100 unit tests, ~2 s, no network
black .                     # format (CI runs black --check)
ruff check .
cd addon/app && python -m tesla_ocpp_bridge     # needs env vars, see CONTRIBUTING
python tools/fake_twc.py --port 8081 --speed 30 # simulators for manual runs
python tools/fake_csms.py --port 9000
docker build -t tesla-ocpp-bridge:smoke addon && tools/smoke_test.sh  # container e2e, ~10 s
```

CI (`.github/workflows/build.yml`) gates the GHCR push on tests, the smoke
test and, for `v*` tags, the `config.yaml` version. It also prunes old GHCR
images and rebuilds weekly. Dependabot covers pip and GitHub Actions only.
The Docker base image is left out on purpose, because Python 3.14 is a
decision.

## Configuration

- HA app: the `options`/`schema` in `addon/config.yaml`, with labels in
  `addon/translations/en.yaml` and `lt.yaml`. `charge_point_id` and `twc_host` default to
  `null`, which makes HA require them.
- Elsewhere: environment variables from `.env` (template `.env.example`).
  They override `/data/options.json`. CONTRIBUTING.md ("Adding a
  configuration option") lists the seven places a new option goes.

## Contributing / review

- `main` has a GitHub ruleset. Merging a PR needs 1 approving review, green CI
  (`Tests (Python 3.12/3.13/3.14)`, `Container smoke test`) and resolved
  conversations. Force pushes and deleting `main` are blocked.
- PRs are mandatory: nobody can push to `main` directly, admins included.
  Admins can bypass the rules only when merging a PR (bypass mode
  `pull_request`), because GitHub doesn't let authors approve their own PRs.
  Tags are not affected, so pushing release tags still works.
- Merged PR branches are deleted automatically (repo setting
  `delete_branch_on_merge`). Locally, `git fetch --prune` removes them.
- `.github/CODEOWNERS` requests a review from the owner. CI job names are
  required check names: if you rename a job in `build.yml`, update the ruleset
  too.

## AppArmor and HA updates

- `addon/apparmor.txt` confines the app on HA. It brings the security rating
  from 7 to 8: 5 base, +2 for no ports or host network, +1 for a custom
  profile. Image signing gives nothing, because Supervisor has no signing
  support. The profile was built from an strace of a full session. CI loads
  it on the Ubuntu runner, runs the smoke test under it and fails on any
  `apparmor="DENIED"` line. That can't be tested on macOS, because Docker
  Desktop has no AppArmor.
- Stop signals come from the container runtime. On Ubuntu 24.04+ (CI runs on
  `ubuntu-26.04`), runc and crun have their own AppArmor profiles, so the
  sender is `runc`, not `unconfined`. The profile therefore accepts signals
  from `unconfined`, `runc` and `crun`, like Docker's default profile.
  Without that, `docker stop` ends in SIGKILL (exit 137). The smoke test
  checks for a clean stop.
- Supervisor rules: exactly one `profile` line. Supervisor renames it to the
  app's full slug, so never reference the profile's own name. A new profile
  is only loaded on install or update.
- HA picks up changes only when `version` in `addon/config.yaml` increases.
  It builds locally from `addon/` and doesn't use tags or GHCR. Add a section
  to `addon/CHANGELOG.md` for every version.
- To test a branch on HA before merging, add the repository URL with
  `#<branch>` (Supervisor's `RE_REPOSITORY` allows letters, digits,
  `_ - . /`). Stop the main install while testing: two bridges with the same
  charger ID would fight over the OCPP connection.

## Invariants and gotchas

- The energy register (`state.register_wh`) must never decrease. It is
  `max(lifetime energy_wh, session_base + session_energy_wh - offset)`,
  because the TWC lifetime counter may lag during a session.
- `state.outbox` is delivered strictly FIFO. A StartTransaction answered with a
  CallError drops that transaction's later meter/stop messages; a timeout
  keeps everything queued for the next connection.
- ocpp library 2.1.0: `Action.boot_notification` (snake_case), `call.X` /
  `call_result.X` without a `Payload` suffix, snake_case kwargs in handlers.
- Never name a ChargePoint handler method `start` or `stop`: that overrides
  `ChargePoint.start()`. This bug once broke `tools/fake_csms.py`.
- `@after` handlers run via `ensure_future`. Tests must `wait_until` for
  their follow-up messages.
- Tests use `tests/helpers.Loopback`, an in-memory peer. All frames go
  through the real ocpp schema validation.
- Monkeypatch `MAX_QUEUED_METER_VALUES` / `TWC_OFFLINE_AFTER_S` / `TWC_WARN_AFTER_S` on
  `tesla_ocpp_bridge.bridge`, where they are used.

## Status / open items

- Tested only against the simulators and in Docker. **Never run against real
  CircleLink or a real TWC yet.** Do not point a simulator at real CircleLink:
  it would create fake sessions in the user's account.
- To verify on the first real run:
  1. Does CircleLink require a password (`ocpp_password`)?
  2. idTag must be the CircleLink station name (confirmed by the user).
     Check for `idTag status: Accepted`.
  3. Does the real TWC update `lifetime.energy_wh` live during a session? Also
     check the field names (`vehicle_connected`, `contactor_closed`,
     `session_energy_wh`, `voltageA_v`, `currentA_a`).
  4. Compare the session kWh with the Tesla app.
- Remote: `git@github.com:Brunas/tesla-ocpp-bridge.git` (public, MIT
  `LICENSE`). The GHCR image in `docker-compose.yml` is
  `ghcr.io/brunas/tesla-ocpp-bridge`.
