import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from hltv_service.parsers_v2 import parse_match_intelligence
from hltv_service.storage import SCHEMA_VERSION, Storage

FIXTURES = Path(__file__).parent / "fixtures"
URL = "https://www.hltv.org/matches/2400001/alpha-vs-beta"
FIRST = datetime(2026, 7, 31, 10, tzinfo=UTC)
LATER = FIRST + timedelta(hours=1)


def html(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def snapshot(
    store: Storage, captured_at: datetime, content: str, page_type: str = "match_detail"
) -> str:
    return store.save_raw_snapshot(
        requested_url=URL,
        final_url=URL,
        page_type=page_type,
        navigation_outcome="success",
        title="Sanitized fixture",
        captured_at=captured_at,
        html=content,
    )


def test_v1_database_migrates_to_append_only_intelligence_schema(tmp_path) -> None:
    database = tmp_path / "migration.sqlite"
    connection = sqlite3.connect(database)
    connection.executescript(
        """
        CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT);
        INSERT INTO schema_migrations VALUES(1, '2026-07-30T00:00:00+00:00');
        PRAGMA user_version=1;
        """
    )
    connection.close()

    store = Storage(database)
    tables = {
        row[0]
        for row in store.connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }

    assert (
        store.connection.execute("PRAGMA user_version").fetchone()[0]
        == SCHEMA_VERSION
    )
    assert {
        "match_detail_observations",
        "match_lineup_observations",
        "match_veto_observations",
        "map_result_observations",
        "team_map_stat_observations",
        "team_result_observations",
        "head_to_head_observations",
        "event_detail_observations",
        "snapshot_verifications",
        "normalized_verifications",
    }.issubset(tables)
    store.close()


def test_identical_state_deduplicates_but_verification_freshness_advances(
    tmp_path,
) -> None:
    store = Storage(tmp_path / "freshness.sqlite")
    content = html("match-detail-upcoming.html")
    first_snapshot = snapshot(store, FIRST, content)
    first = parse_match_intelligence(
        content,
        url=URL,
        observed_at=FIRST,
        source_snapshot_id=first_snapshot,
    )
    assert store.insert_intelligence("match_detail", first.detail) is True

    repeated_snapshot = snapshot(store, LATER, content)
    repeated = parse_match_intelligence(
        content,
        url=URL,
        observed_at=LATER,
        source_snapshot_id=repeated_snapshot,
    )
    assert repeated_snapshot == first_snapshot
    assert store.insert_intelligence("match_detail", repeated.detail) is False
    assert (
        store.connection.execute(
            "SELECT COUNT(*) FROM match_detail_observations"
        ).fetchone()[0]
        == 1
    )
    assert (
        store.connection.execute(
            "SELECT COUNT(*) FROM snapshot_verifications"
        ).fetchone()[0]
        == 2
    )
    record = store.match_detail(2400001)[0]
    assert datetime.fromisoformat(record["_last_verified_at"]) == LATER
    store.close()


def test_later_changed_state_is_append_only_and_point_in_time_queryable(
    tmp_path,
) -> None:
    store = Storage(tmp_path / "history.sqlite")
    upcoming_html = html("match-detail-upcoming.html")
    upcoming_snapshot = snapshot(store, FIRST, upcoming_html)
    upcoming = parse_match_intelligence(
        upcoming_html,
        url=URL,
        observed_at=FIRST,
        source_snapshot_id=upcoming_snapshot,
    )
    store.insert_intelligence("match_detail", upcoming.detail)

    live_html = html("match-detail-live.html")
    live_snapshot = snapshot(store, LATER, live_html)
    live = parse_match_intelligence(
        live_html,
        url=URL,
        observed_at=LATER,
        source_snapshot_id=live_snapshot,
    )
    assert store.insert_intelligence("match_detail", live.detail) is True

    earlier = store.match_detail(
        2400001, (FIRST + timedelta(minutes=30)).isoformat()
    )[0]
    current = store.match_detail(2400001)[0]
    assert earlier["status"] == "upcoming"
    assert current["status"] == "live"
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        store.connection.execute(
            "UPDATE match_detail_observations SET status='finished'"
        )
    store.close()


def test_match_lineup_player_ids_are_indexed_without_fabrication(tmp_path) -> None:
    store = Storage(tmp_path / "lineups.sqlite")
    content = html("match-detail-upcoming.html")
    source = snapshot(store, FIRST, content)
    parsed = parse_match_intelligence(
        content,
        url=URL,
        observed_at=FIRST,
        source_snapshot_id=source,
    )
    for lineup in parsed.lineups:
        store.insert_intelligence("match_lineup", lineup)

    rows = store.connection.execute(
        """
        SELECT provider_player_id, nickname FROM match_lineup_players
        WHERE provider_match_id=2400001 ORDER BY nickname
        """
    ).fetchall()
    unresolved = next(row for row in rows if row["nickname"] == "unresolved-b3")
    assert unresolved["provider_player_id"] is None
    assert any(row["provider_player_id"] == 11001 for row in rows)
    store.close()


def test_worker_lock_rejects_overlap_and_recovers_after_expiry(tmp_path) -> None:
    store = Storage(tmp_path / "lock.sqlite")
    assert store.acquire_worker_lock("one", acquired_at=FIRST, ttl_seconds=300)
    assert not store.acquire_worker_lock(
        "two", acquired_at=FIRST + timedelta(seconds=1), ttl_seconds=300
    )
    assert store.acquire_worker_lock(
        "two", acquired_at=FIRST + timedelta(seconds=301), ttl_seconds=300
    )
    store.release_worker_lock("two")
    assert store.connection.execute("SELECT COUNT(*) FROM worker_locks").fetchone()[0] == 0
    store.close()
