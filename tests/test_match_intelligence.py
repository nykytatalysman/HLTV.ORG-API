from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from HLTV.exceptions import HLTVDeletedError, HLTVParseError
from hltv_service import parsers_v2
from hltv_service.parsers_v2 import (
    parse_event_detail,
    parse_match_intelligence,
    parse_team_map_stats,
)

FIXTURES = Path(__file__).parent / "fixtures"
OBSERVED_AT = datetime(2026, 7, 31, 10, tzinfo=UTC)
MATCH_URL = "https://www.hltv.org/matches/2400001/alpha-vs-beta"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("name", "status", "best_of"),
    (
        ("match-detail-upcoming.html", "upcoming", 3),
        ("match-detail-live.html", "live", 1),
        ("match-detail-finished.html", "finished", 5),
        ("match-detail-postponed.html", "postponed", 3),
        ("match-detail-cancelled.html", "cancelled", 3),
    ),
)
def test_match_lifecycle_and_formats_are_distinct(
    name: str, status: str, best_of: int
) -> None:
    parsed = parse_match_intelligence(
        fixture(name),
        url=MATCH_URL,
        observed_at=OBSERVED_AT,
        source_snapshot_id="snapshot",
    )
    assert parsed.detail.status == status
    assert parsed.detail.best_of == best_of


def test_upcoming_match_preserves_ids_lineups_veto_and_bounded_history() -> None:
    parsed = parse_match_intelligence(
        fixture("match-detail-upcoming.html"),
        url=MATCH_URL,
        observed_at=OBSERVED_AT,
        source_snapshot_id="snapshot",
    )

    assert parsed.detail.provider_match_id == 2400001
    assert parsed.detail.team_one.provider_team_id == 1001
    assert parsed.detail.team_two.provider_team_id == 1002
    assert parsed.detail.event.provider_event_id == 7001
    assert parsed.detail.team_one.displayed_rank == 4
    assert parsed.detail.team_one.map_advantage == 1
    assert parsed.detail.lan is True
    assert parsed.detail.stars == 3
    assert len(parsed.lineups) == 2
    assert parsed.lineups[0].players[2].stand_in is True
    unresolved = next(
        player
        for player in parsed.lineups[1].players
        if player.nickname == "unresolved-b3"
    )
    assert unresolved.provider_player_id is None
    assert unresolved.identity_state == "unresolved"
    assert [action.sequence_number for action in parsed.vetoes] == list(
        range(1, 8)
    )
    assert parsed.vetoes[0].provider_team_id == 1001
    assert parsed.vetoes[-1].action == "remaining"
    assert parsed.vetoes[-1].canonical_map_id == "inferno"
    assert [item.provider_match_id for item in parsed.recent_results] == [
        2399001,
        2399002,
    ]
    assert parsed.head_to_head[0].provider_match_id == 2398000
    assert parsed.head_to_head[0].source_limit == (
        "displayed_hltv_match_page_sample"
    )


def test_finished_map_results_preserve_halves_and_explicit_overtime() -> None:
    parsed = parse_match_intelligence(
        fixture("match-detail-finished.html"),
        url=MATCH_URL,
        observed_at=OBSERVED_AT,
        source_snapshot_id="snapshot",
    )

    assert parsed.detail.result.winner_team_id == 1001
    assert (parsed.detail.result.team_one_score, parsed.detail.result.team_two_score) == (
        3,
        1,
    )
    assert parsed.maps[0].canonical_map_id == "mirage"
    assert parsed.maps[0].overtime is True
    assert parsed.maps[0].half_time_scores == [(6, 6), (6, 6), (4, 2)]
    assert parsed.maps[0].winner_team_id == 1001


def test_deleted_and_broken_match_pages_have_distinct_errors() -> None:
    with pytest.raises(HLTVDeletedError):
        parse_match_intelligence(
            fixture("match-detail-deleted.html"),
            url=MATCH_URL,
            observed_at=OBSERVED_AT,
            source_snapshot_id="snapshot",
        )
    with pytest.raises(HLTVParseError) as error:
        parse_match_intelligence(
            "<html><body><p>ordinary page</p></body></html>",
            url=MATCH_URL,
            observed_at=OBSERVED_AT,
            source_snapshot_id="snapshot",
        )
    assert error.value.parse_state == "unexpected_layout"


def test_optional_section_failure_reduces_completeness(monkeypatch) -> None:
    def broken_veto(*_args, **_kwargs):
        raise ValueError("selector drift")

    monkeypatch.setattr(parsers_v2, "parse_match_vetoes", broken_veto)
    parsed = parse_match_intelligence(
        fixture("match-detail-upcoming.html"),
        url=MATCH_URL,
        observed_at=OBSERVED_AT,
        source_snapshot_id="snapshot",
    )

    assert parsed.detail.provider_match_id == 2400001
    assert parsed.vetoes == []
    assert parsed.detail.data_completeness["veto"] is False
    assert "selector drift" in parsed.detail.section_errors["veto"]


def test_team_map_stats_are_bounded_and_unknown_maps_remain_unresolved() -> None:
    html = fixture("team-map-stats.html").replace("Nuke", "New Experimental Map")
    records = parse_team_map_stats(
        html,
        provider_team_id=1001,
        range_start=OBSERVED_AT - timedelta(days=90),
        range_end=OBSERVED_AT,
        observed_at=OBSERVED_AT,
        source_snapshot_id="snapshot",
    )

    assert records[0].canonical_map_id == "mirage"
    assert records[0].matches_played == 12
    assert records[0].win_rate == pytest.approx(0.667)
    assert records[1].canonical_map_id is None
    assert records[1].map_name == "New Experimental Map"


def test_event_detail_preserves_raw_and_reliably_normalized_prize_pool() -> None:
    event = parse_event_detail(
        fixture("event-detail.html"),
        url="https://www.hltv.org/events/7001/summer-open",
        observed_at=OBSERVED_AT,
        source_snapshot_id="snapshot",
    )

    assert event.provider_event_id == 7001
    assert event.prize_pool_raw == "$250,000"
    assert event.prize_pool_currency == "USD"
    assert event.prize_pool_amount == 250_000
    assert event.lan is True
    assert [team.provider_team_id for team in event.participating_teams] == [
        1001,
        1002,
    ]


def test_rematches_keep_distinct_stable_match_ids() -> None:
    first = parse_match_intelligence(
        fixture("match-detail-upcoming.html"),
        url=MATCH_URL,
        observed_at=OBSERVED_AT,
        source_snapshot_id="first",
    )
    second = parse_match_intelligence(
        fixture("match-detail-upcoming.html"),
        url="https://www.hltv.org/matches/2400002/alpha-vs-beta-rematch",
        observed_at=OBSERVED_AT,
        source_snapshot_id="second",
    )
    assert first.detail.provider_match_id != second.detail.provider_match_id

