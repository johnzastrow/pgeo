#!/usr/bin/env bash
# pgeo-build: one command per thing an operator wants done.
#
#   fetch     download the open data for PGEO_BUILD into the /data cache
#   build     fetch if needed, then load, index and swap into the image's own database
#   dump      write /data/dumps/pgeo-<build>-<version>-<date>.dump from that database
#   push      rsync the newest dump to a host and swap it in there
#
# Scoring a build is not here: an accuracy run needs a front end, so it belongs against the
# serving bundle. See tests/accuracy/run_accuracy.py.
#   shell     a shell with the database running, for looking around
#
# The database lives in /data/pg and is started for the duration of a command. Nothing here
# assumes a previous run: `build` on an empty /data does the whole thing, and on a warm /data
# re-uses every download.
set -euo pipefail

ROOT=/opt/pgeo
export PGEO_DATA_DIR=${PGEO_DATA_DIR:-/data}
build=${PGEO_BUILD:-me}
PGUSER_NAME=pgeo
PGDB=pgeo
# Loopback TCP rather than the unix socket: ogr2ogr is handed a libpq keyword string built from
# this DSN, and a socket URL has no host for it to pass on.
export PGEO_DSN="postgresql://${PGUSER_NAME}@127.0.0.1:5432/${PGDB}"
export PGHOST=127.0.0.1

say() { printf '[%s] %s\n' "$(date +%H:%M:%S)" "$*"; }
die() { echo "pgeo-build: $*" >&2; exit 1; }

# --- the image's own PostgreSQL ---------------------------------------------------------------
# Started here rather than by the base image's entrypoint, because a build is a command that
# finishes, not a server that stays up.
pg_start() {
  mkdir -p "$PGDATA" /var/run/postgresql
  chown -R postgres:postgres "$PGDATA" /var/run/postgresql
  if [[ ! -s "$PGDATA/PG_VERSION" ]]; then
    say "first run: initialising the database in $PGDATA"
    su postgres -c "initdb --username=postgres --auth=trust --encoding=UTF8 --locale=C -D '$PGDATA'" >/dev/null
  fi
  # Written on every start, not only at initdb, so an existing cluster picks up a change to
  # these. postgresql.conf includes it; the include line is added once.
  cat >"$PGDATA/pgeo.conf" <<'CONF'
# A build is a bulk load into a database nobody is querying, thrown away if it fails, so this
# is tuned for throughput rather than durability.
listen_addresses = '127.0.0.1'
fsync = off
synchronous_commit = off
full_page_writes = off
shared_buffers = 1GB
work_mem = 256MB
maintenance_work_mem = 1GB
max_wal_size = 8GB
max_parallel_workers_per_gather = 4
max_parallel_maintenance_workers = 4
CONF
  grep -q "include 'pgeo.conf'" "$PGDATA/postgresql.conf" || echo "include 'pgeo.conf'" >>"$PGDATA/postgresql.conf"
  chown postgres:postgres "$PGDATA/pgeo.conf"
  su postgres -c "pg_ctl -D '$PGDATA' -w -t 120 -l '$PGDATA/start.log' start" >/dev/null \
    || { tail -20 "$PGDATA/start.log" >&2; die "the database did not start"; }
  su postgres -c "psql -tAc \"SELECT 1 FROM pg_roles WHERE rolname='${PGUSER_NAME}'\"" | grep -q 1 \
    || su postgres -c "createuser -s ${PGUSER_NAME}"
  su postgres -c "psql -tAc \"SELECT 1 FROM pg_database WHERE datname='${PGDB}'\"" | grep -q 1 \
    || su postgres -c "createdb -O ${PGUSER_NAME} ${PGDB}"
}
pg_stop() { su postgres -c "pg_ctl -D '$PGDATA' -w -t 120 -m fast stop" >/dev/null 2>&1 || true; }
trap pg_stop EXIT

# --- commands ----------------------------------------------------------------------------------
cmd_fetch() {
  say "fetching sources for build '${build}' into ${PGEO_DATA_DIR} (cached between runs)"
  ( cd "$ROOT" && bash scripts/fetch_data.sh --build "$build" all )
}

# The loader creates a read-only role for the query side and wants its password in
# pgeo/pgeo.secrets. In this image the database is scratch - built, dumped and thrown away - and
# the role's password is set again on the serving host at restore time, so one is generated here
# rather than asked for. An operator who wants to pin it can mount their own secrets file.
ensure_secrets() {
  local f="${ROOT}/pgeo/pgeo.secrets"
  [[ -s "$f" ]] && return
  say "generating a scratch pgeo.secrets (this database is built, dumped and discarded)"
  { printf 'PGEO_DB_PASSWORD=%s\n' "$(tr -dc A-Za-z0-9 </dev/urandom | head -c 32)"
    printf 'PGEO_API_PASSWORD=%s\n' "$(tr -dc A-Za-z0-9 </dev/urandom | head -c 32)"
  } >"$f"
  chmod 600 "$f"
}

cmd_build() {
  [[ -d "${PGEO_DATA_DIR}/raw/$(tr ',' '-' <<<"$build")" ]] || cmd_fetch
  ensure_secrets
  pg_start
  say "preparing GNIS, ZCTA and Overture"
  ( cd "$ROOT" && uv run --project prep --no-dev pelias-prep all --build "$build" )
  say "loading, indexing and swapping"
  ( cd "$ROOT" && uv run --project pgeo --no-dev pgeo-load build --build "$build" )
  say "built"
}

cmd_dump() {
  pg_start
  local ver name out
  # an unbuilt database has no geocode schema, so this is allowed to fail and be reported
  ver=$(psql -U "$PGUSER_NAME" -d "$PGDB" -Atc 'SELECT geocode.engine_version()' 2>/dev/null || true)
  [[ "$ver" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "the database reports engine version '${ver}'; build first"
  out="${PGEO_DATA_DIR}/dumps"; mkdir -p "$out"
  name="pgeo-$(tr ',' '-' <<<"$build")-${ver}-$(date +%Y%m%d-%H%M)"
  # feature_ac is excluded and rebuilt on the far side: its rows must sit in the same physical
  # order as pgeo.feature, and a dump does not preserve that. See scripts/pgeo_dump.sh.
  pg_dump -U "$PGUSER_NAME" -d "$PGDB" -Fc -Z 6 -n pgeo -n geocode -n geocode_api \
    --exclude-table=pgeo.feature_ac > "${out}/${name}.dump.part"
  mv "${out}/${name}.dump.part" "${out}/${name}.dump"
  ( cd "$out" && sha256sum "${name}.dump" > "${name}.dump.sha256" )
  say "wrote ${out}/${name}.dump ($(du -h "${out}/${name}.dump" | cut -f1)) and its .sha256"
}

cmd_push() {
  local host=${1:?usage: pgeo-build push user@host [remote-dir]}
  local remote=${2:-/srv/pgeo/incoming}
  local newest
  newest=$(ls -1t "${PGEO_DATA_DIR}"/dumps/*.dump 2>/dev/null | head -1) \
    || die "no dump in ${PGEO_DATA_DIR}/dumps; run 'dump' first"
  [[ -n "$newest" ]] || die "no dump in ${PGEO_DATA_DIR}/dumps; run 'dump' first"
  say "sending $(basename "$newest") to ${host}:${remote}"
  # rsync so an interrupted push resumes; the checksum goes with it and the far side checks it.
  rsync -aP --partial "$newest" "${newest}.sha256" "${host}:${remote}/"
  say "swapping on ${host}"
  ssh "$host" "pgeo-swap ${remote}/$(basename "$newest")"
  say "done"
}

case "${1:-help}" in
  fetch)    shift; cmd_fetch "$@" ;;
  build)    shift; cmd_build "$@" ;;
  dump)     shift; cmd_dump "$@" ;;
  push)     shift; cmd_push "$@" ;;
  shell)    pg_start; exec bash ;;
  help|-h|--help) sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//' ;;
  *) die "unknown command '$1' (try: help)" ;;
esac
