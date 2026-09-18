Tuning files for pgeo-db. `active.conf` is included by postgresql.conf; point it at the
profile under test (copy or symlink), then restart the db container. Each change and its
measured effect is recorded in docs/PGEO_TUNING.md.
