from datetime import datetime

import pytest
from pydantic import ValidationError

from hltv_service.schemas import (
    Match,
    MatchTeam,
    RankingEntry,
    RankingTeam,
    RosterPlayer,
)


def test_unknown_player_id_remains_null_and_is_not_invented():
    player = RosterPlayer(nickname="TBA")
    assert player.provider_player_id is None
    assert player.player_url is None


def test_service_models_reject_timezone_less_observations():
    with pytest.raises(ValidationError, match="timezone"):
        RankingEntry(
            ranking_date=datetime.fromisoformat("2026-07-27T00:00:00"),
            region="World",
            position=1,
            team=RankingTeam(
                provider_team_id=1,
                name="A",
                provider_url="https://www.hltv.org/team/1/a",
            ),
            observed_at=datetime.fromisoformat("2026-07-30T00:00:00"),
            source_snapshot_id="snapshot",
        )


def test_service_models_reject_timezone_less_domain_timestamps():
    observed_at = datetime.fromisoformat("2026-07-30T00:00:00+00:00")
    ranking_team = RankingTeam(
        provider_team_id=1,
        name="A",
        provider_url="https://www.hltv.org/team/1/a",
    )

    with pytest.raises(ValidationError, match="timezone"):
        RankingEntry(
            ranking_date=datetime.fromisoformat("2026-07-27T00:00:00"),
            region="World",
            position=1,
            team=ranking_team,
            observed_at=observed_at,
            source_snapshot_id="snapshot",
        )

    with pytest.raises(ValidationError, match="timezone"):
        Match(
            provider_match_id=1,
            status="upcoming",
            scheduled_at_utc=datetime.fromisoformat("2026-07-31T12:00:00"),
            team_one=MatchTeam(provider_team_id=1, name="A"),
            team_two=MatchTeam(provider_team_id=2, name="B"),
            match_url="https://www.hltv.org/matches/1/a-vs-b",
            observed_at=observed_at,
            source_snapshot_id="snapshot",
        )
