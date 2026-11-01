#!/bin/sh
# PostgREST connects as pgeo_api: a login role that owns nothing, which the dump grants SELECT on
# the tables it reads and EXECUTE on the API functions.
#
# The role is cluster-wide and a dump does not carry its password or settings, so something has
# to set them. This runs once, on the cluster's first boot, from the values the compose file
# passes in. pgeo_swap.sh runs the same api-role.psql on every restore, so a cluster created
# before a setting existed, or a change to .env, converges on the next swap.
#
# What it sets (password, statement timeout, request limits) and where each comes from is
# described at the top of api-role.psql. The statement timeout caps any one API query at 5 s by
# default: the slowest measured query is about 0.5 s (a full New York address parse), so it only
# ever stops a runaway, and it bounds what a burst can cost when nothing in front limits rates.
set -eu

[ -n "${PGEO_API_PASSWORD:-}" ] || { echo "PGEO_API_PASSWORD is not set" >&2; exit 1; }
# An absolute path: the entrypoint sources a .sh that is not executable, and $0 is then its own.
psql -U "$POSTGRES_USER" -d postgres -f /docker-entrypoint-initdb.d/api-role.psql
