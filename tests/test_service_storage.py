from datetime import UTC, datetime

import pytest

from hltv_service.schemas import RankingEntry, RankingTeam
from hltv_service.storage import SCHEMA_VERSION, Storage


def ranking(snapshot_id: str = "snapshot_fixture") -> RankingEntry:
    return RankingEntry(
        ranking_date=datetime(2026, 7, 27, tzinfo=UTC),
        region="World",
        position=1,
        points=925,
        team=RankingTeam(
            provider_team_id=1001,
            name="Alpha",
            provider_url="https://www.hltv.org/team/1001/alpha",
        ),
        observed_at=datetime(2026, 7, 30, 12, tzinfo=UTC),
        source_snapshot_id=snapshot_id,
    )


def test_migration_creates_versioned_append_only_schema(tmp_path):
    store = Storage(tmp_path / "service.sqlite")
    assert store.connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    tables = {
        row[0]
        for row in store.connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    assert {
        "raw_snapshots",
        "raw_parse_attempts",
        "ranking_observations",
        "team_observations",
        "roster_observations",
        "match_observations",
        "event_observations",
    } <= tables
    store.close()


def test_raw_evidence_is_persisted_before_parse_and_html_is_gated(tmp_path):
    store = Storage(tmp_path / "service.sqlite")
    snapshot_id = store.save_raw_snapshot(
        requested_url="https://www.hltv.org/ranking/teams",
        final_url="https://www.hltv.org/ranking/teams",
        page_type="rankings",
        navigation_outcome="success",
        title="Ranking | HLTV.org",
        captured_at=datetime(2026, 7, 30, tzinfo=UTC),
        html="<main>evidence</main>",
    )
    pending = store.evidence(snapshot_id)
    assert pending["parse_status"] == "pending"
    assert "html" not in pending
    store.save_parse_result(snapshot_id, "failed", "fixture parse failure")
    failed = store.evidence(snapshot_id, include_html=True)
    assert failed["parse_status"] == "failed"
    assert failed["parse_error"] == "fixture parse failure"
    assert failed["html"] == "<main>evidence</main>"
    with pytest.raises(Exception, match="append-only"):
        store.connection.execute(
            "UPDATE raw_snapshots SET parse_status='success'"
        )
    store.close()


def test_normalized_inserts_are_idempotent_and_historical_versions_append(tmp_path):
    store = Storage(tmp_path / "service.sqlite")
    first_snapshot = store.save_raw_snapshot(
        requested_url="https://www.hltv.org/ranking/teams",
        final_url="https://www.hltv.org/ranking/teams",
        page_type="rankings",
        navigation_outcome="success",
        title="HLTV.org",
        captured_at=datetime(2026, 7, 30, tzinfo=UTC),
        html="<main>first</main>",
    )
    first = ranking(first_snapshot)
    assert store.insert_model("ranking", first)
    assert not store.insert_model("ranking", first)
    second_snapshot = store.save_raw_snapshot(
        requested_url="https://www.hltv.org/ranking/teams",
        final_url="https://www.hltv.org/ranking/teams",
        page_type="rankings",
        navigation_outcome="success",
        title="HLTV.org",
        captured_at=datetime(2026, 7, 31, tzinfo=UTC),
        html="<main>changed</main>",
    )
    assert store.insert_model(
        "ranking",
        ranking(second_snapshot).model_copy(
            update={"observed_at": datetime(2026, 7, 31, 12, tzinfo=UTC)}
        ),
    )
    assert len(store.history("ranking", "provider_team_id", 1001, 10, 0)) == 2
    store.close()
