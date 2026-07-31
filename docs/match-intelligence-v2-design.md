# Match intelligence v2 design

## Audited base

Phase 2 starts from `feature/counter-signal-data-service-v1` at `fa5ce15`
and CounterSignal's cache-only integration at `83a9b1f`. CounterSignal Phase 2
also carries the validated Railway memory/persistence hardening from
`origin/hotfix/railway-memory-stability`.

CounterSignal remains a single-writer Railway runtime. Its file-backed SQLite
adapter, canonical identity tables, ambiguity review queue, append-only
historical observations, feature registry, and inclusive point-in-time rule
(`observed_at <= cutoff`) remain authoritative. The HLTV service does not
create a competing canonical identity or feature system.

The HLTV API process is cache-only. A separately scheduled ingestion worker is
the only component that imports Selenium. A SQLite ingestion lock prevents
overlapping workers. API reads remain available while ingestion is blocked or
partially failing.

## Source fields and selector strategy

The match parser is split into isolated header, teams, event, lineup, veto,
map, result, recent-result, and head-to-head sections. Critical match identity,
participant identity, and schedule fields reject an unusable record. Optional
sections report completeness and section errors without invalidating useful
sections.

Primary selectors follow the current public match-page structure:

- `.countdown`, `.timeAndEvent .date[data-unix]`, and `.preformatted-text`
  for lifecycle, schedule, format, and LAN/online metadata;
- `.team1-gradient` and `.team2-gradient` for participants and displayed rank;
- `.timeAndEvent .event a` for stable event identity;
- `div.players`, player links, and `data-player-id` for match lineups;
- the last `.veto-box .padding` for ordered veto actions;
- `.mapholder`, `.mapname`, `.results-team-score`, and
  `.results-center-half-score` for map results;
- `.head-to-head-listing` and `.past-matches` only when each row exposes a
  stable HLTV match URL.

Team map statistics use the bounded `/stats/teams/maps/{team_id}/-` page with
explicit start and end dates. Event details use `/events/{event_id}/-` and
stable team links.

The selector design was cross-checked against the maintained unofficial
[HLTV getMatch parser](https://github.com/gigobyte/HLTV/blob/master/src/endpoints/getMatch.ts),
[team-stat parser](https://github.com/gigobyte/HLTV/blob/master/src/endpoints/getTeamStats.ts),
and [event parser](https://github.com/gigobyte/HLTV/blob/master/src/endpoints/getEvent.ts).
Those projects are references only; no source is copied or vendored.

## Normalized records

New service models cover:

- match detail and section-level completeness;
- match-specific lineups and optional coaches;
- ordered veto actions;
- per-map results and reliable half/overtime information;
- bounded team map statistics;
- recent team results and limited displayed head-to-head samples;
- event details and participating teams.

Unknown values remain `null`. Player IDs are never fabricated. Map display
values are retained alongside a conservative canonical map ID from a fixed
alias table. Unrecognized maps retain the display value and a null canonical
ID.

## Evidence and freshness lineage

Every navigation stores raw compressed HTML and SHA-256 evidence before
parsing. Content-addressed snapshots are immutable. Repeated captures of the
same HTML append verification records, allowing `last_verified_at` to advance
without duplicating normalized state.

Normalized observation identity excludes capture time and evidence ID. An
identical state therefore remains one state observation while each successful
verification remains auditable. A changed normalized state creates a new
append-only observation linked to its evidence snapshot.

Lineage is:

```text
requested URL
  -> raw snapshot
  -> snapshot verification
  -> normalized observation
  -> normalized verification
  -> v2 cache response
  -> CounterSignal provider mapping
  -> canonical historical observation
  -> point-in-time feature row
```

## Point-in-time rules

- API `observed-before` filters select only states first observed by the
  requested cutoff and only verification events at or before that cutoff.
- Match results and completed map scores cannot be used before their
  observation time.
- Match lineups are stored separately from team-profile rosters. A current
  roster never rewrites a historical match lineup.
- Veto updates create new states; earlier feature cutoffs see only earlier
  actions.
- Team map statistics and event details use their actual capture time.
- CounterSignal continues to enforce `observed_at <= cutoff`, source-update,
  validity, identity, and quality rules in its existing point-in-time query
  system. Provider `effective_at` values that describe a future scheduled
  match are retained as source payload metadata rather than incorrectly used
  as an observation-validity start.

## Failure handling

Blocked, deleted, unavailable, postponed, cancelled, incomplete, and layout
regression states are distinct. A blocked capture is saved and is not retried
immediately. Per-item parser/navigation failures are recorded without deleting
cache or fabricating empty records. A batch may retain successful items even
when another item fails. The first Cloudflare block ends further browser work
for that run and returns an explicit non-zero blocked status.

## CounterSignal feature mapping

Existing registry concepts remain authoritative:

- `mapStatsA/B` receive canonical-map observations and sample metadata;
- `recentFormA/B` use stable, earlier match results;
- `headToHead` uses only direct stable-ID meetings observed before cutoff;
- `rosterA/B` remains the general team roster feature.

New entries cover match-specific lineup verification, observed veto context,
event context, and HLTV data quality. Rank, rank difference, ranking points,
movement, map strength differences, low-sample flags, roster change counts,
event context, H2H summaries, source age, and completeness are deterministic
translations only. The HLTV service contains no model, probability, staking,
or betting recommendation logic.
