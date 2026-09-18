#!/usr/bin/env bash
# Snapshot the local `pelias` Elasticsearch index into DATA_DIR/es_snapshots so Ansible can
# ship it to the VM / VPS (role pelias_runtime restores it by name).
#
#   scripts/snapshot_local.sh            # snapshot named pelias-YYYYMMDD-HHMM
#   scripts/snapshot_local.sh my-name
set -euo pipefail

ES="http://127.0.0.1:9200"
NAME="${1:-pelias-$(date +%Y%m%d-%H%M)}"
[[ "$NAME" =~ ^[a-z0-9][a-z0-9._-]{0,99}$ ]] || { echo "invalid snapshot name: ${NAME}" >&2; exit 1; }

curl -fsS -X PUT "${ES}/_snapshot/pelias_repo" -H 'Content-Type: application/json' \
  -d '{"type":"fs","settings":{"location":"/usr/share/elasticsearch/snapshots","compress":true}}' >/dev/null

# Make sure all writes are visible and segments are merged before snapshotting.
curl -fsS -X POST "${ES}/pelias/_refresh" >/dev/null
curl -fsS -X POST "${ES}/pelias/_forcemerge?max_num_segments=1" >/dev/null

result="$(curl -fsS -X PUT "${ES}/_snapshot/pelias_repo/${NAME}?wait_for_completion=true" \
  -H 'Content-Type: application/json' -d '{"indices":"pelias","include_global_state":false}')"
state="$(jq -r '.snapshot.state' <<<"$result")"
[[ "$state" == "SUCCESS" ]] || { echo "snapshot ${NAME} failed: ${result}" >&2; exit 1; }
echo "snapshot ${NAME}: ${state}; deploy with -e pelias_snapshot_name=${NAME}"
