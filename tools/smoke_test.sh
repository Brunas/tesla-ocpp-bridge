#!/usr/bin/env bash
# End-to-end smoke test of a built bridge image against the simulators in tools/.
#
# Runs fake_csms, fake_twc and the bridge as containers on a private Docker
# network (the image already has the simulators' dependencies), then drives one
# session: boot -> plug in + charge -> meter values -> unplug -> stop, and
# checks that the bridge exits cleanly on SIGTERM.
# Needs only Docker. Never talks to the real CircleLink or a real Wall Connector.
#
#   docker build -t tesla-ocpp-bridge:smoke addon && tools/smoke_test.sh
#
# SMOKE_APPARMOR=<profile> runs the bridge under that (already loaded) AppArmor
# profile, as CI does with addon/apparmor.txt. Linux only.
set -euo pipefail

IMAGE=${1:-tesla-ocpp-bridge:smoke}
TIMEOUT=${SMOKE_TIMEOUT:-30} # seconds allowed for each step
TOOLS=$(cd "$(dirname "$0")" && pwd)
P=tob-smoke-$$
NET=$P-net
CSMS=$P-csms
TWC=$P-twc
BRIDGE=$P-bridge

cleanup() {
  local status=$?
  if [ "$status" -ne 0 ]; then
    for c in "$CSMS" "$TWC" "$BRIDGE"; do
      echo "----- last log lines of ${c#"$P"-}"
      docker logs --tail 40 "$c" 2>&1 || true
    done
  fi
  docker rm -fv "$CSMS" "$TWC" "$BRIDGE" >/dev/null 2>&1 || true
  docker network rm "$NET" >/dev/null 2>&1 || true
  exit "$status"
}
trap cleanup EXIT

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

# wait_for <container> <extended regex> <description>
wait_for() {
  local deadline=$((SECONDS + TIMEOUT)) logs
  while :; do
    logs=$(docker logs "$1" 2>&1)
    grep -qE "$2" <<<"$logs" && break
    [ "$(docker inspect -f '{{.State.Running}}' "$BRIDGE" 2>/dev/null || echo true)" = true ] ||
      fail "bridge container exited while waiting for: $3"
    ((SECONDS < deadline)) || fail "no /$2/ in ${1#"$P"-} logs after ${TIMEOUT}s ($3)"
    sleep 1
  done
  echo "ok: $3"
}

# wait_port <container> <port>: until something listens on it inside the container
wait_port() {
  local deadline=$((SECONDS + TIMEOUT))
  until docker exec "$1" python -c \
    'import socket, sys; socket.create_connection(("127.0.0.1", int(sys.argv[1])), 1)' "$2" \
    2>/dev/null; do
    ((SECONDS < deadline)) || fail "${1#"$P"-} not listening on port $2 after ${TIMEOUT}s"
    sleep 1
  done
}

# twc <query>: drive the fake Wall Connector, e.g. twc 'plug=1&charge=1'
twc() {
  docker exec "$TWC" python -c \
    'import sys, urllib.request; urllib.request.urlopen(sys.argv[1]).read()' \
    "http://127.0.0.1:8081/control?$1"
}

docker network create "$NET" >/dev/null
docker run -d --name "$CSMS" --network "$NET" -v "$TOOLS:/tools:ro" "$IMAGE" \
  python /tools/fake_csms.py --port 9000 >/dev/null
docker run -d --name "$TWC" --network "$NET" -v "$TOOLS:/tools:ro" "$IMAGE" \
  python /tools/fake_twc.py --port 8081 --speed 60 >/dev/null
wait_port "$CSMS" 9000
wait_port "$TWC" 8081
echo "ok: simulators up"

security=()
if [ -n "${SMOKE_APPARMOR:-}" ]; then
  security=(--security-opt "apparmor=$SMOKE_APPARMOR")
fi
# ${a[@]+...}: bash 3.2 (macOS) treats an empty array as unset under set -u.
docker run -d --name "$BRIDGE" --network "$NET" ${security[@]+"${security[@]}"} \
  -e OCPP_URL="ws://$CSMS:9000" \
  -e CHARGE_POINT_ID=SMOKE-TEST \
  -e TWC_HOST="$TWC:8081" \
  -e POLL_INTERVAL=1 \
  -e METER_VALUE_INTERVAL=2 \
  -e LOG_LEVEL=debug \
  "$IMAGE" >/dev/null
if [ -n "${SMOKE_APPARMOR:-}" ]; then
  profile=$(docker inspect -f '{{.AppArmorProfile}}' "$BRIDGE")
  [ "$profile" = "$SMOKE_APPARMOR" ] || fail "bridge runs under AppArmor '$profile', not '$SMOKE_APPARMOR'"
  echo "ok: bridge confined by AppArmor profile $profile"
fi

wait_for "$CSMS" "BootNotification" "BootNotification reached the CSMS"
twc 'plug=1&charge=1'
wait_for "$CSMS" "StartTransaction" "StartTransaction on plug-in"
wait_for "$BRIDGE" "idTag status: Accepted" "bridge got a transaction id"
wait_for "$CSMS" "MeterValues tx=[0-9]+" "MeterValues during the session"
twc 'plug=0'
wait_for "$CSMS" "StopTransaction" "StopTransaction on unplug"

# Home Assistant and Docker stop the app with SIGTERM and wait 10 s before
# SIGKILL. The bridge must exit cleanly; under AppArmor this also checks that
# it may receive the signal.
stop_start=$SECONDS
docker stop -t 10 "$BRIDGE" >/dev/null
exit_code=$(docker inspect -f '{{.State.ExitCode}}' "$BRIDGE")
[ "$exit_code" = 0 ] || fail "bridge exited with $exit_code after docker stop (SIGTERM not handled?)"
((SECONDS - stop_start < 10)) || fail "bridge took $((SECONDS - stop_start)) s to stop"
bridge_log=$(docker logs "$BRIDGE" 2>&1)
grep -q "Stopped" <<<"$bridge_log" || fail "no 'Stopped' in bridge log after docker stop"
echo "ok: clean stop on SIGTERM"

if grep -q Traceback <<<"$bridge_log"; then
  fail "traceback in bridge log"
fi
echo "PASS: smoke test of $IMAGE"
