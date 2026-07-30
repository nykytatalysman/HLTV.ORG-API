# CounterSignal integration implementation note

## Repository audit

The HLTV repository originally exposed browser-backed `Teams`, `Matches`, and
`News` clients. Parsing was already isolated in `HLTV/parsers.py`, but browser
navigation and parsing had no durable evidence store, no provider-ID contract,
and no read-only service boundary.

CounterSignal already has the systems this integration needs:

- provider orchestration in `src/match-service.js`;
- UUID-backed canonical entities and matches in
  `src/canonical-identity.js`;
- namespaced provider mappings, validity intervals, confidence states, and an
  auditable ambiguity queue in `src/identity-store.js` and
  `src/identity-service.js`;
- append-only historical observations and ingestion batches in
  `src/historical-store.js` and `src/historical-ingestion.js`;
- strict `observed_at`, nullable `source_updated_at`, `effective_at`,
  `scheduled_at`, and validity semantics in `src/historical-observation.js`;
- inclusive knowledge cutoffs and freshness selection in
  `src/point-in-time.js`;
- feature provenance and leakage enforcement in `src/historical-features.js`
  and `src/leakage-guard.js`;
- in-code SQLite migrations using `PRAGMA user_version`, idempotent DDL,
  migration rehearsal scripts, and immutable-table triggers;
- provider status objects, bounded provider-specific retries, server-side
  environment configuration, and sanitized public errors.

The integration therefore uses CounterSignal's existing canonical IDs,
`provider_mappings`, review cases, append-only observations, and point-in-time
cutoffs. It does not introduce a second identity database in CounterSignal.

## Implemented boundary

```text
HLTV.org
  -> controlled Selenium ingestion worker
  -> append-only raw evidence + normalized SQLite observations
  -> FastAPI JSON service
  -> CounterSignal HLTV client/provider adapter
  -> existing canonical identity and historical ingestion
```

Only `python -m hltv_service.worker` imports and constructs
`SeleniumFetcher`. `hltv_service.app` reads SQLite only. CounterSignal cannot
send HTML to the service and cannot trigger ingestion over HTTP.

Every normalized record keeps `provider = "hltv"`, its numeric provider ID,
provider URL, source snapshot reference, and point-in-time timestamps. A
missing upstream ID stays null; it is never derived from a name.

## Identity and evidence decisions

HLTV numeric IDs are provider identities, not CounterSignal canonical IDs.
CounterSignal first checks an exact active provider mapping. Ambiguous
cross-provider candidates create review cases; name similarity alone never
confirms a merge. Raw snapshot IDs flow into historical
`providerReference`/source evidence, while normalized payload hashes remain
deterministic and idempotent.

The GPL-3.0-or-later Python code remains a separately operated network
service. No Python source is copied or vendored into CounterSignal.

## Operational failure model

The worker persists HTML before parsing. Recognized Cloudflare challenges are
stored as blocked evidence, set the run state to `blocked`, receive no
immediate retry, and return a non-zero exit status. Existing normalized rows
remain untouched. Parser-layout failures are distinct from an explicit empty
match schedule.

The API returns stale cached records with age metadata. It returns `503` only
when a collection has no usable cache. Raw HTML is excluded from evidence
responses unless the development-only flag is explicitly enabled.
