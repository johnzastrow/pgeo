#!/usr/bin/env bash
# Start (or restart) Photon on a chosen set of cores, so it can be measured on the same footing
# as the container-based engines.
#
#   tests/load/photon_serve.sh            all cores, matching the M0 / PM unconstrained tier
#   tests/load/photon_serve.sh 0,1        two cores, matching Pelias C2 and pgeo P2
#   tests/load/photon_serve.sh 0,1,2,3    four cores, matching C4 and P4
#
# k6 runs on cores 5 and 11 (tests/load/run_matrix.py K6_CPUS), so the engine must avoid those
# for a pinned comparison; all-cores runs contend with the load generator exactly as the
# unconstrained configurations of the other engines do.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CORES="${1:-}"
PORT="${PHOTON_PORT:-2322}"
cd "$ROOT"

[[ -d data/photon-app/photon_data ]] || {
  echo "no Photon index at data/photon-app/photon_data - build it from Nominatim first" >&2
  exit 1
}

pkill -f "photon.jar -data-dir" 2>/dev/null || true
sleep 2

pin=()
[[ -n "$CORES" ]] && pin=(taskset -c "$CORES")
echo "== photon on ${CORES:-all cores}, port $PORT"
nohup "${pin[@]}" java -jar data/photon-app/photon.jar \
  -data-dir data/photon-app -listen-port "$PORT" -listen-ip 127.0.0.1 \
  > data/logs/photon_server.log 2>&1 &

for _ in $(seq 1 40); do
  if curl -sf -o /dev/null --max-time 2 "http://127.0.0.1:$PORT/api?q=bangor&limit=1"; then
    echo "== ready"
    exit 0
  fi
  sleep 3
done
echo "== photon did not come up; see data/logs/photon_server.log" >&2
exit 1
