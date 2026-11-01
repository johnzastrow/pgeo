#!/usr/bin/env bash
# Bring a new dump into a running pgeo deployment, with the service answering throughout all
# but the switch itself.
#
#   scripts/pgeo_swap.sh --dump data/pgeo/dumps/pgeo-0.19.1-20260925-0825.dump [--build me]
#                        [--keep-previous] [--no-checksum]
#
# The dump's .sha256 must sit beside it (pgeo_dump.sh writes one); --no-checksum skips that check.
#
# The expensive part - restore, the autocomplete side table, vacuum, and the checks - happens in
# a second database while the current one keeps serving. Only the final switch interrupts
# anything, and that is a database rename: connections are dropped, two renames commit, PostgREST
# reconnects on its own. A second or so, against the several minutes a straight pg_restore into
# the live database takes with the schemas dropped.
#
# Why a database and not a schema. The original sketch (docs/DOCKER_IMAGES.md) had the dump
# restored into a `pgeo_incoming` schema and renamed into place, which is what a *build* does
# locally. A dump cannot do it: pg_dump writes its schema names into the archive and pg_restore
# has no way to rewrite them - `-n` selects a schema, it does not rename one. Renaming the live
# schemas out of the way first would leave the service without them for the whole restore, which
# is the outage this exists to avoid. So the unit of swap is the database.
#
# Safe to re-run: the staging database is recreated each time, and nothing touches the live one
# until every check has passed.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
build=me
dump=""
keep_previous=0
verify_checksum=1

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dump)  dump="${2:?--dump needs a path}"; shift 2 ;;
    --build) build="${2:?--build needs a name}"; shift 2 ;;
    --keep-previous) keep_previous=1; shift ;;
    --no-checksum) verify_checksum=0; shift ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
[[ -n "$dump" ]] || { echo "need --dump <file>" >&2; exit 2; }
[[ -f "$dump" ]] || { echo "no such dump: $dump" >&2; exit 1; }

build="$(tr ',' '-' <<<"${build,,}")"
# PGEO_STACK prefixes the containers of a serving bundle (deploy/compose.yml), so that a bundle
# can run on a machine that already has this repository's development stack. It defaults to the
# development stack's own name, which is what a run from a checkout wants.
container="${PGEO_STACK:-pgeo}_db"; database=pgeo
if [[ "$build" != "me" ]]; then
  container="pgeo-${build}_db"
  env_file="${ROOT}/pgeo/builds/${build}.env"
  [[ -f "$env_file" ]] || { echo "no ${env_file}; run scripts/pgeo_setup.sh --build ${build}" >&2; exit 1; }
  database="$(sed -n 's/^PGEO_DB_NAME=//p' "$env_file")"
fi
staging="${database}_incoming"
previous="${database}_previous"

say() { printf '[%s] %s\n' "$(date +%H:%M:%S)" "$*"; }
psql_db() { docker exec -i "$container" psql -v ON_ERROR_STOP=1 -U pgeo -d "$1" "${@:2}"; }

docker inspect "$container" >/dev/null 2>&1 || { echo "container ${container} is not running" >&2; exit 1; }

# The dump must arrive with its checksum, and match it. pg_restore runs as the database
# superuser, so a truncated or altered dump is not something to restore and find out about.
# Both pgeo_dump.sh and the build image's push write the .sha256; --no-checksum is the explicit
# way round it for a dump made some other way.
if [[ "$verify_checksum" == 1 ]]; then
  [[ -f "${dump}.sha256" ]] || {
    echo "no ${dump}.sha256 beside the dump; copy it too, or pass --no-checksum to skip the check" >&2
    exit 1
  }
  say "checking ${dump##*/}.sha256"
  (cd "$(dirname "$dump")" && sha256sum -c "$(basename "$dump").sha256" >/dev/null) \
    || { echo "checksum mismatch; refusing to swap" >&2; exit 1; }
else
  say "WARNING: --no-checksum given; restoring ${dump##*/} without verifying it"
fi

say "staging database ${staging}"
psql_db postgres -c "DROP DATABASE IF EXISTS ${staging} WITH (FORCE)" >/dev/null
psql_db postgres -c "CREATE DATABASE ${staging}" >/dev/null

# The dump grants to pgeo_api and assumes the extensions exist, exactly as the Ansible restore
# does. The role is cluster-wide, so it already exists on a host that has served once.
psql_db "$staging" >/dev/null <<'SQL'
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS unaccent;
CREATE EXTENSION IF NOT EXISTS fuzzystrmatch;
SQL

# The API role's password, statement timeout and request limits. A dump carries the grants to
# pgeo_api but none of these - the role is cluster-wide, so no dump could - and a change to them
# in the deployment's settings should take effect on the next swap. The same api-role.psql runs
# on a bundle's first boot (deploy/db/init/10-api-role.sh); its header lists the variables.
#
# Each value comes from this shell's environment, else from the deployment's settings file: .env
# beside this script on a serving bundle, pgeo/pgeo.secrets in a checkout.
role_sql="$(dirname "${BASH_SOURCE[0]}")/db/init/api-role.psql"           # serving bundle
[[ -f "$role_sql" ]] || role_sql="${ROOT}/deploy/db/init/api-role.psql"    # checkout
settings_file="$(dirname "${BASH_SOURCE[0]}")/.env"
[[ -f "$settings_file" ]] || settings_file="${ROOT}/pgeo/pgeo.secrets"
role_vars=(PGEO_API_PASSWORD PGEO_API_STATEMENT_TIMEOUT
           PGEO_MAX_TEXT PGEO_MAX_SIZE PGEO_MAX_CIRCLE_KM PGEO_MAX_REVERSE_KM PGEO_MAX_ADDRESS_KM)
exec_env=()
for v in "${role_vars[@]}"; do
  if [[ -z "${!v+x}" && -f "$settings_file" ]] && grep -q "^${v}=" "$settings_file"; then
    export "$v=$(sed -n "s/^${v}=//p" "$settings_file" | tail -1)"
  fi
  # -e NAME with no value copies it from this environment: no value ever goes on a command line,
  # where ps would show the password to every local user.
  [[ -n "${!v+x}" ]] && { export "$v"; exec_env+=(-e "$v"); }
done
say "applying the pgeo_api role settings (password, statement timeout, request limits)"
docker exec -i "${exec_env[@]}" "$container" psql -U pgeo -d postgres -q >/dev/null < "$role_sql"
psql_db postgres -c "GRANT CONNECT ON DATABASE ${staging} TO pgeo_api" >/dev/null

say "restoring (the live database is still answering)"
docker exec -i "$container" pg_restore -U pgeo -d "$staging" --no-owner --exit-on-error < "$dump"

# feature_ac is excluded from the dump on purpose and rebuilt here: its rows have to sit in the
# same physical order as pgeo.feature, because ties between equal candidates are broken by that
# order and the two front ends must agree. See scripts/pgeo_dump.sh.
say "rebuilding the autocomplete side table in this host's physical order"
psql_db "$staging" >/dev/null <<'SQL'
BEGIN;
SET LOCAL max_parallel_workers_per_gather = 0;
DROP TABLE IF EXISTS pgeo.feature_ac;
CREATE TABLE pgeo.feature_ac AS
  SELECT id, importance, geom, layer, label, name_norm, tokens
  FROM pgeo.feature WHERE layer <> 'address' ORDER BY ctid;
ALTER TABLE pgeo.feature_ac ADD PRIMARY KEY (id);
CREATE INDEX feature_ac_tokens_idx ON pgeo.feature_ac USING gin (tokens);
CREATE INDEX feature_ac_name_trgm_idx ON pgeo.feature_ac USING gin (name_norm gin_trgm_ops);
GRANT SELECT ON pgeo.feature_ac TO pgeo_api;
COMMIT;
SQL

say "vacuum analyze (a restored table has no visibility map or statistics)"
docker exec "$container" psql -U pgeo -d "$staging" -c "VACUUM (ANALYZE)" >/dev/null

# Everything above can fail without touching what is being served. These checks are the last
# point at which that is still true, so they are the ones worth having.
say "checking the staged database before anything is switched"
ver="$(docker exec "$container" psql -U pgeo -d "$staging" -Atc 'SELECT geocode.engine_version()')"
[[ "$ver" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "staged database reports engine version '${ver}'" >&2; exit 1; }
n="$(docker exec "$container" psql -U pgeo -d "$staging" -Atc 'SELECT count(*) FROM pgeo.feature')"
(( n > 0 )) || { echo "staged database holds no features" >&2; exit 1; }
hits="$(docker exec "$container" psql -U pgeo -d "$staging" -Atc \
  "SELECT count(*) FROM geocode.search('main st', NULL, NULL, NULL, NULL, NULL)")"
(( hits > 0 )) || { echo "staged database answers nothing for a query that should match" >&2; exit 1; }
has_limits="$(docker exec "$container" psql -U pgeo -d "$staging" -Atc \
  "SELECT to_regprocedure('geocode.check_api_role_limits()') IS NOT NULL")"
if [[ "$has_limits" != t ]]; then
  limits="(a dump from before configurable limits: the built-in values apply)"
else
  limits="$(docker exec "$container" psql -U pgeo -d "$staging" -Atc \
    'SELECT geocode.check_api_role_limits()' 2>&1 | grep '^{' || true)"
fi
[[ -n "$limits" ]] || {
  echo "the pgeo_api request limits are invalid; fix PGEO_MAX_* and swap again:" >&2
  docker exec "$container" psql -U pgeo -d "$staging" -Atc 'SELECT geocode.check_api_role_limits()' >&2 || true
  exit 1
}
say "staged: ${ver}, ${n} features, search returns ${hits} for 'main st'"
say "request limits: ${limits}"

# The switch. Two renames, each needing the database free of connections; PostgREST reconnects
# by itself. Nothing here is slow, which is the whole point of the work above.
say "switching"
psql_db postgres >/dev/null <<SQL
SELECT pg_terminate_backend(pid) FROM pg_stat_activity
 WHERE datname IN ('${database}', '${staging}') AND pid <> pg_backend_pid();
DROP DATABASE IF EXISTS ${previous} WITH (FORCE);
ALTER DATABASE ${database} RENAME TO ${previous};
ALTER DATABASE ${staging} RENAME TO ${database};
SQL
psql_db "$database" -c "NOTIFY pgrst, 'reload schema'" >/dev/null
say "switched: ${database} is now the dump, previous kept as ${previous}"

if (( keep_previous )); then
  say "keeping ${previous} (--keep-previous); drop it when you are satisfied"
else
  psql_db postgres -c "DROP DATABASE IF EXISTS ${previous} WITH (FORCE)" >/dev/null
  say "dropped ${previous}"
fi
