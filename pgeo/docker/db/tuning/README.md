`active.conf` is included by `postgresql.conf` and is **generated** by `pgeo-tune apply
<profile>` from `pgeo/tuning/profiles/<profile>.toml` (not tracked in git). Without it the
database runs on the conservative defaults in `postgresql.conf`.

    uv run --project pgeo pgeo-tune list
    uv run --project pgeo pgeo-tune apply medium

Profiles, sizing rules and every measured change: docs/TUNING_REPORT.md and
docs/PGEO_TUNING.md.
