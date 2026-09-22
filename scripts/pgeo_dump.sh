#!/usr/bin/env bash
# Dump the built pgeo database for deployment (restored on the query host by the
# pgeo_runtime Ansible role). Only the geocoder's schemas: pgeo (data), geocode (functions,
# reference tables), geocode_api (HTTP API). Extensions and roles are created on the host.
#
#   scripts/pgeo_dump.sh            -> data/pgeo/dumps/pgeo-<engine version>-<date>.dump
# Then deploy:  cd infra/ansible && ansible-playbook site.yml -e pgeo_dump_name=<name>
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="$ROOT/data/pgeo/dumps"
mkdir -p "$OUT"
ver="$(docker exec pgeo_db psql -U pgeo -d pgeo -Atc 'SELECT geocode.engine_version()')"
[[ "$ver" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "unexpected engine version: $ver" >&2; exit 1; }
name="pgeo-${ver}-$(date +%Y%m%d-%H%M)"
# pgeo.feature_ac is left out on purpose. It is a narrow copy of pgeo.feature whose rows must sit
# in the same physical order as their source, because ties between equal candidates are broken
# by that order. A dump and restore of two tables does not keep their relative order (177 of
# 203,399 rows landed elsewhere on VM 120, and 6 of 276 keystrokes then answered differently
# on the fast and slow routes). So the restore rebuilds it from the restored feature table.
docker exec pgeo_db pg_dump -U pgeo -d pgeo -Fc -Z 6 -n pgeo -n geocode -n geocode_api \
  --exclude-table=pgeo.feature_ac > "$OUT/$name.dump.part"
mv "$OUT/$name.dump.part" "$OUT/$name.dump"
echo "wrote $OUT/$name.dump ($(du -h "$OUT/$name.dump" | cut -f1))"
echo "deploy: cd infra/ansible && ansible-playbook site.yml -e pgeo_dump_name=$name"
