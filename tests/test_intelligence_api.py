from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from hltv_service.app import create_app
from hltv_service.config import ServiceConfig
from hltv_service.parsers_v2 import (
    parse_event_detail,
    parse_match_intelligence,
    parse_team_map_stats,
)
from hltv_service.storage import Storage

FIXTURES = Path(__file__).parent / "fixtures"
URL = "https://www.hltv.org/matches/2400001/alpha-vs-beta"
FIRST = datetime(2026, 7, 31, 10, tzinfo=UTC)
LATER = FIRST + timedelta(hours=1)


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def raw(store: Storage, url: str, content: str, captured_at: datetime) -> str:
    return store.save_raw_snapshot(
        requested_url=url,
        final_url=url,
        page_type="fixture",
        navigation_outcome="success",
        title="Fixture",
        captured_at=captured_at,
        html=content,
    )


def populated(tmp_path) -> tuple[TestClient, Storage]:
    store = Storage(tmp_path / "api-v2.sqlite")
    match_html = fixture("match-detail-upcoming.html")
    source = raw(store, URL, match_html, FIRST)
    parsed = parse_match_intelligence(
        match_html,
        url=URL,
        observed_at=FIRST,
        source_snapshot_id=source,
    )
    for kind, records in (
        ("match_detail", [parsed.detail]),
        ("match_lineup", parsed.lineups),
        ("match_veto", parsed.vetoes),
        ("map_result", parsed.maps),
        ("team_result", parsed.recent_results),
        ("head_to_head", parsed.head_to_head),
    ):
        for record in records:
            store.insert_intelligence(kind, record)
    stats_html = fixture("team-map-stats.html")
    stats_source = raw(
        store,
        "https://www.hltv.org/stats/teams/maps/1001/-",
        stats_html,
        FIRST,
    )
    for record in parse_team_map_stats(
        stats_html,
        provider_team_id=1001,
        range_start=FIRST - timedelta(days=90),
        range_end=FIRST,
        observed_at=FIRST,
        source_snapshot_id=stats_source,
    ):
        store.insert_intelligence("team_map_stat", record)
    event_html = fixture("event-detail.html")
    event_url = "https://www.hltv.org/events/7001/summer-open"
    event_source = raw(store, event_url, event_html, FIRST)
    store.insert_intelligence(
        "event_detail",
        parse_event_detail(
            event_html,
            url=event_url,
            observed_at=FIRST,
            source_snapshot_id=event_source,
        ),
    )
    config = ServiceConfig(
        database_path=store.path,
        api_token="test-token",
        max_stale_seconds=10**9,
    )
    return TestClient(create_app(config=config, storage=store)), store


def get(client: TestClient, path: str):
    return client.get(path, headers={"Authorization": "Bearer test-token"})


def test_v2_contract_exposes_cached_evidence_and_last_verification(tmp_path) -> None:
    client, store = populated(tmp_path)

    detail = get(client, "/v2/matches/2400001")
    assert detail.status_code == 200
    assert detail.json()["schema_version"] == "2.0"
    assert detail.json()["data"]["provider_match_id"] == 2400001
    assert detail.json()["meta"]["last_verified_at"]
    assert detail.json()["meta"]["evidence_references"]
    assert get(client, "/v2/matches/2400001/lineups").json()["data"][0][
        "provider_team_id"
    ] == 1001
    assert get(client, "/v2/matches/2400001/veto").json()["data"][0][
        "sequence_number"
    ] == 1
    assert len(get(client, "/v2/matches/2400001/maps").json()["data"]) == 3
    assert len(get(client, "/v2/teams/1001/map-stats").json()["data"]) == 2
    assert get(client, "/v2/teams/1001/results").json()["data"][0][
        "provider_match_id"
    ] == 2399001
    assert get(
        client, "/v2/head-to-head?team-one-id=1001&team-two-id=1002"
    ).json()["data"][0]["provider_match_id"] == 2398000
    assert get(client, "/v2/events/7001").json()["data"]["name"] == (
        "Summer Open 2026"
    )
    store.close()


def test_v2_authentication_parameters_and_unavailable_cache(tmp_path) -> None:
    client, store = populated(tmp_path)

    assert client.get("/v2/matches/2400001").status_code == 401
    assert get(client, "/v2/matches/0").status_code == 422
    assert get(
        client, "/v2/head-to-head?team-one-id=1001&team-two-id=1001"
    ).status_code == 422
    assert get(client, "/v2/events/9999").status_code == 503
    store.close()


def test_v2_observed_before_cannot_see_later_match_state(tmp_path) -> None:
    client, store = populated(tmp_path)
    live_html = fixture("match-detail-live.html")
    live_source = raw(store, URL, live_html, LATER)
    live = parse_match_intelligence(
        live_html,
        url=URL,
        observed_at=LATER,
        source_snapshot_id=live_source,
    )
    store.insert_intelligence("match_detail", live.detail)

    current = get(client, "/v2/matches/2400001").json()["data"]
    cutoff = (FIRST + timedelta(minutes=30)).isoformat().replace("+00:00", "Z")
    earlier = get(
        client, f"/v2/matches/2400001?observed-before={cutoff}"
    ).json()["data"]
    assert current["status"] == "live"
    assert earlier["status"] == "upcoming"
    assert earlier["result"] is None
    store.close()
