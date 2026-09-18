#!/usr/bin/env bash
# Queue follow-up capacity runs after a running matrix finishes.
#   tests/load/rerun_after.sh <runner-pid> <run-dir>
# Always reruns C1, C2 (floor profile, placeholder raised to 0.8 GB) and C1s. If any
# standard-profile config (C3, C4, C4a) in <run-dir> had placeholder OOM-killed, raises the
# standard profile's placeholder to 0.8 GB and reruns those configs too.
set -euo pipefail
PID="$1"
RUN_DIR="$2"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

while kill -0 "$PID" 2>/dev/null; do sleep 30; done
echo "matrix ${PID} finished at $(date -Is)"

oom_configs="$(python3 - "$RUN_DIR" <<'EOF'
import json, sys
from pathlib import Path
hit = []
for cid in ("C3", "C4", "C4a"):
    f = Path(sys.argv[1]) / f"{cid}.json"
    if not f.exists():
        continue
    r = json.loads(f.read_text())
    steps = [r["runs"]["validate"], *r["runs"]["ramp"]]
    if any(s["health"].get("placeholder", {}).get("oom") for s in steps):
        hit.append(cid)
print(",".join(hit))
EOF
)"

configs="C1,C2,C1s"
if [[ -n "$oom_configs" ]]; then
  echo "placeholder OOM on standard profile in: ${oom_configs}; raising standard placeholder to 0.8 GB"
  sed -i 's/^MEM_STD = {"libpostal": 2.2, "interpolation": 2.3, "pip": 0.9, "placeholder": 0.5}/MEM_STD = {"libpostal": 2.2, "interpolation": 2.3, "pip": 0.9, "placeholder": 0.8}/' \
    tests/load/run_matrix.py
  grep -q '"placeholder": 0.8}$' <(grep '^MEM_STD' tests/load/run_matrix.py) \
    || { echo "failed to update MEM_STD" >&2; exit 1; }
  configs="${configs},${oom_configs}"
else
  echo "no placeholder OOM on the standard profile"
fi
echo "rerunning: ${configs}"
exec python3 tests/load/run_matrix.py --configs "$configs"
