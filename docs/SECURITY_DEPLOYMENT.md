# Security: deployment scenarios for Pelias and pgeo

What each platform exposes, how to deploy each one safely at three levels of ambition, and
where hardening pulls against the project's stated goal of the fewest moving parts (G6).

Everything in the "as deployed" column was measured on VM 120 on 2026-09-20, not assumed.
`tests/security/test_posture.py` asserts each control and runs in `scripts/rebuild_all.sh`
(`verify` and `deploy` stages).

## 1. What each platform actually exposes

| | Pelias | pgeo |
|---|---|---|
| Processes reachable over HTTP | 6 (API, Elasticsearch, libpostal, placeholder, pip, interpolation) | 2 (PostgREST, PostgreSQL) |
| Authentication between them | none; services trust anything that can reach them | PostgreSQL password, and a role that holds `SELECT` only |
| Data store credentials | **none** - Elasticsearch accepts unauthenticated reads and writes from anything that can open the port | password, per-role privileges, `pg_hba` |
| Blast radius of one compromised component | the whole index: read, modify or delete | whatever `SELECT` allows; no write path exists |
| Query text in logs | not logged by the engine | not logged (`log_parameter_max_length = 0`, `log_statement = none`) |
| Attack surface in our own code | none (upstream services) | the SQL functions and the edge rules in this repository |
| Patching | six upstream images | two upstream images |

The asymmetry that matters: **Elasticsearch has no credentials at all.** Pelias's design assumes
the data store is unreachable, so the only thing standing between a foothold on the host and the
whole index is the loopback binding. pgeo's data store authenticates, and the account the API
uses cannot write even with the password. That is not a criticism of Pelias's code - it is the
posture its architecture asks you to maintain.

The countervailing point: **pgeo's attack surface includes code we wrote.** Pelias's query path
is maintained by a project with many users; pgeo's ranking SQL, its edge parameter rewriting and
its `/v1/address` endpoint are this project's own. Bugs there are ours to find.

## 2. As deployed today (measured)

| Control | State |
|---|---|
| Engine ports | all bound to `127.0.0.1`; only nginx listens on the LAN address |
| Firewall | ufw default deny inbound; 8080 from the TLS terminator only, 22 from the LAN and the tailnet |
| TLS | terminated by the wharf Caddy with public DNS-01 certificates; plain HTTP inside the LAN hop |
| Methods | everything but `GET` refused at the edge |
| Rate limits | 10 r/s search, 25 r/s autocomplete per client IP, keyed on the real address from `X-Forwarded-For` |
| Headers | CSP `default-src 'self'` with no remote origin, `nosniff`, `X-Frame-Options: DENY`, Referrer-Policy, Permissions-Policy |
| Demo page | no `innerHTML`, `document.write` or `eval` anywhere in its own JavaScript; every value reaches the DOM through `textContent` |
| Unknown parameters | rejected with a Pelias-shaped 400 on the pure-SQL path |
| Request bodies | capped at 4 KB |
| Access log | path only - the query string is **not** logged (fixed 2026-09-20; see below) |
| Database | `pgeo_api` is a non-superuser with `SELECT` only; the password is percent-encoded into the DSN |
| Images | pinned by tag and digest |
| Authorization | **API keys at the edge** (Phase 11, done 2026-09-22): `X-API-Key` header, SHA-256 hashes in the inventory, refused in the URL, client name in the log, fails closed. `docs/API_KEYS.md` |

### Two findings from this assessment

**The access log recorded every address searched.** nginx's default `combined` format logs the
whole request line, so `/v1/search?text=389+Congress+St` sat in plaintext on disk with default
rotation. PostgreSQL was carefully configured not to log bind parameters, which made the overall
claim look satisfied while the layer in front of it undid it. Fixed with a `pathonly` log format
that records `$uri` instead of `$request`, verified on the deployed service, and now asserted by
a test. **Log files written before 2026-09-20 still contain those query strings** and should be
rotated out or deleted if the deployment handled real client addresses.

**A hand-set database password containing `/` or `#` silently produced a different host** in the
DSN, because the password was interpolated into a URL unencoded. Generated passwords are
alphanumeric so it never fired in practice. Fixed by percent-encoding; six reserved characters
are now parametrised in `pgeo/tests/test_settings.py`.

## 3. Deployment tiers

### Tier 1 - departmental, LAN-only (what this project needs)

Both platforms, as deployed today, plus:

- ~~**Authorization at the edge** (Phase 11).~~ Done: hashed API keys checked by nginx's njs
  module, no new component (`docs/API_KEYS.md`). What remains is single sign-on for the people
  behind the clients, which does cost a component and names people rather than machines.
- **Backups of the data store**, tested by restoring. Both engines rebuild from source data, so
  the backup is a convenience for the index or dump rather than the only copy - which is itself a
  security property: a ransomware event costs a rebuild, not the data.
- **Log retention shortened** for the edge, now that the path is all it records.

Pelias-specific: keep Elasticsearch on loopback and never publish 9200, even "temporarily";
that single binding is the whole access-control story.

pgeo-specific: keep the `pgeo_api` role at `SELECT`, and keep the tuning allowlist - it is what
stops a configuration change from becoming arbitrary SQL.

### Tier 2 - shared across a organisation, still internal

Add, for both:

- **Per-client credentials and quotas**, so one team's batch job cannot exhaust the service, and
  an abusive query pattern can be attributed. Rate limits keyed on IP stop being meaningful once
  requests arrive through a shared proxy.
- **TLS on the internal hop**, not only at the terminator. Today the LAN hop is plain HTTP; on a
  shared network that is a sniffable list of addresses people looked up.
- **Audit logging of who asked**, deliberately separated from what they asked, so access can be
  reviewed without building the address log this project just removed.
- **A read-only replica** for anything ad hoc, so exploratory use cannot affect the service.

Pelias-specific: enable Elasticsearch security (the free tier's basic authentication) and give
the API its own credentials. This is the single biggest change in its posture and it costs
configuration, not components.

pgeo-specific: move the database to its own host or at least its own volume with separate
credentials per front end; PostgREST already holds nothing but a connection string.

### Tier 3 - internet-facing

Both platforms need, in addition:

- **A WAF or at minimum request shaping** in front, since a geocoder is an attractive scraping
  target: the index is the asset, and unmetered querying extracts it.
- **Per-key quotas and billing-grade accounting**, not just rate limits.
- **Query-pattern monitoring** - bulk enumeration looks nothing like a person typing.
- **An egress policy**: neither engine should make outbound connections at runtime; alert if one
  does.
- **Separate build and serve hosts.** The build needs 5.5-11.5 GB and pulls from the internet;
  the server needs neither. Never build on an exposed host.

At this tier the platforms converge: the controls are mostly in front of the engine, and the
choice between them stops being a security question.

## 4. Where hardening pulls against "fewest components"

This is the real tension, and it is worth being explicit rather than pretending the goals align.

| Hardening step | What it costs | Verdict for a departmental deployment |
|---|---|---|
| Authorization at the edge | hashed keys in nginx: nothing. SSO: an identity provider, a session layer, and an outage mode | Do the keys now; SSO only if the organisation already runs one |
| TLS on the internal hop | certificate management on an internal name, or a service mesh | Skip on a trusted LAN; required the moment the network is shared |
| Elasticsearch authentication (Pelias) | configuration only, no new component | Do it - it is the cheapest large improvement available to Pelias |
| Per-client quotas | a state store (Redis or similar) for counters | Defer; IP-keyed limits are enough for a department |
| WAF | a component, tuning, and false positives against odd but legitimate place names | Not for LAN-only |
| Read-only replica | doubles the database footprint | Only when ad-hoc use appears |
| Separate build host | a second machine, or a machine rented per build | Already true here: builds run on a workstation and ship a snapshot or dump |

**The honest summary of the tension.** Almost every control worth having at Tier 1 is
configuration rather than a component: a firewall rule, a binding, a role grant, a log format, a
header. The expensive ones - identity providers, WAFs, quota stores, replicas - buy protection
against threats a LAN-only departmental service does not face. G6 and good security agree for
longer than one might expect, and they part company at the point where the service stops being
departmental.

The one place they genuinely conflicted was **authorization**. Any authentication scheme adds
something: a key store, a session, a place to revoke. The lightest honest answer - hashed API
keys checked in nginx with keys issued out of band - turned out to add nothing: the hash is
computed by a module nginx already ships, the key store is the inventory file the deployment
already has, and revocation is deleting a line and reloading. It is not single sign-on, and for
three users in one department it does not need to be.

## 5. Why self-hosting and LAN-only help, beyond cost

The project chose self-hosting for control and cost. The security case is at least as strong, and
it is worth stating because it is the argument that survives scrutiny when someone proposes a
commercial API instead.

- **The queries never leave.** A geocoder's request log is a behavioural record: who is being
  dispatched to, which addresses a department is interested in, when. With a commercial API that
  record exists on someone else's infrastructure under their retention policy. For LANCER, whose
  client addresses are encrypted at rest precisely because they are sensitive, sending them to a
  third party for resolution would undo that protection at the only moment it matters.
- **No third-party availability or pricing risk.** A dependency that can change terms is a
  security concern as much as a commercial one; an outage mid-dispatch is an operational one.
- **LAN-only removes an entire class of threat rather than mitigating it.** There is no
  credential stuffing, no bot traffic, no scraping, and no exposure to internet-wide scanning.
  Controls that would be mandatory on a public endpoint become optional, which is why a
  two-container service on a 1 GB machine is a defensible posture here and would not be on the
  internet.
- **The blast radius is bounded by the data.** Both engines serve public open data. A total
  compromise of the index or database leaks nothing that is not already downloadable - the
  sensitive material is in the *queries*, which is why the log finding above mattered more than
  anything about the data store.
- **Rebuildability is a security property.** Both platforms rebuild from source data with one
  command, so the recovery answer to tampering, ransomware or a suspect image is a rebuild from
  a known recipe, not a forensic argument about whether the index is still trustworthy.

The cost is that patching is now the department's job. Six pinned images (Pelias) or two (pgeo)
have to be updated deliberately; nobody does it in the background. That is the honest trade for
everything above, and it is smaller for pgeo purely because there is less of it.

## 6. Recommendation

For the deployment this project set out to build - a handful of users, one department, a LAN:

1. **pgeo on the current posture plus hashed API keys at the edge** is a defensible Tier 1
   service today. Its data store authenticates, its API role cannot write, and there are two
   components to patch.
2. **If Pelias is kept**, enable Elasticsearch authentication. It is configuration, not a
   component, and it closes the one gap where a foothold on the host becomes control of the data.
3. **Do not expose either to the internet** without Tier 3, at which point the security question
   no longer distinguishes them and the choice returns to accuracy, memory and throughput.
