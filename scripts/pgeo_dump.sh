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
docker exec pgeo_db pg_dump -U pgeo -d pgeo -Fc -Z 6 -n pgeo -n geocode -n geocode_api > "$OUT/$name.dump.part"
mv "$OUT/$name.dump.part" "$OUT/$name.dump"
echo "wrote $OUT/$name.dump ($(du -h "$OUT/$name.dump" | cut -f1))"
echo "deploy: cd infra/ansible && ansible-playbook site.yml -e pgeo_dump_name=$name"
