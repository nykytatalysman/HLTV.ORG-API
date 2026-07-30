"""Versioned service-facing Pydantic models."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ServiceModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @field_validator(
        "observed_at", "source_updated_at", "effective_at", check_fields=False
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
