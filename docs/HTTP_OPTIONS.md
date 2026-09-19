# HTTP Options for "Postgres as the API endpoint" (Phase 13)

Research note, 2026-09-18. Question: how should HTTP requests reach the geocoder when the
whole service (parsing, search, ranking, Pelias-shaped JSON) lives in PostgreSQL? The
user's constraint for this part: prefer PostgreSQL core + contrib; external options need
arguments for and against. Recommendation at the end.

## 1. The starting fact

**Neither PostgreSQL core nor any contrib extension contains an HTTP server.** Core offers
the building blocks (background workers, custom extensions in C), and the contrib
extensions are all in-database features (pg_trgm, unaccent, fuzzystrmatch, ...). So every
way of answering HTTP is one of:

- (a) a **third-party extension** that runs an HTTP server *inside* Postgres, or
- (b) an **external process** that speaks HTTP and forwards to Postgres over the normal
  client protocol (a "gateway"), or
- (c) our **own code**, either a custom extension (C or Rust/pgrx background worker) or an
  application server (today's FastAPI layer).

Extensions such as `pgsql-http` and `pg_net` are HTTP *clients* (Postgres calling out), not
servers, so they do not apply.

## 2. Requirements that decide the choice

| Requirement | Why it matters here |
|-------------|--------------------|
| Pelias-compatible GET URLs, including dotted parameter names (`focus.point.lat`, `boundary.rect.min_lon`) | Demo page, `<pelias-search>` widget, k6 load tests and the accuracy harness all speak Pelias |
| Read-only, least privilege | The HTTP-facing role may only EXECUTE the `geocode_api` functions |
| Handles hundreds of concurrent users on a small VM | Load targets in LOAD_TEST_PLAN.md |
| Works behind wharf Caddy (TLS there) and later on the VPS | TLS inside the component is optional |
| PostgreSQL 18 | Phase 10 target |
| Maintained, reviewable, pinned versions | Security baseline |

## 3. Options

| Option | Kind | How it would serve `/v1/search` | Fit |
|--------|------|---------------------------------|-----|
| **PostgREST** (Haskell, MIT) | External gateway | `GET /rpc/v1_search?text=...` maps query params to function arguments by name; edge rewrites `/v1/search` to `/rpc/v1_search` | Strong: mature, stateless, role-based security, zero business logic outside SQL. Risk: dotted keys may be read as embedded-resource filters, needs a test; reserved names (`select`, `order`, `limit`, `offset`, ...) cannot be argument names |
| **Omnigres `omni_httpd`** (C, Apache-2.0) | In-database extension | HTTP workers are Postgres backends; route handlers are SQL | The literal "Postgres is the endpoint". Authors describe it as early work (HTTPS and HTTP/2 multiplexing still maturing); PG18 via packages; third-party extension in the database |
| **OpenResty + pgmoon** (nginx + Lua, BSD/MIT) | External gateway | nginx location calls `SELECT geocode_api.v1_search(...)` via the pgmoon Lua driver and returns the JSON | Small, fast, nginx already sits on the VM; a few dozen lines of Lua to own; non-standard nginx build |
| **ngx_postgres** (nginx upstream module) | External gateway | nginx talks to Postgres directly (RDS output, needs conversion) | Needs a custom OpenResty build (`--with-http_postgres_module`); older, less active |
| **pREST** (Go) | External gateway | REST over tables and custom SQL routes (TOML) | Workable, smaller community than PostgREST; routes defined outside SQL |
| **pg_featureserv** (Go, Crunchy Data) | External gateway | OGC API Features: `/functions/{name}/items` returns GeoJSON from functions in schema `postgisftw` | Good for spatial function publishing, but its URL and response shapes are OGC, not Pelias; a rewrite layer would be needed |
| **GraphQL gateways** (PostGraphile, Hasura, pg_graphql + gateway) | External | GraphQL endpoint | Wrong protocol for a Pelias-compatible REST API |
| **Custom extension** (C / Rust pgrx background worker) | Own in-database code | Our own HTTP listener inside Postgres | Maximum control and purity, but we would own an HTTP server's security and maintenance: not justified |
| **FastAPI (current Phase 10 layer)** | Own application server | Already built | Kept as the comparison baseline |

## 4. Arguments: external gateway (PostgREST as the representative)

**For**

- **Keeps the database core-only.** No third-party code runs inside the Postgres process
  (or needs to be installed into it); the database stays PostgreSQL + PostGIS + contrib.
- **Failure isolation.** A gateway crash, memory leak or bad request cannot take down or
  corrupt the database; it restarts independently.
- **Mature and widely deployed**, with a clear security model: every request runs as a
  database role; only exposed schemas and granted functions are reachable.
- **All logic still lives in SQL**, which satisfies the goal. The gateway is a translator
  with no business rules, so it can be swapped (PostgREST, OpenResty, pREST) without
  touching the geocoder.
- **Independent scaling and upgrades.** The gateway can move, restart or be upgraded
  without a database restart; several instances can share one database.
- **Connection pooling** is built in, so many HTTP clients share a small number of
  database connections.

**Against**

- **One more process** to deploy, monitor and pin (though small and stateless).
- **An extra network hop** (usually a local socket; sub-millisecond, but measurable).
- **URL and parameter shape is the gateway's**, not ours: PostgREST uses `/rpc/<fn>` and
  may interpret dotted keys; the edge must rewrite paths (and possibly parameter names).
- **Error format** comes from the gateway unless the SQL functions return errors as data.

## 5. Arguments: in-database HTTP (Omnigres `omni_httpd` as the representative)

**For**

- **Literally "Postgres is the API"**: one process tree, one thing to deploy, and request
  handling stays inside the database's transaction and security model.
- **No extra hop**, and no separate connection pool; the published benchmark was fast
  (tens of thousands of simple requests per second).
- **Full control over URLs and parameters**, so Pelias paths and dotted parameter names can
  be matched exactly without an edge rewrite.

**Against**

- **Third-party C code inside the database process**: a bug or crash in the HTTP layer can
  bring down Postgres backends; the database's attack surface grows by an HTTP parser.
- **Maturity**: the authors describe it as early work (HTTPS support and HTTP/2
  multiplexing were listed as unfinished), with a smaller user base than the gateways.
- **Upgrades are coupled**: the extension must be rebuilt and re-verified for every
  PostgreSQL minor/major version; a database upgrade can be blocked by the extension.
- **Resource contention**: HTTP workers are Postgres backends and compete with queries for
  `max_worker_processes`, memory and CPU; tuning one affects the other.
- **Not core or contrib**, which is exactly what the user's constraint asks to avoid.

## 6. Recommendation

1. **Primary: PostgREST** in front of `geocode_api` (all logic in SQL, database stays
   core + contrib + PostGIS). Edge (nginx on the VM / Caddy) rewrites `/v1/<endpoint>` to
   `/rpc/v1_<endpoint>`. First test in Phase 13: whether dotted parameter names reach the
   functions; if not, the edge renames them (or the SQL functions take underscore names
   and the edge maps them).
2. **Fallback / alternative: OpenResty + pgmoon**, if PostgREST's URL grammar gets in the
   way: still no third-party code in the database, full control of paths and parameters,
   a few dozen lines of Lua.
3. **Not pursued: Omnigres**, because it puts third-party code inside the database, which
   is what the constraint avoids. It stays documented as the "purest" option should that
   property ever outweigh maturity and isolation.
4. **Kept for comparison: FastAPI** (Phase 10), so the report can show what a pure-SQL
   design gains or loses against an application layer on accuracy, latency and memory.

## Sources

- PostgREST, functions as RPC: <https://docs.postgrest.org/en/stable/references/api/functions.html>
- Omnigres omni_httpd architecture: <https://docs.omnigres.org/omni_httpd/architecture/>;
  intro: <https://docs.omnigres.org/omni_httpd/intro/>; "What happens if you put an HTTP
  server inside Postgres?": <https://dev.to/omnigres/what-happens-if-you-put-http-server-inside-postgres-33kd>;
  repository: <https://github.com/omnigres/omnigres>; PG18 packaging via Pigsty:
  <https://pigsty.io/ext/e/omni_httpd/>
- OpenResty and PostgreSQL: <https://leafo.net/guides/using-postgres-with-openresty.html>;
  pgmoon: <https://github.com/leafo/pgmoon>; ngx_postgres:
  <https://openresty.org/en/postgres-nginx-module.html>
- pREST: <https://github.com/prest/prest>
- pg_featureserv: <https://github.com/CrunchyData/pg_featureserv>;
  <https://www.crunchydata.com/blog/crunchy-spatial-querying-spatial-features-with-pg_featureserv>
- "Just Use Postgres" (context on the trend): <https://nesbitt.io/2026/03/10/just-use-postgres.html>
