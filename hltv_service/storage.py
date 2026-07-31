"""Append-only SQLite evidence and normalized cache."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import zlib
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import PARSER_VERSION

SCHEMA_VERSION = 2
ENTITY_TABLES = {
    "ranking": "ranking_observations",
    "team": "team_observations",
    "roster": "roster_observations",
    "match": "match_observations",
    "event": "event_observations",
}
INTELLIGENCE_TABLES = {
    "match_detail": "match_detail_observations",
    "match_lineup": "match_lineup_observations",
    "match_veto": "match_veto_observations",
    "map_result": "map_result_observations",
    "team_map_stat": "team_map_stat_observations",
    "team_result": "team_result_observations",
    "head_to_head": "head_to_head_observations",
    "event_detail": "event_detail_observations",
}


def utc_now() -> datetime:
    return datetime.now(UTC)


def iso(value: datetime | None = None) -> str:
    return (value or utc_now()).astimezone(UTC).isoformat()


def canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def stable_id(prefix: str, *parts: object) -> str:
    digest = hashlib.sha256(
        "\x1f".join(str(part) for part in parts).encode()
    ).hexdigest()
    return f"{prefix}_{digest[:32]}"


class Storage:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.execute("PRAGMA journal_mode = WAL")
        self.migrate()

    def close(self) -> None:
        self.connection.close()

    def migrate(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                applied_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS raw_snapshots (
                snapshot_id TEXT PRIMARY KEY,
                requested_url TEXT NOT NULL,
                final_url TEXT NOT NULL,
                page_type TEXT NOT NULL,
                navigation_outcome TEXT NOT NULL,
                browser_title TEXT,
                captured_at TEXT NOT NULL,
                sha256 TEXT NOT NULL,
                html_zlib BLOB NOT NULL,
                blocked INTEGER NOT NULL CHECK (blocked IN (0, 1)),
                parser_version TEXT NOT NULL,
                parse_status TEXT NOT NULL,
                parse_error TEXT,
                UNIQUE(requested_url, final_url, sha256, blocked)
            );
            CREATE TABLE IF NOT EXISTS ingestion_runs (
                run_id TEXT PRIMARY KEY,
                command TEXT NOT NULL,
                attempted_at TEXT NOT NULL,
                completed_at TEXT,
                status TEXT NOT NULL,
                blocked INTEGER NOT NULL DEFAULT 0 CHECK (blocked IN (0, 1)),
                summary_json TEXT NOT NULL,
                error TEXT
            );
            CREATE TABLE IF NOT EXISTS raw_parse_attempts (
                parse_attempt_id TEXT PRIMARY KEY,
                snapshot_id TEXT NOT NULL REFERENCES raw_snapshots(snapshot_id),
                parsed_at TEXT NOT NULL,
                parser_version TEXT NOT NULL,
                parse_status TEXT NOT NULL,
                parse_error TEXT,
                UNIQUE(snapshot_id, parser_version, parse_status, parse_error)
            );
            CREATE TABLE IF NOT EXISTS ranking_observations (
                observation_id TEXT PRIMARY KEY,
                source_snapshot_id TEXT NOT NULL REFERENCES raw_snapshots(snapshot_id),
                provider_team_id INTEGER NOT NULL,
                ranking_date TEXT NOT NULL,
                region TEXT NOT NULL,
                position INTEGER NOT NULL,
                observed_at TEXT NOT NULL,
                data_json TEXT NOT NULL,
                UNIQUE(source_snapshot_id, provider_team_id, ranking_date, region)
            );
            CREATE TABLE IF NOT EXISTS team_observations (
                observation_id TEXT PRIMARY KEY,
                source_snapshot_id TEXT NOT NULL REFERENCES raw_snapshots(snapshot_id),
                provider_team_id INTEGER NOT NULL,
                observed_at TEXT NOT NULL,
                data_json TEXT NOT NULL,
                UNIQUE(source_snapshot_id, provider_team_id)
            );
            CREATE TABLE IF NOT EXISTS roster_observations (
                observation_id TEXT PRIMARY KEY,
                source_snapshot_id TEXT NOT NULL REFERENCES raw_snapshots(snapshot_id),
                provider_team_id INTEGER NOT NULL,
                observed_at TEXT NOT NULL,
                data_json TEXT NOT NULL,
                UNIQUE(source_snapshot_id, provider_team_id)
            );
            CREATE TABLE IF NOT EXISTS match_observations (
                observation_id TEXT PRIMARY KEY,
                source_snapshot_id TEXT NOT NULL REFERENCES raw_snapshots(snapshot_id),
                provider_match_id INTEGER NOT NULL,
                scheduled_at TEXT,
                status TEXT NOT NULL,
                team_one_id INTEGER,
                team_two_id INTEGER,
                event_id INTEGER,
                observed_at TEXT NOT NULL,
                data_json TEXT NOT NULL,
                UNIQUE(source_snapshot_id, provider_match_id)
            );
            CREATE TABLE IF NOT EXISTS event_observations (
                observation_id TEXT PRIMARY KEY,
                source_snapshot_id TEXT NOT NULL REFERENCES raw_snapshots(snapshot_id),
                provider_event_id INTEGER NOT NULL,
                observed_at TEXT NOT NULL,
                data_json TEXT NOT NULL,
                UNIQUE(source_snapshot_id, provider_event_id)
            );
            CREATE TABLE IF NOT EXISTS snapshot_verifications (
                verification_id TEXT PRIMARY KEY,
                snapshot_id TEXT NOT NULL REFERENCES raw_snapshots(snapshot_id),
                verified_at TEXT NOT NULL,
                navigation_outcome TEXT NOT NULL,
                blocked INTEGER NOT NULL CHECK (blocked IN (0, 1)),
                UNIQUE(snapshot_id, verified_at, navigation_outcome, blocked)
            );
            CREATE TABLE IF NOT EXISTS normalized_verifications (
                verification_id TEXT PRIMARY KEY,
                observation_kind TEXT NOT NULL,
                observation_id TEXT NOT NULL,
                source_snapshot_id TEXT NOT NULL REFERENCES raw_snapshots(snapshot_id),
                verified_at TEXT NOT NULL,
                UNIQUE(
                    observation_kind, observation_id,
                    source_snapshot_id, verified_at
                )
            );
            CREATE TABLE IF NOT EXISTS ingestion_item_results (
                item_result_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL REFERENCES ingestion_runs(run_id),
                item_type TEXT NOT NULL,
                provider_id INTEGER,
                requested_url TEXT NOT NULL,
                attempted_at TEXT NOT NULL,
                completed_at TEXT NOT NULL,
                status TEXT NOT NULL,
                source_snapshot_id TEXT,
                error_type TEXT,
                error TEXT
            );
            CREATE TABLE IF NOT EXISTS worker_locks (
                lock_name TEXT PRIMARY KEY,
                owner_id TEXT NOT NULL,
                acquired_at TEXT NOT NULL,
                expires_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS match_detail_observations (
                observation_id TEXT PRIMARY KEY,
                state_hash TEXT NOT NULL,
                source_snapshot_id TEXT NOT NULL REFERENCES raw_snapshots(snapshot_id),
                provider_match_id INTEGER NOT NULL,
                scheduled_at TEXT NOT NULL,
                status TEXT NOT NULL,
                team_one_id INTEGER NOT NULL,
                team_two_id INTEGER NOT NULL,
                event_id INTEGER,
                observed_at TEXT NOT NULL,
                effective_at TEXT,
                data_json TEXT NOT NULL,
                UNIQUE(provider_match_id, state_hash)
            );
            CREATE TABLE IF NOT EXISTS match_lineup_observations (
                observation_id TEXT PRIMARY KEY,
                state_hash TEXT NOT NULL,
                source_snapshot_id TEXT NOT NULL REFERENCES raw_snapshots(snapshot_id),
                provider_match_id INTEGER NOT NULL,
                provider_team_id INTEGER NOT NULL,
                observed_at TEXT NOT NULL,
                effective_at TEXT,
                data_json TEXT NOT NULL,
                UNIQUE(provider_match_id, provider_team_id, state_hash)
            );
            CREATE TABLE IF NOT EXISTS match_lineup_players (
                lineup_player_id TEXT PRIMARY KEY,
                lineup_observation_id TEXT NOT NULL
                    REFERENCES match_lineup_observations(observation_id),
                provider_match_id INTEGER NOT NULL,
                provider_team_id INTEGER NOT NULL,
                provider_player_id INTEGER,
                nickname TEXT NOT NULL,
                role TEXT,
                stand_in INTEGER,
                coach INTEGER NOT NULL CHECK (coach IN (0, 1))
            );
            CREATE TABLE IF NOT EXISTS match_veto_observations (
                observation_id TEXT PRIMARY KEY,
                state_hash TEXT NOT NULL,
                source_snapshot_id TEXT NOT NULL REFERENCES raw_snapshots(snapshot_id),
                provider_match_id INTEGER NOT NULL,
                sequence_number INTEGER NOT NULL,
                provider_team_id INTEGER,
                canonical_map_id TEXT,
                observed_at TEXT NOT NULL,
                data_json TEXT NOT NULL,
                UNIQUE(provider_match_id, sequence_number, state_hash)
            );
            CREATE TABLE IF NOT EXISTS map_result_observations (
                observation_id TEXT PRIMARY KEY,
                state_hash TEXT NOT NULL,
                source_snapshot_id TEXT NOT NULL REFERENCES raw_snapshots(snapshot_id),
                provider_match_id INTEGER NOT NULL,
                map_order INTEGER NOT NULL,
                canonical_map_id TEXT,
                observed_at TEXT NOT NULL,
                data_json TEXT NOT NULL,
                UNIQUE(provider_match_id, map_order, state_hash)
            );
            CREATE TABLE IF NOT EXISTS team_map_stat_observations (
                observation_id TEXT PRIMARY KEY,
                state_hash TEXT NOT NULL,
                source_snapshot_id TEXT NOT NULL REFERENCES raw_snapshots(snapshot_id),
                provider_team_id INTEGER NOT NULL,
                canonical_map_id TEXT,
                range_start TEXT NOT NULL,
                range_end TEXT NOT NULL,
                observed_at TEXT NOT NULL,
                data_json TEXT NOT NULL,
                UNIQUE(
                    provider_team_id, canonical_map_id,
                    range_start, range_end, state_hash
                )
            );
            CREATE TABLE IF NOT EXISTS team_result_observations (
                observation_id TEXT PRIMARY KEY,
                state_hash TEXT NOT NULL,
                source_snapshot_id TEXT NOT NULL REFERENCES raw_snapshots(snapshot_id),
                provider_team_id INTEGER NOT NULL,
                provider_match_id INTEGER NOT NULL,
                opponent_team_id INTEGER,
                event_id INTEGER,
                match_date TEXT NOT NULL,
                observed_at TEXT NOT NULL,
                data_json TEXT NOT NULL,
                UNIQUE(provider_team_id, provider_match_id, state_hash)
            );
            CREATE TABLE IF NOT EXISTS head_to_head_observations (
                observation_id TEXT PRIMARY KEY,
                state_hash TEXT NOT NULL,
                source_snapshot_id TEXT NOT NULL REFERENCES raw_snapshots(snapshot_id),
                provider_match_id INTEGER NOT NULL,
                provider_team_one_id INTEGER NOT NULL,
                provider_team_two_id INTEGER NOT NULL,
                event_id INTEGER,
                match_date TEXT NOT NULL,
                observed_at TEXT NOT NULL,
                data_json TEXT NOT NULL,
                UNIQUE(
                    provider_match_id, provider_team_one_id,
                    provider_team_two_id, state_hash
                )
            );
            CREATE TABLE IF NOT EXISTS event_detail_observations (
                observation_id TEXT PRIMARY KEY,
                state_hash TEXT NOT NULL,
                source_snapshot_id TEXT NOT NULL REFERENCES raw_snapshots(snapshot_id),
                provider_event_id INTEGER NOT NULL,
                start_at TEXT,
                end_at TEXT,
                observed_at TEXT NOT NULL,
                effective_at TEXT,
                data_json TEXT NOT NULL,
                UNIQUE(provider_event_id, state_hash)
            );
            CREATE INDEX IF NOT EXISTS rankings_lookup
                ON ranking_observations(ranking_date, region, position, observed_at);
            CREATE INDEX IF NOT EXISTS teams_lookup
                ON team_observations(provider_team_id, observed_at);
            CREATE INDEX IF NOT EXISTS matches_lookup
                ON match_observations(provider_match_id, scheduled_at, observed_at);
            CREATE INDEX IF NOT EXISTS events_lookup
                ON event_observations(provider_event_id, observed_at);
            CREATE INDEX IF NOT EXISTS snapshot_verification_lookup
                ON snapshot_verifications(snapshot_id, verified_at DESC);
            CREATE INDEX IF NOT EXISTS normalized_verification_lookup
                ON normalized_verifications(
                    observation_kind, observation_id, verified_at DESC
                );
            CREATE INDEX IF NOT EXISTS ingestion_item_status_lookup
                ON ingestion_item_results(
                    run_id, item_type, provider_id, status, attempted_at
                );
            CREATE INDEX IF NOT EXISTS match_detail_lookup
                ON match_detail_observations(
                    provider_match_id, scheduled_at, observed_at DESC
                );
            CREATE INDEX IF NOT EXISTS match_detail_event_lookup
                ON match_detail_observations(event_id, scheduled_at);
            CREATE INDEX IF NOT EXISTS match_lineup_lookup
                ON match_lineup_observations(
                    provider_match_id, provider_team_id, observed_at DESC
                );
            CREATE INDEX IF NOT EXISTS match_lineup_player_lookup
                ON match_lineup_players(
                    provider_player_id, provider_match_id, provider_team_id
                );
            CREATE INDEX IF NOT EXISTS match_veto_lookup
                ON match_veto_observations(
                    provider_match_id, sequence_number, observed_at DESC
                );
            CREATE INDEX IF NOT EXISTS match_veto_map_lookup
                ON match_veto_observations(canonical_map_id, observed_at DESC);
            CREATE INDEX IF NOT EXISTS map_result_lookup
                ON map_result_observations(
                    provider_match_id, map_order, observed_at DESC
                );
            CREATE INDEX IF NOT EXISTS map_result_map_lookup
                ON map_result_observations(canonical_map_id, observed_at DESC);
            CREATE INDEX IF NOT EXISTS team_map_stat_lookup
                ON team_map_stat_observations(
                    provider_team_id, canonical_map_id, observed_at DESC
                );
            CREATE INDEX IF NOT EXISTS team_result_lookup
                ON team_result_observations(
                    provider_team_id, match_date DESC, observed_at DESC
                );
            CREATE INDEX IF NOT EXISTS team_result_match_lookup
                ON team_result_observations(provider_match_id, observed_at DESC);
            CREATE INDEX IF NOT EXISTS head_to_head_lookup
                ON head_to_head_observations(
                    provider_team_one_id, provider_team_two_id,
                    match_date DESC, observed_at DESC
                );
            CREATE INDEX IF NOT EXISTS event_detail_lookup
                ON event_detail_observations(
                    provider_event_id, start_at, observed_at DESC
                );
            """
        )
        append_only_tables = (
            "raw_snapshots",
            "raw_parse_attempts",
            "snapshot_verifications",
            "normalized_verifications",
            "ingestion_item_results",
            "match_lineup_players",
            *ENTITY_TABLES.values(),
            *INTELLIGENCE_TABLES.values(),
        )
        for table in append_only_tables:
            self.connection.executescript(
                f"""
                CREATE TRIGGER IF NOT EXISTS {table}_no_update
                BEFORE UPDATE ON {table} BEGIN
                    SELECT RAISE(ABORT, 'append-only table');
                END;
                CREATE TRIGGER IF NOT EXISTS {table}_no_delete
                BEFORE DELETE ON {table} BEGIN
                    SELECT RAISE(ABORT, 'append-only table');
                END;
                """
            )
        self.connection.execute(
            """
            INSERT OR IGNORE INTO schema_migrations(version, applied_at)
            VALUES (?, ?)
            """,
            (SCHEMA_VERSION, iso()),
        )
        self.connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        self.connection.commit()

    @contextmanager
    def transaction(self) -> Iterator[None]:
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            yield
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise

    def save_raw_snapshot(
        self,
        *,
        requested_url: str,
        final_url: str,
        page_type: str,
        navigation_outcome: str,
        title: str,
        captured_at: datetime,
        html: str,
        blocked: bool = False,
        parse_status: str = "pending",
        parse_error: str | None = None,
    ) -> str:
        raw_hash = hashlib.sha256(html.encode()).hexdigest()
        snapshot_id = stable_id(
            "snapshot", requested_url, final_url, raw_hash, int(blocked)
        )
        self.connection.execute(
            """
            INSERT OR IGNORE INTO raw_snapshots (
                snapshot_id, requested_url, final_url, page_type,
                navigation_outcome, browser_title, captured_at, sha256,
                html_zlib, blocked, parser_version, parse_status, parse_error
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot_id,
                requested_url,
                final_url,
                page_type,
                navigation_outcome,
                title,
                iso(captured_at),
                raw_hash,
                zlib.compress(html.encode(), level=9),
                int(blocked),
                PARSER_VERSION,
                parse_status,
                parse_error,
            ),
        )
        self.connection.execute(
            """
            INSERT OR IGNORE INTO snapshot_verifications (
                verification_id, snapshot_id, verified_at,
                navigation_outcome, blocked
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                stable_id(
                    "snapshot_verification",
                    snapshot_id,
                    iso(captured_at),
                    navigation_outcome,
                    int(blocked),
                ),
                snapshot_id,
                iso(captured_at),
                navigation_outcome,
                int(blocked),
            ),
        )
        self.connection.commit()
        return snapshot_id

    def save_parse_result(
        self, snapshot_id: str, status: str, error: str | None = None
    ) -> None:
        self.connection.execute(
            """
            INSERT OR IGNORE INTO raw_parse_attempts (
                parse_attempt_id, snapshot_id, parsed_at, parser_version,
                parse_status, parse_error
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                stable_id(
                    "parse", snapshot_id, PARSER_VERSION, status, error or ""
                ),
                snapshot_id,
                iso(),
                PARSER_VERSION,
                status,
                error,
            ),
        )
        self.connection.commit()

    def save_run(
        self,
        *,
        run_id: str,
        command: str,
        attempted_at: datetime,
        status: str,
        summary: dict[str, Any],
        completed_at: datetime | None = None,
        blocked: bool = False,
        error: str | None = None,
    ) -> None:
        self.connection.execute(
            """
            INSERT OR REPLACE INTO ingestion_runs (
                run_id, command, attempted_at, completed_at, status,
                blocked, summary_json, error
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                command,
                iso(attempted_at),
                iso(completed_at) if completed_at else None,
                status,
                int(blocked),
                canonical_json(summary),
                error,
            ),
        )
        self.connection.commit()

    def insert_model(self, kind: str, model: Any) -> bool:
        table = ENTITY_TABLES[kind]
        data = model.model_dump(mode="json", exclude_none=False)
        snapshot_id = data["source_snapshot_id"]
        observed_at = data["observed_at"]
        if kind == "ranking":
            provider_id = data["team"]["provider_team_id"]
            logical = (
                provider_id,
                data["ranking_date"],
                data["region"],
                data["position"],
            )
            columns = (
                "observation_id, source_snapshot_id, provider_team_id, "
                "ranking_date, region, position, observed_at, data_json"
            )
            values = (
                stable_id("ranking", snapshot_id, *logical),
                snapshot_id,
                provider_id,
                data["ranking_date"],
                data["region"],
                data["position"],
                observed_at,
                canonical_json(data),
            )
        elif kind in {"team", "roster"}:
            provider_id = data["provider_team_id"]
            columns = (
                "observation_id, source_snapshot_id, provider_team_id, "
                "observed_at, data_json"
            )
            values = (
                stable_id(kind, snapshot_id, provider_id),
                snapshot_id,
                provider_id,
                observed_at,
                canonical_json(data),
            )
        elif kind == "match":
            provider_id = data["provider_match_id"]
            columns = (
                "observation_id, source_snapshot_id, provider_match_id, "
                "scheduled_at, status, team_one_id, team_two_id, event_id, "
                "observed_at, data_json"
            )
            values = (
                stable_id(kind, snapshot_id, provider_id),
                snapshot_id,
                provider_id,
                data["scheduled_at_utc"],
                data["status"],
                data["team_one"]["provider_team_id"],
                data["team_two"]["provider_team_id"],
                data["event"]["provider_event_id"] if data["event"] else None,
                observed_at,
                canonical_json(data),
            )
        else:
            provider_id = data["provider_event_id"]
            columns = (
                "observation_id, source_snapshot_id, provider_event_id, "
                "observed_at, data_json"
            )
            values = (
                stable_id(kind, snapshot_id, provider_id),
                snapshot_id,
                provider_id,
                observed_at,
                canonical_json(data),
            )
        cursor = self.connection.execute(
            f"INSERT OR IGNORE INTO {table} ({columns}) "
            f"VALUES ({','.join('?' for _ in values)})",
            values,
        )
        self.connection.commit()
        return cursor.rowcount == 1

    def insert_intelligence(self, kind: str, model: Any) -> bool:
        table = INTELLIGENCE_TABLES[kind]
        data = model.model_dump(mode="json", exclude_none=False)
        state = {
            key: value
            for key, value in data.items()
            if key not in {"observed_at", "source_snapshot_id"}
        }
        state_hash = hashlib.sha256(canonical_json(state).encode()).hexdigest()
        snapshot_id = data["source_snapshot_id"]
        observed_at = data["observed_at"]
        common = {
            "state_hash": state_hash,
            "source_snapshot_id": snapshot_id,
            "observed_at": observed_at,
            "data_json": canonical_json(data),
        }
        if kind == "match_detail":
            logical = (data["provider_match_id"],)
            fields = {
                **common,
                "provider_match_id": data["provider_match_id"],
                "scheduled_at": data["scheduled_at_utc"],
                "status": data["status"],
                "team_one_id": data["team_one"]["provider_team_id"],
                "team_two_id": data["team_two"]["provider_team_id"],
                "event_id": (
                    data["event"]["provider_event_id"] if data["event"] else None
                ),
                "effective_at": data["effective_at"],
            }
        elif kind == "match_lineup":
            logical = (data["provider_match_id"], data["provider_team_id"])
            fields = {
                **common,
                "provider_match_id": data["provider_match_id"],
                "provider_team_id": data["provider_team_id"],
                "effective_at": data["effective_at"],
            }
        elif kind == "match_veto":
            logical = (data["provider_match_id"], data["sequence_number"])
            fields = {
                **common,
                "provider_match_id": data["provider_match_id"],
                "sequence_number": data["sequence_number"],
                "provider_team_id": data["provider_team_id"],
                "canonical_map_id": data["canonical_map_id"],
            }
        elif kind == "map_result":
            logical = (data["provider_match_id"], data["map_order"])
            fields = {
                **common,
                "provider_match_id": data["provider_match_id"],
                "map_order": data["map_order"],
                "canonical_map_id": data["canonical_map_id"],
            }
        elif kind == "team_map_stat":
            logical = (
                data["provider_team_id"],
                data["canonical_map_id"] or data["map_name"],
                data["range_start"],
                data["range_end"],
            )
            fields = {
                **common,
                "provider_team_id": data["provider_team_id"],
                "canonical_map_id": data["canonical_map_id"],
                "range_start": data["range_start"],
                "range_end": data["range_end"],
            }
        elif kind == "team_result":
            logical = (data["provider_team_id"], data["provider_match_id"])
            fields = {
                **common,
                "provider_team_id": data["provider_team_id"],
                "provider_match_id": data["provider_match_id"],
                "opponent_team_id": data["opponent_team_id"],
                "event_id": data["provider_event_id"],
                "match_date": data["match_date"],
            }
        elif kind == "head_to_head":
            logical = (
                data["provider_match_id"],
                data["provider_team_one_id"],
                data["provider_team_two_id"],
            )
            fields = {
                **common,
                "provider_match_id": data["provider_match_id"],
                "provider_team_one_id": data["provider_team_one_id"],
                "provider_team_two_id": data["provider_team_two_id"],
                "event_id": data["provider_event_id"],
                "match_date": data["match_date"],
            }
        else:
            logical = (data["provider_event_id"],)
            fields = {
                **common,
                "provider_event_id": data["provider_event_id"],
                "start_at": data["start_at"],
                "end_at": data["end_at"],
                "effective_at": data["effective_at"],
            }
        observation_id = stable_id(kind, *logical, state_hash)
        fields = {"observation_id": observation_id, **fields}
        columns = tuple(fields)
        cursor = self.connection.execute(
            f"INSERT OR IGNORE INTO {table} ({','.join(columns)}) "
            f"VALUES ({','.join('?' for _ in columns)})",
            tuple(fields[column] for column in columns),
        )
        inserted = cursor.rowcount == 1
        if kind == "match_lineup" and inserted:
            participants = [
                *((player, False) for player in data["players"]),
                *(
                    [(data["coach"], True)]
                    if data.get("coach")
                    else []
                ),
            ]
            for index, (player, coach) in enumerate(participants):
                self.connection.execute(
                    """
                    INSERT OR IGNORE INTO match_lineup_players (
                        lineup_player_id, lineup_observation_id,
                        provider_match_id, provider_team_id,
                        provider_player_id, nickname, role, stand_in, coach
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        stable_id(
                            "lineup_player",
                            observation_id,
                            index,
                            player["provider_player_id"],
                            player["nickname"],
                            int(coach),
                        ),
                        observation_id,
                        data["provider_match_id"],
                        data["provider_team_id"],
                        player["provider_player_id"],
                        player["nickname"],
                        player["status"],
                        (
                            int(player["stand_in"])
                            if player["stand_in"] is not None
                            else None
                        ),
                        int(coach),
                    ),
                )
        self.connection.execute(
            """
            INSERT OR IGNORE INTO normalized_verifications (
                verification_id, observation_kind, observation_id,
                source_snapshot_id, verified_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                stable_id(
                    "normalized_verification",
                    kind,
                    observation_id,
                    snapshot_id,
                    observed_at,
                ),
                kind,
                observation_id,
                snapshot_id,
                observed_at,
            ),
        )
        self.connection.commit()
        return inserted

    def intelligence_latest(
        self,
        kind: str,
        *,
        where: str,
        params: tuple[Any, ...],
        partition_by: str,
        order_by: str,
        observed_before: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        table = INTELLIGENCE_TABLES[kind]
        clauses = [where]
        query_params = list(params)
        if observed_before:
            clauses.append("observed_at <= ?")
            query_params.append(observed_before)
        verification_cutoff = (
            "AND verification.verified_at <= ?" if observed_before else ""
        )
        verification_params: list[Any] = (
            [observed_before] if observed_before else []
        )
        rows = self.connection.execute(
            f"""
            WITH ranked AS (
                SELECT observation_id, data_json, observed_at,
                    ROW_NUMBER() OVER (
                        PARTITION BY {partition_by}
                        ORDER BY observed_at DESC, observation_id DESC
                    ) row_number
                FROM {table}
                WHERE {' AND '.join(clauses)}
            )
            SELECT ranked.observation_id, ranked.data_json,
                (
                    SELECT MAX(verification.verified_at)
                    FROM normalized_verifications verification
                    WHERE verification.observation_kind = ?
                      AND verification.observation_id = ranked.observation_id
                      {verification_cutoff}
                ) last_verified_at
            FROM ranked
            WHERE ranked.row_number = 1
            ORDER BY {order_by}
            LIMIT ? OFFSET ?
            """,
            (
                *query_params,
                kind,
                *verification_params,
                limit,
                offset,
            ),
        ).fetchall()
        result = []
        for row in rows:
            data = json.loads(row["data_json"])
            data["_observation_id"] = row["observation_id"]
            data["_last_verified_at"] = row["last_verified_at"]
            result.append(data)
        return result

    def latest_intelligence_verified_at(
        self, kind: str, id_column: str, provider_id: int
    ) -> datetime | None:
        table = INTELLIGENCE_TABLES[kind]
        row = self.connection.execute(
            f"""
            SELECT MAX(verification.verified_at)
            FROM {table} observation
            JOIN normalized_verifications verification
              ON verification.observation_kind = ?
             AND verification.observation_id = observation.observation_id
            WHERE observation.{id_column} = ?
            """,
            (kind, provider_id),
        ).fetchone()
        return datetime.fromisoformat(row[0]) if row and row[0] else None

    def record_item_result(
        self,
        *,
        run_id: str,
        item_type: str,
        provider_id: int | None,
        requested_url: str,
        attempted_at: datetime,
        completed_at: datetime,
        status: str,
        source_snapshot_id: str | None = None,
        error: Exception | None = None,
    ) -> None:
        error_type = type(error).__name__ if error else None
        error_text = str(error) if error else None
        self.connection.execute(
            """
            INSERT OR IGNORE INTO ingestion_item_results (
                item_result_id, run_id, item_type, provider_id,
                requested_url, attempted_at, completed_at, status,
                source_snapshot_id, error_type, error
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                stable_id(
                    "item_result",
                    run_id,
                    item_type,
                    provider_id,
                    requested_url,
                    iso(attempted_at),
                    status,
                    source_snapshot_id,
                    error_type,
                    error_text,
                ),
                run_id,
                item_type,
                provider_id,
                requested_url,
                iso(attempted_at),
                iso(completed_at),
                status,
                source_snapshot_id,
                error_type,
                error_text,
            ),
        )
        self.connection.commit()

    def acquire_worker_lock(
        self,
        owner_id: str,
        *,
        acquired_at: datetime,
        ttl_seconds: int,
    ) -> bool:
        expires_at = acquired_at.timestamp() + ttl_seconds
        with self.transaction():
            self.connection.execute(
                "DELETE FROM worker_locks WHERE expires_at <= ?",
                (iso(acquired_at),),
            )
            cursor = self.connection.execute(
                """
                INSERT OR IGNORE INTO worker_locks (
                    lock_name, owner_id, acquired_at, expires_at
                ) VALUES ('ingestion', ?, ?, ?)
                """,
                (
                    owner_id,
                    iso(acquired_at),
                    iso(datetime.fromtimestamp(expires_at, tz=UTC)),
                ),
            )
        return cursor.rowcount == 1

    def release_worker_lock(self, owner_id: str) -> None:
        self.connection.execute(
            "DELETE FROM worker_locks WHERE lock_name='ingestion' AND owner_id=?",
            (owner_id,),
        )
        self.connection.commit()

    def list_latest(
        self,
        kind: str,
        *,
        where: str = "",
        params: tuple[Any, ...] = (),
        order_by: str = "observed_at DESC",
        limit: int = 100,
        offset: int = 0,
        partition_by: str,
    ) -> list[dict[str, Any]]:
        table = ENTITY_TABLES[kind]
        predicate = f"WHERE {where}" if where else ""
        rows = self.connection.execute(
            f"""
            SELECT data_json FROM (
                SELECT data_json, observed_at,
                    ROW_NUMBER() OVER (
                        PARTITION BY {partition_by}
                        ORDER BY observed_at DESC, observation_id DESC
                    ) AS row_number
                FROM {table} {predicate}
            )
            WHERE row_number = 1
            ORDER BY {order_by}
            LIMIT ? OFFSET ?
            """,
            (*params, limit, offset),
        ).fetchall()
        return [json.loads(row["data_json"]) for row in rows]

    def history(
        self, kind: str, id_column: str, provider_id: int, limit: int, offset: int
    ) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            f"""
            SELECT data_json FROM {ENTITY_TABLES[kind]}
            WHERE {id_column} = ?
            ORDER BY observed_at DESC, observation_id DESC LIMIT ? OFFSET ?
            """,
            (provider_id, limit, offset),
        ).fetchall()
        return [json.loads(row["data_json"]) for row in rows]

    def evidence(
        self, snapshot_id: str, *, include_html: bool = False
    ) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM raw_snapshots WHERE snapshot_id = ?", (snapshot_id,)
        ).fetchone()
        if row is None:
            return None
        result = dict(row)
        compressed = result.pop("html_zlib")
        result["blocked"] = bool(result["blocked"])
        if include_html:
            result["html"] = zlib.decompress(compressed).decode()
        parsed = self.connection.execute(
            """
            SELECT parse_status, parse_error, parsed_at
            FROM raw_parse_attempts WHERE snapshot_id = ?
            ORDER BY parsed_at DESC LIMIT 1
            """,
            (snapshot_id,),
        ).fetchone()
        if parsed:
            result.update(dict(parsed))
        return result

    def status(self) -> dict[str, Any]:
        attempted = self.connection.execute(
            """
            SELECT * FROM ingestion_runs
            ORDER BY attempted_at DESC, rowid DESC LIMIT 1
            """
        ).fetchone()
        successful = self.connection.execute(
            """
            SELECT * FROM ingestion_runs WHERE status = 'success'
            ORDER BY completed_at DESC, rowid DESC LIMIT 1
            """
        ).fetchone()
        counts = {
            name: self.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for name, table in {
                **ENTITY_TABLES,
                **INTELLIGENCE_TABLES,
            }.items()
        }
        newest = self.connection.execute(
            """
            SELECT MAX(observed_at) FROM (
                SELECT observed_at FROM ranking_observations
                UNION ALL SELECT observed_at FROM team_observations
                UNION ALL SELECT observed_at FROM match_observations
                UNION ALL SELECT observed_at FROM match_detail_observations
                UNION ALL SELECT observed_at FROM team_map_stat_observations
                UNION ALL SELECT observed_at FROM event_detail_observations
            )
            """
        ).fetchone()[0]
        return {
            "database_available": True,
            "most_recent_attempt": dict(attempted) if attempted else None,
            "most_recent_success": dict(successful) if successful else None,
            "blocked": bool(attempted["blocked"]) if attempted else False,
            "newest_observed_at": newest,
            "data_counts": counts,
        }

    def known_team_profiles(self) -> list[dict[str, Any]]:
        candidates: dict[int, dict[str, Any]] = {}
        for item in self.list_latest(
            "ranking",
            limit=10_000,
            partition_by="provider_team_id, ranking_date, region",
        ):
            team = item["team"]
            candidates[team["provider_team_id"]] = {
                "provider_team_id": team["provider_team_id"],
                "name": team["name"],
                "profile_url": team["provider_url"],
            }
        for item in self.list_latest(
            "match", limit=10_000, partition_by="provider_match_id"
        ):
            for key in ("team_one", "team_two"):
                team = item[key]
                if team["provider_team_id"] is not None:
                    candidates[team["provider_team_id"]] = {
                        "provider_team_id": team["provider_team_id"],
                        "name": team["name"],
                        "profile_url": team["provider_url"],
                    }
        return list(candidates.values())

    def latest_team_observed_at(self, provider_team_id: int) -> datetime | None:
        row = self.connection.execute(
            """
            SELECT observed_at FROM team_observations
            WHERE provider_team_id = ? ORDER BY observed_at DESC LIMIT 1
            """,
            (provider_team_id,),
        ).fetchone()
        return datetime.fromisoformat(row[0]) if row else None

    def rankings(
        self,
        *,
        ranking_date: str | None = None,
        region: str | None = None,
        observed_before: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if region:
            clauses.append("region = ?")
            params.append(region)
        if observed_before:
            clauses.append("observed_at <= ?")
            params.append(observed_before)
        if ranking_date is None:
            date_where = " AND ".join(clauses) or "1 = 1"
            row = self.connection.execute(
                f"""
                SELECT MAX(ranking_date) FROM ranking_observations
                WHERE {date_where}
                """,
                tuple(params),
            ).fetchone()
            ranking_date = row[0] if row else None
            if ranking_date is None:
                return []
        clauses.append("ranking_date = ?")
        params.append(ranking_date)
        return self.list_latest(
            "ranking",
            where=" AND ".join(clauses),
            params=tuple(params),
            order_by="json_extract(data_json, '$.position') ASC",
            limit=limit,
            offset=offset,
            partition_by="provider_team_id, ranking_date, region",
        )

    def matches(
        self,
        *,
        status: str | None = None,
        from_timestamp: str | None = None,
        to_timestamp: str | None = None,
        team_id: int | None = None,
        event_id: int | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if status:
            clauses.append("status = ?")
            params.append(status)
        if from_timestamp:
            clauses.append("scheduled_at >= ?")
            params.append(from_timestamp)
        if to_timestamp:
            clauses.append("scheduled_at <= ?")
            params.append(to_timestamp)
        if team_id is not None:
            clauses.append("(team_one_id = ? OR team_two_id = ?)")
            params.extend((team_id, team_id))
        if event_id is not None:
            clauses.append("event_id = ?")
            params.append(event_id)
        return self.list_latest(
            "match",
            where=" AND ".join(clauses),
            params=tuple(params),
            order_by="COALESCE(json_extract(data_json, '$.scheduled_at_utc'), '') ASC",
            limit=limit,
            offset=offset,
            partition_by="provider_match_id",
        )

    def latest_entity(
        self, kind: str, id_column: str, provider_id: int
    ) -> dict[str, Any] | None:
        rows = self.history(kind, id_column, provider_id, 1, 0)
        return rows[0] if rows else None

    def match_detail(
        self, provider_match_id: int, observed_before: str | None = None
    ) -> list[dict[str, Any]]:
        return self.intelligence_latest(
            "match_detail",
            where="provider_match_id = ?",
            params=(provider_match_id,),
            partition_by="provider_match_id",
            order_by="observed_at DESC",
            observed_before=observed_before,
            limit=1,
        )

    def match_lineups(
        self, provider_match_id: int, observed_before: str | None = None
    ) -> list[dict[str, Any]]:
        return self.intelligence_latest(
            "match_lineup",
            where="provider_match_id = ?",
            params=(provider_match_id,),
            partition_by="provider_team_id",
            order_by="json_extract(data_json, '$.provider_team_id') ASC",
            observed_before=observed_before,
        )

    def match_vetoes(
        self, provider_match_id: int, observed_before: str | None = None
    ) -> list[dict[str, Any]]:
        return self.intelligence_latest(
            "match_veto",
            where="provider_match_id = ?",
            params=(provider_match_id,),
            partition_by="sequence_number",
            order_by="json_extract(data_json, '$.sequence_number') ASC",
            observed_before=observed_before,
        )

    def match_maps(
        self, provider_match_id: int, observed_before: str | None = None
    ) -> list[dict[str, Any]]:
        return self.intelligence_latest(
            "map_result",
            where="provider_match_id = ?",
            params=(provider_match_id,),
            partition_by="map_order",
            order_by="json_extract(data_json, '$.map_order') ASC",
            observed_before=observed_before,
        )

    def team_map_stats(
        self,
        provider_team_id: int,
        observed_before: str | None = None,
    ) -> list[dict[str, Any]]:
        return self.intelligence_latest(
            "team_map_stat",
            where="provider_team_id = ?",
            params=(provider_team_id,),
            partition_by=(
                "COALESCE(canonical_map_id, "
                "json_extract(data_json, '$.map_name'))"
            ),
            order_by="json_extract(data_json, '$.map_name') ASC",
            observed_before=observed_before,
        )

    def team_results(
        self,
        provider_team_id: int,
        observed_before: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        return self.intelligence_latest(
            "team_result",
            where="provider_team_id = ?",
            params=(provider_team_id,),
            partition_by="provider_match_id",
            order_by="json_extract(data_json, '$.match_date') DESC",
            observed_before=observed_before,
            limit=limit,
            offset=offset,
        )

    def head_to_head(
        self,
        team_one_id: int,
        team_two_id: int,
        observed_before: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        low, high = sorted((team_one_id, team_two_id))
        return self.intelligence_latest(
            "head_to_head",
            where=(
                "MIN(provider_team_one_id, provider_team_two_id) = ? "
                "AND MAX(provider_team_one_id, provider_team_two_id) = ?"
            ),
            params=(low, high),
            partition_by="provider_match_id",
            order_by="json_extract(data_json, '$.match_date') DESC",
            observed_before=observed_before,
            limit=limit,
            offset=offset,
        )

    def event_detail(
        self, provider_event_id: int, observed_before: str | None = None
    ) -> list[dict[str, Any]]:
        return self.intelligence_latest(
            "event_detail",
            where="provider_event_id = ?",
            params=(provider_event_id,),
            partition_by="provider_event_id",
            order_by="observed_at DESC",
            observed_before=observed_before,
            limit=1,
        )

    def known_event_profiles(self) -> list[dict[str, Any]]:
        candidates: dict[int, dict[str, Any]] = {}
        for item in self.list_latest(
            "match", limit=10_000, partition_by="provider_match_id"
        ):
            event = item.get("event")
            if event and event.get("provider_event_id"):
                candidates[event["provider_event_id"]] = {
                    "provider_event_id": event["provider_event_id"],
                    "name": event.get("name"),
                    "provider_url": event.get("provider_url"),
                }
        return list(candidates.values())

    def has_records(self, kind: str) -> bool:
        tables = {**ENTITY_TABLES, **INTELLIGENCE_TABLES}
        row = self.connection.execute(
            f"SELECT EXISTS(SELECT 1 FROM {tables[kind]} LIMIT 1)"
        ).fetchone()
        return bool(row[0])
