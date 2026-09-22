#!/usr/bin/env bash
# Dump the built pgeo database for deployment (restored on the query host by the
# pgeo_runtime Ansible role). Only the geocoder's schemas: pgeo (data), geocode (functions,
# reference tables), geocode_api (HTTP API). Extensions and roles are created on the host.
#
#   scripts/pgeo_dump.sh [--build NAME]
#       -> data/pgeo[-<build>]/dumps/pgeo-[<build>-]<engine version>-<date>.dump
# Then deploy:  cd infra/ansible && ansible-playbook site.yml -e pgeo_dump_name=<name>
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

build=me
while [[ $# -gt 0 ]]; do
  case "$1" in
    --build) build="${2:?--build needs a name}"; shift 2 ;;
    -h|--help) sed -n '2,8p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
# A build given as a state list ("me,nh,vt") names a directory, a database and containers, so
# it takes the same dashed form the rest of the pipeline uses.
build="$(tr ',' '-' <<<"${build,,}")"

# The default build keeps the original container, database and paths.
container=pgeo_db; database=pgeo; OUT="$ROOT/data/pgeo/dumps"; tag=""
if [[ "$build" != "me" ]]; then
  container="pgeo-${build}_db"
  OUT="$ROOT/data/pgeo-${build}/dumps"; tag="${build}-"
  # The database name is whatever scripts/pgeo_setup.sh recorded: it is an SQL identifier, so
  # "vt-nh" becomes "vt_nh". Recomputing it here once produced a name nothing could connect to.
  env_file="$ROOT/pgeo/builds/${build}.env"
  [[ -f "$env_file" ]] || { echo "no $env_file; run scripts/pgeo_setup.sh --build $build" >&2; exit 1; }
  database="$(sed -n 's/^PGEO_DB_NAME=//p' "$env_file")"
fi
mkdir -p "$OUT"
ver="$(docker exec "$container" psql -U pgeo -d "$database" -Atc 'SELECT geocode.engine_version()')"
[[ "$ver" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "unexpected engine version: $ver" >&2; exit 1; }
name="pgeo-${tag}${ver}-$(date +%Y%m%d-%H%M)"
# pgeo.feature_ac is left out on purpose. It is a narrow copy of pgeo.feature whose rows must sit
# in the same physical order as their source, because ties between equal candidates are broken
# by that order. A dump and restore of two tables does not keep their relative order (177 of
# 203,399 rows landed elsewhere on VM 120, and 6 of 276 keystrokes then answered differently
# on the fast and slow routes). So the restore rebuilds it from the restored feature table.
docker exec "$container" pg_dump -U pgeo -d "$database" -Fc -Z 6 -n pgeo -n geocode -n geocode_api \
  --exclude-table=pgeo.feature_ac > "$OUT/$name.dump.part"
mv "$OUT/$name.dump.part" "$OUT/$name.dump"
echo "wrote $OUT/$name.dump ($(du -h "$OUT/$name.dump" | cut -f1))"
echo "deploy: cd infra/ansible && ansible-playbook site.yml -e pgeo_dump_name=$name"
