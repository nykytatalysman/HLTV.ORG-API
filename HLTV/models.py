"""Typed return models with compatibility properties for the 2020 API."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class Player:
    name: str
    url: str = ""
    country: str = ""
    image_url: str = ""
    status: str = ""


@dataclass(slots=True)
class RankedTeam:
    position: int
    name: str
    points: int
    players: list[str] = field(default_factory=list)
    change: str = ""
    team_url: str = ""
    logo_url: str = ""


@dataclass(slots=True)
class TopTeamsResult:
    rankings: list[RankedTeam]

    @property
    def teams(self) -> list[str]:
        return [team.name for team in self.rankings]

    @property
    def score(self) -> list[int]:
        return [team.points for team in self.rankings]

    @property
    def players(self) -> list[list[str]]:
        return [team.players for team in self.rankings]


@dataclass(slots=True)
class TeamProfile:
    name: str
    country: str
    players: list[str] = field(default_factory=list)
    current_rank: str = "0"
    valve_rank: str = "0"
    weeks_in_top_30: str = "0"
    players_age: str = "0"
    peak: str = "0"
    time_at_peak: str = "0 weeks"
    current_form: list[int] = field(default_factory=lambda: [0, 0])
    team_logo: str = ""
    url: str = ""


@dataclass(slots=True)
class Match:
    event: str
    teams: tuple[str, str]
    format: str
    status: str
    url: str
    scores: tuple[str, str] = ("", "")
    time: str = ""
    stars: int = 0
    maps: list[str] = field(default_factory=list)


@dataclass(slots=True)
class MatchesResult:
    matches: list[Match]

    @property
    def stars(self) -> list[str]:
        return [str(match.stars) for match in self.matches]

    @property
    def events(self) -> list[str]:
        return [match.event for match in self.matches]

    @property
    def format(self) -> list[str]:
        return [match.format for match in self.matches]

    @property
    def maps(self) -> list[list[str]]:
        return [match.maps for match in self.matches]

    @property
    def teams(self) -> list[list[str]]:
        return [list(match.teams) for match in self.matches]

    @property
    def scores(self) -> list[list[str]]:
        return [list(match.scores) for match in self.matches]


@dataclass(slots=True)
class NewsItem:
    title: str
    time: str
    comments: str
    url: str


@dataclass(slots=True)
class NewsList:
    articles: list[NewsItem]

    @property
    def news_titles(self) -> list[str]:
        return [article.title for article in self.articles]

    @property
    def time(self) -> list[str]:
        return [article.time for article in self.articles]

    @property
    def comments(self) -> list[str]:
        return [article.comments for article in self.articles]


@dataclass(slots=True)
class NewsArticle:
    title: str
    author: str
    date: str
    content: str
    images: list[str]
    url: str
