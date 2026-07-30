from datetime import UTC, datetime

from fastapi.testclient import TestClient

from hltv_service.app import create_app
from hltv_service.config import ServiceConfig
from hltv_service.schemas import (
    Match,
    MatchTeam,
    RankingEntry,
    RankingTeam,
    RosterPlayer,
    RosterSnapshot,
    Team,
)
from hltv_service.storage import Storage


def cached_match(store, *, observed_at):
    snapshot = store.save_raw_snapshot(
        requested_url="https://www.hltv.org/matches",
        final_url="https://www.hltv.org/matches",
        page_type="matches",
        navigation_outcome="success",
        title="HLTV.org",
        captured_at=observed_at,
        html="<main>fixture</main>",
    )
    model = Match(
        provider_match_id=2400001,
        status="upcoming",
        scheduled_at_utc=datetime(2026, 8, 1, 12, tzinfo=UTC),
        team_one=MatchTeam(provider_team_id=1001, name="Alpha"),
        team_two=MatchTeam(provider_team_id=1002, name="Beta"),
        best_of=3,
        stars=2,
        match_url="https://www.hltv.org/matches/2400001/alpha-vs-beta",
        observed_at=observed_at,
        data_completeness={"identity": True},
        source_snapshot_id=snapshot,
    )
    store.insert_model("match", model)
    return snapshot


def cached_team_and_ranking(store, *, observed_at):
    snapshot = store.save_raw_snapshot(
        requested_url="https://www.hltv.org/team/1001/alpha",
        final_url="https://www.hltv.org/team/1001/alpha",
        page_type="team",
        navigation_outcome="success",
        title="Alpha | HLTV.org",
        captured_at=observed_at,
        html="<main>team fixture</main>",
    )
    player = RosterPlayer(provider_player_id=501, nickname="alpha-one")
    team = Team(
        provider_team_id=1001,
        name="Alpha",
        profile_url="https://www.hltv.org/team/1001/alpha",
        roster=[player],
        observed_at=observed_at,
        data_completeness={"identity": True},
        source_snapshot_id=snapshot,
    )
    store.insert_model("team", team)
    store.insert_model(
        "roster",
        RosterSnapshot(
            provider_team_id=1001,
            roster=[player],
            observed_at=observed_at,
            data_completeness={"provider_ids": True},
            source_snapshot_id=snapshot,
        ),
    )
    ranking_snapshot = store.save_raw_snapshot(
        requested_url="https://www.hltv.org/ranking/teams",
        final_url="https://www.hltv.org/ranking/teams",
        page_type="rankings",
        navigation_outcome="success",
        title="Rankings | HLTV.org",
        captured_at=observed_at,
        html="<main>ranking fixture</main>",
    )
    store.insert_model(
        "ranking",
        RankingEntry(
            ranking_date=datetime(2026, 7, 27, tzinfo=UTC),
            region="World",
            position=1,
            points=925,
            team=RankingTeam(
                provider_team_id=1001,
                name="Alpha",
                provider_url="https://www.hltv.org/team/1001/alpha",
            ),
            observed_at=observed_at,
            source_snapshot_id=ranking_snapshot,
        ),
    )


def test_api_contract_authentication_filters_and_evidence_redaction(tmp_path):
    store = Storage(tmp_path / "service.sqlite")
    snapshot = cached_match(store, observed_at=datetime.now(UTC))
    app = create_app(
        ServiceConfig(
            database_path=store.path,
            api_token="internal-secret",
            max_stale_seconds=60,
        ),
        store,
    )
    client = TestClient(app)
    assert client.get("/health").status_code == 200
    assert client.get("/v1/status").status_code == 401
    headers = {"Authorization": "Bearer internal-secret", "X-Request-ID": "test-1"}
    response = client.get(
        "/v1/matches?status=upcoming&team_id=1001&limit=10", headers=headers
    )
    assert response.status_code == 200
    body = response.json()
    assert body["schema_version"] == "1.0"
    assert body["data"][0]["provider"] == "hltv"
    assert body["data"][0]["provider_match_id"] == 2400001
    assert body["meta"]["is_stale"] is False
    assert response.headers["x-request-id"] == "test-1"
    evidence = client.get(f"/v1/evidence/{snapshot}", headers=headers).json()["data"]
    assert evidence["sha256"]
    assert "html" not in evidence
    assert client.get("/v1/matches?limit=0", headers=headers).status_code == 422
    assert client.get(
        "/v1/matches?from=2026-08-02T00:00:00Z&to=2026-08-01T00:00:00Z",
        headers=headers,
    ).status_code == 422
    assert client.get(
        "/v1/matches?from=2026-08-01T00:00:00", headers=headers
    ).status_code == 422
    store.close()


def test_stale_cache_is_returned_and_missing_cache_uses_503(tmp_path):
    store = Storage(tmp_path / "service.sqlite")
    cached_match(store, observed_at=datetime(2020, 1, 1, tzinfo=UTC))
    client = TestClient(
        create_app(
            ServiceConfig(database_path=store.path, max_stale_seconds=1), store
        )
    )
    response = client.get("/v1/matches")
    assert response.status_code == 200
    assert response.json()["meta"]["is_stale"] is True
    empty = Storage(tmp_path / "empty.sqlite")
    empty_client = TestClient(create_app(ServiceConfig(database_path=empty.path), empty))
    assert empty_client.get("/v1/matches").status_code == 503
    store.close()
    empty.close()


def test_raw_evidence_requires_explicit_development_setting(tmp_path):
    store = Storage(tmp_path / "service.sqlite")
    snapshot = cached_match(store, observed_at=datetime.now(UTC))
    client = TestClient(
        create_app(
            ServiceConfig(database_path=store.path, allow_raw_evidence=True), store
        )
    )
    response = client.get(f"/v1/evidence/{snapshot}")
    assert response.json()["data"]["html"] == "<main>fixture</main>"
    store.close()


def test_status_rankings_team_history_roster_and_detail_contracts(tmp_path):
    store = Storage(tmp_path / "service.sqlite")
    now = datetime.now(UTC)
    cached_match(store, observed_at=now)
    cached_team_and_ranking(store, observed_at=now)
    client = TestClient(create_app(ServiceConfig(database_path=store.path), store))
    status = client.get("/v1/status")
    assert status.status_code == 200
    assert status.json()["data"]["data_counts"]["team"] == 1
    rankings = client.get("/v1/rankings?region=World")
    assert rankings.status_code == 200
    assert rankings.json()["data"][0]["team"]["provider_team_id"] == 1001
    dated = client.get("/v1/rankings/2026-07-27")
    assert dated.status_code == 200
    assert dated.json()["data"][0]["position"] == 1
    assert client.get("/v1/rankings?region=Nowhere").json()["data"] == []
    assert client.get("/v1/matches?status=live").json()["data"] == []
    assert client.get("/v1/matches/2400001").status_code == 200
    assert client.get("/v1/matches/9999999").status_code == 404
    assert client.get("/v1/teams/1001").json()["data"]["name"] == "Alpha"
    assert len(client.get("/v1/teams/1001/history").json()["data"]) == 1
    roster = client.get("/v1/teams/1001/roster").json()["data"]
    assert roster["roster"][0]["provider_player_id"] == 501
    assert client.get("/v1/teams/9999").status_code == 404
    assert client.get("/v1/teams/9999/history").status_code == 404
    assert client.get("/v1/teams/9999/roster").status_code == 404
    assert client.get("/v1/evidence/missing").status_code == 404
    store.close()
