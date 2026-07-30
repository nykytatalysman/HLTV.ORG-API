"""Translate backward-compatible parser records into service models."""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, time
from urllib.parse import urlparse

from HLTV.exceptions import HLTVParseError
from HLTV.models import Match as LegacyMatch
from HLTV.models import RankedTeam, TeamProfile

from .schemas import (
    EventSnapshot,
    Match,
    MatchEvent,
    MatchTeam,
    RankingEntry,
    RankingTeam,
    RosterPlayer,
    RosterSnapshot,
    Team,
)


def _nullable(value: str | None) -> str | None:
    cleaned = str(value or "").strip()
    return cleaned or None


def _number(value: str | None) -> int | None:
    match = re.search(r"-?\d+", str(value or "").replace(",", ""))
    return int(match.group()) if match else None


def _decimal(value: str | None) -> float | None:
    match = re.search(r"\d+(?:\.\d+)?", str(value or ""))
    return float(match.group()) if match else None


def _slug(url: str) -> str | None:
    parts = [part for part in urlparse(url).path.split("/") if part]
    return parts[2] if len(parts) > 2 else None


def normalize_ranking(
    item: RankedTeam,
    *,
    ranking_date: date,
    region: str,
    observed_at: datetime,
    snapshot_id: str,
) -> RankingEntry:
    if item.provider_id is None or not item.team_url:
        raise HLTVParseError(
            "A ranking entry did not expose a stable HLTV team ID.",
            parse_state="parser_regression",
        )
    effective = datetime.combine(ranking_date, time.min, tzinfo=UTC)
    return RankingEntry(
        ranking_date=effective,
        region=region,
        position=item.position,
        previous_position=item.previous_position,
        change=_nullable(item.change),
        points=item.points,
        team=RankingTeam(
            provider_team_id=item.provider_id,
            name=item.name,
            provider_url=item.team_url,
            logo_url=_nullable(item.logo_url),
        ),
        observed_at=observed_at,
        source_snapshot_id=snapshot_id,
    )


def normalize_team(
    profile: TeamProfile, *, observed_at: datetime, snapshot_id: str
) -> tuple[Team, RosterSnapshot]:
    if profile.provider_id is None or not profile.url:
        raise HLTVParseError(
            "A team profile did not expose a stable HLTV team ID.",
            parse_state="parser_regression",
        )
    roster = [
        RosterPlayer(
            provider_player_id=player.provider_id,
            nickname=player.name,
            player_url=_nullable(player.url),
            country=_nullable(player.country),
            status=_nullable(player.status),
            image_url=_nullable(player.image_url),
        )
        for player in profile.roster
    ]
    completeness = {
        "identity": True,
        "country": bool(profile.country),
        "rankings": _number(profile.current_rank) is not None,
        "roster": bool(roster),
        "roster_provider_ids": bool(roster)
        and all(player.provider_player_id is not None for player in roster),
        "average_player_age": _decimal(profile.players_age) is not None,
    }
    team = Team(
        provider_team_id=profile.provider_id,
        name=profile.name,
        slug=_slug(profile.url),
        country=_nullable(profile.country),
        world_rank=_number(profile.current_rank),
        valve_rank=_number(profile.valve_rank),
        ranking_points=None,
        average_player_age=_decimal(profile.players_age),
        logo_url=_nullable(profile.team_logo),
        profile_url=profile.url,
        roster=roster,
        observed_at=observed_at,
        data_completeness=completeness,
        source_snapshot_id=snapshot_id,
    )
    roster_snapshot = RosterSnapshot(
        provider_team_id=profile.provider_id,
        roster=roster,
        observed_at=observed_at,
        data_completeness={
            "roster": bool(roster),
            "provider_ids": completeness["roster_provider_ids"],
        },
        source_snapshot_id=snapshot_id,
    )
    return team, roster_snapshot


def normalize_match(
    item: LegacyMatch, *, observed_at: datetime, snapshot_id: str
) -> tuple[Match, EventSnapshot | None]:
    if item.provider_id is None or not item.url:
        raise HLTVParseError(
            "A match card did not expose a stable numeric HLTV match ID.",
            parse_state="parser_regression",
        )
    best_of_match = re.search(r"\d+", item.format)
    best_of = int(best_of_match.group()) if best_of_match else None
    scheduled = (
        datetime.fromisoformat(item.scheduled_at_utc)
        if item.scheduled_at_utc
        else None
    )
    event_ref = None
    event_snapshot = None
    if item.event or item.event_url:
        event_ref = MatchEvent(
            provider_event_id=item.event_id,
            name=_nullable(item.event),
            provider_url=_nullable(item.event_url),
        )
    if item.event_id is not None and item.event_url:
        event_snapshot = EventSnapshot(
            provider_event_id=item.event_id,
            name=_nullable(item.event),
            provider_url=item.event_url,
            observed_at=observed_at,
            data_completeness={"identity": True, "name": bool(item.event)},
            source_snapshot_id=snapshot_id,
        )
    score_values = tuple(_nullable(value) for value in item.scores)
    scores = score_values if any(score_values) else None
    match = Match(
        provider_match_id=item.provider_id,
        status=item.status,
        scheduled_at_utc=scheduled,
        team_one=MatchTeam(
            provider_team_id=item.team_ids[0],
            name=_nullable(item.teams[0]),
            provider_url=_nullable(item.team_urls[0]),
        ),
        team_two=MatchTeam(
            provider_team_id=item.team_ids[1],
            name=_nullable(item.teams[1]),
            provider_url=_nullable(item.team_urls[1]),
        ),
        event=event_ref,
        best_of=best_of,
        stars=item.stars,
        scores=scores,
        match_url=item.url,
        observed_at=observed_at,
        effective_at=scheduled,
        data_completeness={
            "match_identity": True,
            "team_identities": all(value is not None for value in item.team_ids),
            "event_identity": item.event_id is not None,
            "scheduled_at": scheduled is not None,
            "best_of": best_of is not None,
        },
        source_snapshot_id=snapshot_id,
    )
    return match, event_snapshot
