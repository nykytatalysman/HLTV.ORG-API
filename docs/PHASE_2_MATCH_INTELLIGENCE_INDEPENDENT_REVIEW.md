# Phase 2 Match Intelligence Independent Review

Reviewed base: `feature/match-intelligence-v2` at
`933697028d15e47c436d159245c6709a11f8d63b`.

## Confirmed findings

| Severity | Finding | Resolution |
| --- | --- | --- |
| High | One SQLite connection was shared with `check_same_thread=False` across FastAPI worker threads while the scheduler could write concurrently. WAL did not make that connection object safe. | Replaced it with connection-per-thread operation scopes, WAL, foreign keys, a configurable busy timeout, bounded transactions, and concurrent read/write tests. |
| High | The production image started Uvicorn only. Scheduled ingestion required a second Railway process and did not meet the one-service design. | Added `python -m hltv_service.runtime`, a failure-isolated scheduler thread, single-writer locking, signals, and deterministic cleanup. |
| High | Build-time ownership of `/data` is lost when Railway mounts a volume at container start. | Added a narrowly privileged entrypoint which prepares only the configured database directory and files, then uses `gosu` to run all application and browser work as `hltv`. |
| Medium | Operational status omitted lock, scheduler, blocked-run, parser, queue, freshness, duration, and storage metrics needed for safe shadow operations. | Expanded `/v1/status` and added authenticated `/v1/operations`. |
| Medium | No bounded evidence retention, online backup, integrity check, or restore-verification commands existed. | Added `hltv_service.admin`; normalized history remains indefinite and referenced evidence is preserved. |
| Medium | Automatic CI did not build or exercise the production container or Chromium. | Added Python, Docker persistence/auth/non-root/shutdown, and local Chromium startup jobs. |
| Low | The live smoke command emitted entity names and did not use a machine-readable sanitized report. | It now emits metadata/counts only, performs one paced navigation, and is manual-only. |
| Documentation only | Railway combined-runtime, backup, restore, and eventual merge order were not documented together. | Added production operations documentation. |

## Areas reviewed without a confirmed defect

- Migrations are ordered and idempotent; normalized observations are append-only.
- Raw evidence is saved before parsing and normalized verification references
  preserve first/last verified semantics.
- The worker lock is acquired atomically, expires by timestamp, and rejects
  overlap.
- Scheduler candidates are bounded and prioritized by match state and start time.
- V2 cutoff queries and verification cutoffs prevent post-cutoff observations
  from entering historical responses.
- Missing required match containers fail loudly; section-level partial failures
  remain explicit.
- Provider numeric IDs flow through identity mappings and unresolved players
  create review cases instead of guessed merges.
- Blocked pages are saved, are not immediately retried, and preserve the cache.
- Fetchers and storage are closed in worker `finally` blocks.

No additional scraping scope was added by this work.

