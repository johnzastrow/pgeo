#!/usr/bin/env bash
# Rebuild everything from nothing: data, Pelias, pgeo, verification, report; optionally deploy.
# This is the final iteration of both platforms. Runbook with every step explained: docs/REBUILD.md.
#
#   scripts/rebuild_all.sh                      # check data pelias pgeo verify report
#   scripts/rebuild_all.sh pgeo verify          # selected stages, in the order given
#   scripts/rebuild_all.sh deploy               # ship to the query host (explicit only; see below)
#
# Stages
#   check   required tools are installed
#   data    pelias CLI (pinned), raw inputs, prepared CSVs (GNIS, ZCTA, Overture)
#   pelias  Pelias download + prepare + import + start + tests, then an Elasticsearch snapshot
#   pgeo    pgeo first-time setup, then build + tuning profile + known answers + accuracy gate
#           (pgeo fetches its own OpenAddresses and Who's on First; see GETTING_STARTED.md)
#   verify  API compatibility contract and browser smoke test of the demo page
#   report  docs/REPORT.md and docs/REPORT.pdf from the saved results
#   deploy  pgeo dump, then Ansible to the query host with the newest snapshot and dump
#           (VM 120 must have 14 GB: docs/DEPLOY_PGEO.md)
# Load tests are not a stage: they take hours (docs/REBUILD.md, section 7).
#
# This script rebuilds the Maine build, because that is what the report measures and what the
# `pelias` stage needs. To build another region, follow GETTING_STARTED.md: the same stages with
# --build, and without Pelias, which is not part of a pgeo deployment.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

stages=("$@")
[[ ${#stages[@]} -gt 0 ]] || stages=(check data pelias pgeo verify report)
say() { printf '\n==== %s ====\n' "$*"; }

stage_check() {
  local missing=()
  for t in docker uv git curl python3 ansible-playbook; do command -v "$t" >/dev/null || missing+=("$t"); done
  docker compose version >/dev/null 2>&1 || missing+=("docker compose v2")
  if [[ ${#missing[@]} -gt 0 ]]; then echo "missing tools: ${missing[*]} (docs/REBUILD.md, section 1)" >&2; exit 1; fi
  command -v pandoc >/dev/null && command -v xelatex >/dev/null || echo "note: pandoc/xelatex missing: the report stage will build Markdown only"
  echo "tools ok"
}

stage_data() {
  scripts/bootstrap.sh
  scripts/fetch_data.sh all
  (cd prep && uv run python -m pytest -q && uv run pelias-prep all)
}

stage_pelias() {
  if [[ "$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:9200/pelias)" == "200" ]]; then
    echo "the local 'pelias' index exists; drop it first to rebuild: (cd projects/pelias_maine && ../../vendor/pelias-docker/pelias elastic drop)" >&2
    exit 1
  fi
  scripts/build_local.sh setup download prepare import up test
  scripts/snapshot_local.sh
}

stage_pgeo() {
  for f in data/pelias/whosonfirst/sqlite data/pelias/openstreetmap/maine-latest.osm.pbf data/pelias/interpolation_oa/us/me \
           data/processed/csv/gnis.csv; do
    [[ -e "$f" ]] || { echo "missing $f: run the data and pelias stages first (or scripts/build_local.sh setup download)" >&2; exit 1; }
  done
  scripts/pgeo_setup.sh --profile workstation
  scripts/pgeo_rebuild.sh --profile workstation -f pgeo/compose.pinned.yml
}

stage_verify() {
  uv run --project pgeo python tests/compat/compat_test.py --json report/data/compat.json
  (cd pgeo && uv run python -m pytest -q)
  uv run --project report --with pytest pytest report/tests -q     # the report renderer
  uv run --project pgeo python -m pytest tests/security -q                   # posture of the local stack
  if ! curl -sf -o /dev/null http://127.0.0.1:8088/; then
    (nohup scripts/dev_web.sh > data/logs/dev_web.log 2>&1 &)
    sleep 3
  fi
  python3 tests/web/smoke_demo.py
}

stage_report() {
  scripts/build_report.sh --refresh
}

stage_deploy() {
  scripts/pgeo_dump.sh
  local dump snap
  dump="$(ls -t data/pgeo/dumps/*.dump | head -1 | xargs basename | sed 's/\.dump$//')"
  snap="$(curl -s 'http://127.0.0.1:9200/_snapshot/pelias_repo/_all' | python3 -c \
    'import json,sys; s=json.load(sys.stdin)["snapshots"]; print(sorted(x["snapshot"] for x in s)[-1])')"
  echo "deploying snapshot $snap and dump $dump"
  (cd infra/ansible && ansible-playbook site.yml -e "pelias_snapshot_name=$snap" -e "pgeo_dump_name=$dump" \
     -e pelias_force_restore=true -e pgeo_force_restore=true)
  site="${GEOCODER_URL:-https://geocoder.example.org}"
  python3 tests/web/smoke_demo.py "$site"
  # the deployed posture, including the host checks that only apply to a real deployment
  PGEO_EDGE="$site/pgeo" PELIAS_EDGE="$site" \
    PGEO_VM_SSH="${PGEO_VM_SSH:-}" uv run --project pgeo python -m pytest tests/security -q
}

for s in "${stages[@]}"; do
  case "$s" in
    check|data|pelias|pgeo|verify|report|deploy) say "$s"; "stage_$s" ;;
    -h|--help) sed -n '2,22p' "$0"; exit 0 ;;
    *) echo "unknown stage: $s (see --help)" >&2; exit 2 ;;
  esac
done
say "done: ${stages[*]}"
