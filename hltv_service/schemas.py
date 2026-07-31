"""Versioned service-facing Pydantic models."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ServiceModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @field_validator(
        "observed_at",
        "source_updated_at",
        "effective_at",
        "scheduled_at_utc",
        "ranking_date",
        "actual_start_at",
        "completed_at",
        "match_date",
        "range_start",
        "range_end",
        "start_at",
        "end_at",
        check_fields=False,
    )
    @classmethod
    def timezone_aware(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("timestamps must include a timezone")
        return value


class RosterPlayer(ServiceModel):
    provider_player_id: int | None = None
    nickname: str
    player_url: str | None = None
    country: str | None = None
    status: str | None = None
    image_url: str | None = None


class Team(ServiceModel):
    provider: Literal["hltv"] = "hltv"
    provider_team_id: int
    name: str
    slug: str | None = None
    country: str | None = None
    world_rank: int | None = None
    valve_rank: int | None = None
    ranking_points: int | None = None
    average_player_age: float | None = None
    logo_url: str | None = None
    profile_url: str
    roster: list[RosterPlayer] = Field(default_factory=list)
    observed_at: datetime
    source_updated_at: datetime | None = None
    effective_at: datetime | None = None
    data_completeness: dict[str, bool]
    source_snapshot_id: str


class RosterSnapshot(ServiceModel):
    provider: Literal["hltv"] = "hltv"
    provider_team_id: int
    roster: list[RosterPlayer]
    observed_at: datetime
    source_updated_at: datetime | None = None
    effective_at: datetime | None = None
    data_completeness: dict[str, bool]
    source_snapshot_id: str


class MatchTeam(ServiceModel):
    provider: Literal["hltv"] = "hltv"
    provider_team_id: int | None = None
    name: str | None = None
    provider_url: str | None = None


class MatchEvent(ServiceModel):
    provider: Literal["hltv"] = "hltv"
    provider_event_id: int | None = None
    name: str | None = None
    provider_url: str | None = None


class EventSnapshot(ServiceModel):
    provider: Literal["hltv"] = "hltv"
    provider_event_id: int
    name: str | None = None
    provider_url: str
    observed_at: datetime
    source_updated_at: datetime | None = None
    effective_at: datetime | None = None
    data_completeness: dict[str, bool]
    source_snapshot_id: str


class Match(ServiceModel):
    provider: Literal["hltv"] = "hltv"
    provider_match_id: int
    status: Literal["live", "upcoming", "finished", "postponed", "cancelled"]
    scheduled_at_utc: datetime | None = None
    team_one: MatchTeam
    team_two: MatchTeam
    event: MatchEvent | None = None
    best_of: int | None = None
    stars: int | None = None
    scores: tuple[str | None, str | None] | None = None
    match_url: str
    observed_at: datetime
    source_updated_at: datetime | None = None
    effective_at: datetime | None = None
    data_completeness: dict[str, bool]
    source_snapshot_id: str


class RankingTeam(ServiceModel):
    provider: Literal["hltv"] = "hltv"
    provider_team_id: int
    name: str
    provider_url: str
    logo_url: str | None = None


class RankingEntry(ServiceModel):
    provider: Literal["hltv"] = "hltv"
    ranking_date: datetime
    region: str
    position: int = Field(ge=1)
    previous_position: int | None = Field(default=None, ge=1)
    change: str | None = None
    points: int | None = Field(default=None, ge=0)
    team: RankingTeam
    observed_at: datetime
    source_snapshot_id: str


class ApiMeta(ServiceModel):
    data_age_seconds: int | None
    is_stale: bool
    source_snapshot_id: str | None
    pagination: dict[str, int | None] | None = None


class ApiEnvelope(ServiceModel):
    schema_version: Literal["1.0"] = "1.0"
    data: object
    meta: ApiMeta


class StreamMetadata(ServiceModel):
    name: str
    url: str | None = None


class MatchDetailTeam(MatchTeam):
    displayed_rank: int | None = Field(default=None, ge=1)
    map_advantage: int | None = Field(default=None, ge=0)


class MatchDetailEvent(MatchEvent):
    stage: str | None = None


class MatchResult(ServiceModel):
    winner_team_id: int | None = None
    team_one_score: int | None = Field(default=None, ge=0)
    team_two_score: int | None = Field(default=None, ge=0)
    forfeit: bool | None = None
    walkover: bool | None = None


class MatchDetail(ServiceModel):
    provider: Literal["hltv"] = "hltv"
    provider_match_id: int
    match_url: str
    status: Literal[
        "live", "upcoming", "finished", "postponed", "cancelled"
    ]
    scheduled_at_utc: datetime
    actual_start_at: datetime | None = None
    completed_at: datetime | None = None
    best_of: int | None = Field(default=None, ge=1, le=7)
    event: MatchDetailEvent | None = None
    team_one: MatchDetailTeam
    team_two: MatchDetailTeam
    stars: int | None = Field(default=None, ge=0, le=5)
    lan: bool | None = None
    venue: str | None = None
    location: str | None = None
    streams: list[StreamMetadata] = Field(default_factory=list)
    result: MatchResult | None = None
    observed_at: datetime
    source_updated_at: datetime | None = None
    effective_at: datetime | None = None
    data_completeness: dict[str, bool]
    section_errors: dict[str, str] = Field(default_factory=dict)
    source_snapshot_id: str


class MatchLineupPlayer(ServiceModel):
    provider_player_id: int | None = None
    nickname: str
    player_url: str | None = None
    status: str | None = None
    stand_in: bool | None = None
    identity_state: Literal["stable", "unresolved"]


class MatchLineup(ServiceModel):
    provider: Literal["hltv"] = "hltv"
    provider_match_id: int
    provider_team_id: int
    team_name: str
    players: list[MatchLineupPlayer]
    coach: MatchLineupPlayer | None = None
    observed_at: datetime
    effective_at: datetime | None = None
    data_completeness: dict[str, bool]
    source_snapshot_id: str


class VetoAction(ServiceModel):
    provider: Literal["hltv"] = "hltv"
    provider_match_id: int
    sequence_number: int = Field(ge=1)
    provider_team_id: int | None = None
    team_name: str | None = None
    action: Literal["ban", "pick", "remaining", "decider"]
    canonical_map_id: str | None = None
    map_name: str
    raw_text: str
    observed_at: datetime
    source_snapshot_id: str


class MapResultObservation(ServiceModel):
    provider: Literal["hltv"] = "hltv"
    provider_match_id: int
    map_order: int = Field(ge=1)
    canonical_map_id: str | None = None
    map_name: str
    picker_team_id: int | None = None
    team_one_score: int | None = Field(default=None, ge=0)
    team_two_score: int | None = Field(default=None, ge=0)
    half_time_scores: list[tuple[int, int]] | None = None
    overtime: bool | None = None
    status: Literal["upcoming", "live", "finished", "cancelled"]
    winner_team_id: int | None = None
    observed_at: datetime
    source_snapshot_id: str


class TeamMapStatObservation(ServiceModel):
    provider: Literal["hltv"] = "hltv"
    provider_team_id: int
    canonical_map_id: str | None = None
    map_name: str
    matches_played: int | None = Field(default=None, ge=0)
    wins: int | None = Field(default=None, ge=0)
    losses: int | None = Field(default=None, ge=0)
    win_rate: float | None = Field(default=None, ge=0, le=1)
    round_differential: int | None = None
    ct_side_win_rate: float | None = Field(default=None, ge=0, le=1)
    t_side_win_rate: float | None = Field(default=None, ge=0, le=1)
    range_start: datetime
    range_end: datetime
    opponent_strength_context: str | None = None
    observed_at: datetime
    source_snapshot_id: str


class TeamResultObservation(ServiceModel):
    provider: Literal["hltv"] = "hltv"
    provider_team_id: int
    provider_match_id: int
    match_date: datetime
    opponent_team_id: int | None = None
    opponent_name: str | None = None
    provider_event_id: int | None = None
    best_of: int | None = Field(default=None, ge=1, le=7)
    team_score: int | None = Field(default=None, ge=0)
    opponent_score: int | None = Field(default=None, ge=0)
    winner_team_id: int | None = None
    maps_played: int | None = Field(default=None, ge=0)
    status: str
    observed_at: datetime
    source_snapshot_id: str


class HeadToHeadObservation(ServiceModel):
    provider: Literal["hltv"] = "hltv"
    provider_match_id: int
    provider_team_one_id: int
    provider_team_two_id: int
    match_date: datetime
    provider_event_id: int | None = None
    canonical_map_id: str | None = None
    map_name: str | None = None
    winner_team_id: int | None = None
    score_text: str | None = None
    source_limit: str
    observed_at: datetime
    source_snapshot_id: str


class EventTeam(ServiceModel):
    provider_team_id: int
    name: str
    provider_url: str | None = None
    participation_reason: str | None = None
    rank_during_event: int | None = Field(default=None, ge=1)


class EventDetail(ServiceModel):
    provider: Literal["hltv"] = "hltv"
    provider_event_id: int
    name: str
    provider_url: str
    start_at: datetime | None = None
    end_at: datetime | None = None
    prize_pool_raw: str | None = None
    prize_pool_currency: str | None = None
    prize_pool_amount: int | None = Field(default=None, ge=0)
    location: str | None = None
    event_type: str | None = None
    lan: bool | None = None
    participating_teams: list[EventTeam] = Field(default_factory=list)
    stages: list[str] = Field(default_factory=list)
    tier: str | None = None
    observed_at: datetime
    source_updated_at: datetime | None = None
    effective_at: datetime | None = None
    data_completeness: dict[str, bool]
    section_errors: dict[str, str] = Field(default_factory=dict)
    source_snapshot_id: str
