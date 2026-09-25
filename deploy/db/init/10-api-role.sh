#!/bin/sh
# PostgREST connects as pgeo_api: a login role that owns nothing, which the dump grants SELECT on
# the tables it reads and EXECUTE on the API functions.
#
# The role is cluster-wide and a dump does not carry its password, so something has to set one.
# This runs once, on the cluster's first boot, from the same value the rest service is given.
# pgeo_swap.sh applies it again on every restore, so a cluster that was created before this file
# existed - or one restored from a dump made elsewhere - converges on the right password too.
set -eu

psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d postgres -v pw="$PGEO_API_PASSWORD" <<'SQL'
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'pgeo_api') THEN
    CREATE ROLE pgeo_api LOGIN;
  END IF;
END $$;
-- Built through format(%L) rather than interpolated by the shell, so a password holding a quote
-- is a password and not a syntax error or an injection.
SELECT format('ALTER ROLE pgeo_api LOGIN PASSWORD %L', :'pw') \gexec
SQL
