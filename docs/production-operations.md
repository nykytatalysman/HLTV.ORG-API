# Production Operations

## Railway one-service layout

Mount one persistent volume at `/data` and set:

```text
HLTV_DATABASE_PATH=/data/hltv-service.sqlite
HLTV_PERSISTENT_DIRECTORY=/data
HLTV_RUNTIME_MODE=combined
HLTV_SCHEDULER_ENABLED=true
HLTV_INITIAL_REFRESH=false
HLTV_SCHEDULER_INTERVAL_SECONDS=900
HLTV_INGESTION_LOCK_TTL_SECONDS=3600
HLTV_API_WORKERS=1
HLTV_PRODUCTION=true
```

The image command is `python -m hltv_service.runtime`. Its entrypoint starts
with the limited privilege needed to prepare the runtime-mounted directory,
then immediately drops to `hltv`. The API stays available when a scheduled
cycle fails or is blocked. HTTP handlers never create a browser.

For local API-only execution:

```bash
HLTV_RUNTIME_MODE=api HLTV_SCHEDULER_ENABLED=false \
  python -m hltv_service.runtime
```

SQLite uses WAL, foreign keys, a configurable busy timeout, thread-confined
connections, and one ingestion writer. Do not configure multiple API workers
with SQLite.

## Retention and recovery

```bash
python -m hltv_service.admin retention
python -m hltv_service.admin integrity-check
python -m hltv_service.admin backup
python -m hltv_service.admin restore-verify /data/backups/example.sqlite
```

`HLTV_RAW_EVIDENCE_RETENTION_DAYS` defaults to 90 and
`HLTV_FAILED_EVIDENCE_RETENTION_DAYS` to 180. Zero retains indefinitely.
Deletion is bounded by `HLTV_RETENTION_BATCH_SIZE`, and evidence referenced by
normalized history is never removed. `HLTV_NORMALIZED_HISTORY_RETENTION_DAYS`
must remain zero so point-in-time reconstruction stays possible.

Before Railway volume maintenance, run the online backup command, copy the
result to access-controlled storage, and run `restore-verify` against the copy.
Restoration is an operator action: stop the service, retain the current volume,
place the verified backup at the configured database path with `hltv`
ownership, then start in API-only mode and run `integrity-check` before
re-enabling the scheduler.

## Safe eventual merge order

1. Audit both Phase 2 branches independently.
2. Make GitHub Actions and Docker validation green.
3. Merge the HLTV service changes.
4. Deploy the HLTV service privately while CounterSignal remains disabled.
5. Verify persistent volume writes and scheduled ingestion.
6. Run HLTV in shadow collection mode.
7. Merge the CounterSignal adapter behind disabled-by-default mode.
8. Generate coverage and data-quality reports.
9. Build a research-valid point-in-time dataset.
10. Run feature ablation and walk-forward evaluation.
11. Approve only repeatable out-of-sample features.

These operations are documented only; this branch does not merge or deploy.

