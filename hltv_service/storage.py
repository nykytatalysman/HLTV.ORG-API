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

SCHEMA_VERSION = 1
ENTITY_TABLES = {
    "ranking": "ranking_observations",
    "team": "team_observations",
    "roster": "roster_observations",
    "match": "match_observations",
    "event": "event_observations",
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
            CREATE INDEX IF NOT EXISTS rankings_lookup
                ON ranking_observations(ranking_date, region, position, observed_at);
            CREATE INDEX IF NOT EXISTS teams_lookup
                ON team_observations(provider_team_id, observed_at);
            CREATE INDEX IF NOT EXISTS matches_lookup
                ON match_observations(provider_match_id, scheduled_at, observed_at);
            CREATE INDEX IF NOT EXISTS events_lookup
                ON event_observations(provider_event_id, observed_at);
            """
        )
        for table in ("raw_snapshots", "raw_parse_attempts", *ENTITY_TABLES.values()):
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
            "SELECT * FROM ingestion_runs ORDER BY attempted_at DESC LIMIT 1"
        ).fetchone()
        successful = self.connection.execute(
            """
            SELECT * FROM ingestion_runs WHERE status = 'success'
            ORDER BY completed_at DESC LIMIT 1
            """
        ).fetchone()
        counts = {
            name: self.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for name, table in ENTITY_TABLES.items()
        }
        newest = self.connection.execute(
            """
            SELECT MAX(observed_at) FROM (
                SELECT observed_at FROM ranking_observations
                UNION ALL SELECT observed_at FROM team_observations
                UNION ALL SELECT observed_at FROM match_observations
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

    def has_records(self, kind: str) -> bool:
        row = self.connection.execute(
            f"SELECT EXISTS(SELECT 1 FROM {ENTITY_TABLES[kind]} LIMIT 1)"
        ).fetchone()
        return bool(row[0])
