# perf_lab

The prototype and workload behind `docs/PERFORMANCE_OPTIMIZATION.md`, kept so the measurements
can be re-run. Nothing here is installed by the build.

- `workload.sql` - 2,920 keystrokes from 140 seeded type-ahead sessions (no real addresses beyond
  what the public load corpus already holds).
- `autocomplete_prototype.sql` - `perf_lab.autocomplete4`, findings F1 to F4 applied. It reads
  `perf_lab.feature_ac`, which is built with:

```sql
CREATE SCHEMA IF NOT EXISTS perf_lab;
CREATE TABLE perf_lab.feature_ac AS
  SELECT id, importance, geom, layer, label, name_norm, tokens
  FROM pgeo.feature WHERE layer <> 'address';
CREATE INDEX ON perf_lab.feature_ac USING gin (tokens);
CREATE INDEX ON perf_lab.feature_ac USING gin (name_norm gin_trgm_ops);
ANALYZE perf_lab.feature_ac;
```

`DROP SCHEMA perf_lab CASCADE;` removes all of it.
