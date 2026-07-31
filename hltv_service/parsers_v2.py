"""Section-isolated parsers for bounded HLTV match intelligence."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, TypeVar
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

from HLTV.exceptions import (
    HLTVBlockedError,
    HLTVDeletedError,
    HLTVParseError,
    HLTVUnavailableError,
)
from HLTV.ids import (
    extract_event_id,
    extract_match_id,
    extract_player_id,
    extract_team_id,
)

from .maps import canonical_map_id
from .schemas import (
    EventDetail,
    EventTeam,
    HeadToHeadObservation,
    MapResultObservation,
    MatchDetail,
    MatchDetailEvent,
    MatchDetailTeam,
    MatchLineup,
    MatchLineupPlayer,
    MatchResult,
    StreamMetadata,
    TeamMapStatObservation,
    TeamResultObservation,
    VetoAction,
)

BASE_URL = "https://www.hltv.org"
T = TypeVar("T")


@dataclass(slots=True)
class ParsedMatchIntelligence:
    detail: MatchDetail
    lineups: list[MatchLineup]
    vetoes: list[VetoAction]
    maps: list[MapResultObservation]
    recent_results: list[TeamResultObservation]
    head_to_head: list[HeadToHeadObservation]


def _soup(html: str) -> BeautifulSoup:
    if re.search(r"cf-chl-|just a moment|attention required", html, re.I):
        raise HLTVBlockedError("HLTV returned a Cloudflare challenge page.")
    return BeautifulSoup(html, "html.parser")


def _text(node: Tag | None, separator: str = " ") -> str:
    return node.get_text(separator, strip=True) if node else ""


def _absolute(value: str | None) -> str | None:
    return urljoin(BASE_URL, value) if value else None


def _integer(value: str | None) -> int | None:
    match = re.search(r"-?\d+", (value or "").replace(",", ""))
    return int(match.group()) if match else None


def _percent(value: str | None) -> float | None:
    match = re.search(r"(\d+(?:\.\d+)?)\s*%", value or "")
    return float(match.group(1)) / 100 if match else None


def _unix(value: str | None) -> datetime | None:
    if not value or not re.fullmatch(r"\d{10,13}", value.strip()):
        return None
    timestamp = int(value)
    if timestamp > 9_999_999_999:
        timestamp /= 1000
    return datetime.fromtimestamp(timestamp, tz=UTC)


def _selected_unix(soup: BeautifulSoup, selectors: tuple[str, ...]) -> datetime | None:
    for selector in selectors:
        node = soup.select_one(selector)
        value = _unix(node.get("data-unix") if node else None)
        if value:
            return value
    return None


def _normalized_name(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", (value or "").casefold())


def _guard_match_page(soup: BeautifulSoup) -> None:
    body = _text(soup.body or soup).casefold()
    if "match deleted" in body or "this match has been deleted" in body:
        raise HLTVDeletedError("HLTV identifies this match as deleted.")
    if "match not found" in body or "page not found" in body:
        raise HLTVUnavailableError("The requested HLTV match is unavailable.")
    if not soup.select_one(
        ".match-page, .match-page-v2, .timeAndEvent, .team1-gradient"
    ):
        raise HLTVParseError(
            "Expected match-detail containers are missing.",
            parse_state="unexpected_layout",
        )


def parse_match_status(soup: BeautifulSoup) -> str:
    status = _text(soup.select_one(".countdown, .match-status")).casefold()
    if status == "live" or "match live" in status:
        return "live"
    if "postpon" in status:
        return "postponed"
    if "cancel" in status:
        return "cancelled"
    if "match over" in status or status in {"over", "finished", "completed"}:
        return "finished"
    return "upcoming"


def parse_match_header(soup: BeautifulSoup) -> dict[str, Any]:
    scheduled = _selected_unix(
        soup,
        (
            ".timeAndEvent .date[data-unix]",
            ".match-date[data-unix]",
            "[data-field='scheduled'][data-unix]",
        ),
    )
    if scheduled is None:
        raise HLTVParseError(
            "The match schedule timestamp is missing.",
            parse_state="critical_schedule_missing",
        )
    format_text = _text(
        soup.select_one(".preformatted-text, .match-format")
    )
    best_of_match = re.search(r"\bBest of\s+([1-7])\b|\bBO([1-7])\b", format_text, re.I)
    best_of = (
        int(best_of_match.group(1) or best_of_match.group(2))
        if best_of_match
        else None
    )
    location_match = re.search(r"\((LAN|Online)\)", format_text, re.I)
    lan = (
        location_match.group(1).casefold() == "lan"
        if location_match
        else None
    )
    stars_node = soup.select_one("[data-stars]")
    stars = _integer(stars_node.get("data-stars") if stars_node else None)
    if stars is None:
        active_stars = soup.select(".match-stars .star:not(.faded)")
        stars = len(active_stars) or None
    return {
        "status": parse_match_status(soup),
        "scheduled_at_utc": scheduled,
        "actual_start_at": _selected_unix(
            soup,
            (
                ".actual-start[data-unix]",
                "[data-field='actual-start'][data-unix]",
            ),
        ),
        "completed_at": _selected_unix(
            soup,
            (
                ".match-completed[data-unix]",
                "[data-field='completed'][data-unix]",
            ),
        ),
        "best_of": best_of,
        "stars": stars,
        "lan": lan,
        "venue": _text(
            soup.select_one("[data-field='venue'] .value, .match-venue")
        )
        or None,
        "location": _text(
            soup.select_one("[data-field='location'] .value, .match-location")
        )
        or None,
    }


def parse_match_teams(
    soup: BeautifulSoup,
) -> tuple[MatchDetailTeam, MatchDetailTeam]:
    rankings = soup.select(".teamRanking a, .teamRanking")
    teams: list[MatchDetailTeam] = []
    for index in (1, 2):
        container = soup.select_one(f".team{index}-gradient, .team{index}")
        link = container.select_one('a[href*="/team/"]') if container else None
        name = _text(
            container.select_one(".teamName") if container else None
        )
        url = _absolute(link.get("href") if link else None)
        team_id = extract_team_id(url)
        if not container or not name or team_id is None:
            raise HLTVParseError(
                f"Stable identity for match team {index} is missing.",
                parse_state="critical_team_identity_missing",
            )
        rank = _integer(_text(rankings[index - 1])) if len(rankings) >= index else None
        advantage_node = container.select_one(
            ".map-advantage, [data-map-advantage]"
        )
        advantage = _integer(
            advantage_node.get("data-map-advantage")
            if advantage_node and advantage_node.get("data-map-advantage")
            else _text(advantage_node)
        )
        teams.append(
            MatchDetailTeam(
                provider_team_id=team_id,
                name=name,
                provider_url=url,
                displayed_rank=rank if rank and rank > 0 else None,
                map_advantage=(
                    advantage
                    if advantage is not None and advantage >= 0
                    else None
                ),
            )
        )
    return teams[0], teams[1]


def parse_match_event(soup: BeautifulSoup) -> MatchDetailEvent | None:
    link = soup.select_one(
        '.timeAndEvent .event a[href*="/events/"], '
        '.match-event a[href*="/events/"]'
    )
    if not link:
        return None
    url = _absolute(link.get("href"))
    return MatchDetailEvent(
        provider_event_id=extract_event_id(url),
        name=_text(link) or None,
        provider_url=url,
        stage=_text(soup.select_one(".event-stage, [data-field='stage']")) or None,
    )


def parse_match_streams(soup: BeautifulSoup) -> list[StreamMetadata]:
    streams = []
    for node in soup.select(".stream-box"):
        link = node.select_one("a[href]")
        name = _text(node.select_one(".stream-box-embed, .stream-name")) or "Stream"
        streams.append(
            StreamMetadata(name=name, url=_absolute(link.get("href") if link else None))
        )
    return streams


def _lineup_player(node: Tag) -> MatchLineupPlayer | None:
    link = node if node.name == "a" else node.select_one('a[href*="/player/"]')
    url = _absolute(link.get("href") if link else None)
    raw_id = node.get("data-player-id") or (
        link.get("data-player-id") if link else None
    )
    player_id = _integer(raw_id) or extract_player_id(url)
    nickname = _text(
        node.select_one(".text-ellipsis, .player-nick")
    ) or _text(link)
    if not nickname:
        return None
    explicit_standin = bool(
        node.select_one(".standin, .stand-in")
        or re.search(r"\bstand-?in\b", _text(node), re.I)
    )
    return MatchLineupPlayer(
        provider_player_id=player_id,
        nickname=nickname,
        player_url=url,
        status="stand-in" if explicit_standin else None,
        stand_in=True if explicit_standin else None,
        identity_state="stable" if player_id else "unresolved",
    )


def parse_match_lineups(
    soup: BeautifulSoup,
    *,
    provider_match_id: int,
    teams: tuple[MatchDetailTeam, MatchDetailTeam],
    observed_at: datetime,
    effective_at: datetime | None,
    source_snapshot_id: str,
) -> list[MatchLineup]:
    containers = soup.select("div.players, .match-lineup")
    output: list[MatchLineup] = []
    for index, team in enumerate(teams):
        if index >= len(containers) or team.provider_team_id is None or not team.name:
            continue
        container = containers[index]
        players: list[MatchLineupPlayer] = []
        seen: set[tuple[int | None, str]] = set()
        for node in container.select(
            "[data-player-id], .flagAlign, a[href*='/player/']"
        ):
            player = _lineup_player(node)
            key = (
                player.provider_player_id if player else None,
                player.nickname if player else "",
            )
            if player and key not in seen:
                seen.add(key)
                players.append(player)
        coach_node = container.select_one(".coach")
        coach = _lineup_player(coach_node) if coach_node else None
        output.append(
            MatchLineup(
                provider_match_id=provider_match_id,
                provider_team_id=team.provider_team_id,
                team_name=team.name,
                players=players,
                coach=coach,
                observed_at=observed_at,
                effective_at=effective_at,
                data_completeness={
                    "players_present": bool(players),
                    "stable_player_ids": bool(players)
                    and all(player.provider_player_id for player in players),
                    "coach": coach is not None,
                },
                source_snapshot_id=source_snapshot_id,
            )
        )
    return output


def parse_match_vetoes(
    soup: BeautifulSoup,
    *,
    provider_match_id: int,
    teams: tuple[MatchDetailTeam, MatchDetailTeam],
    observed_at: datetime,
    source_snapshot_id: str,
) -> list[VetoAction]:
    boxes = soup.select(".veto-box")
    if not boxes:
        return []
    lines = boxes[-1].select(".padding div, .veto-line")
    team_by_name = {
        _normalized_name(team.name): team for team in teams if team.name
    }
    output = []
    for line in lines:
        raw = _text(line)
        if not raw or "veto process" in raw.casefold():
            continue
        numbered = re.sub(r"^\d+\.\s*", "", raw)
        action_match = re.match(
            r"(.+?)\s+(removed|banned|picked)\s+(.+)$", numbered, re.I
        )
        team: MatchDetailTeam | None = None
        if action_match:
            team_name, verb, map_name = action_match.groups()
            team = team_by_name.get(_normalized_name(team_name))
            action = "pick" if verb.casefold() == "picked" else "ban"
        else:
            remaining = re.match(
                r"(?:the\s+)?(.+?)\s+(?:was\s+)?"
                r"(left over|remaining|decider)$",
                numbered,
                re.I,
            )
            if not remaining:
                continue
            map_name, verb = remaining.groups()
            action = "decider" if verb.casefold() == "decider" else "remaining"
        output.append(
            VetoAction(
                provider_match_id=provider_match_id,
                sequence_number=len(output) + 1,
                provider_team_id=team.provider_team_id if team else None,
                team_name=team.name if team else None,
                action=action,
                canonical_map_id=canonical_map_id(map_name.strip()),
                map_name=map_name.strip(),
                raw_text=raw,
                observed_at=observed_at,
                source_snapshot_id=source_snapshot_id,
            )
        )
    return output


def parse_map_results(
    soup: BeautifulSoup,
    *,
    provider_match_id: int,
    teams: tuple[MatchDetailTeam, MatchDetailTeam],
    observed_at: datetime,
    source_snapshot_id: str,
) -> list[MapResultObservation]:
    output = []
    team_by_name = {
        _normalized_name(team.name): team for team in teams if team.name
    }
    for order, holder in enumerate(soup.select(".mapholder"), start=1):
        map_name = _text(holder.select_one(".mapname"))
        if not map_name:
            continue
        score_one = _integer(
            _text(holder.select_one(".results-left .results-team-score"))
        )
        score_two = _integer(
            _text(holder.select_one(".results-right .results-team-score"))
        )
        half_text = _text(holder.select_one(".results-center-half-score"))
        halves = [
            (int(left), int(right))
            for left, right in re.findall(r"(\d+)\s*:\s*(\d+)", half_text)
        ]
        pick_node = holder.select_one("[data-pick-team-id], .map-pick")
        picker = _integer(
            pick_node.get("data-pick-team-id") if pick_node else None
        )
        if picker is None and pick_node:
            pick_match = re.search(r"picked by\s+(.+)", _text(pick_node), re.I)
            picked_team = (
                team_by_name.get(_normalized_name(pick_match.group(1)))
                if pick_match
                else None
            )
            picker = picked_team.provider_team_id if picked_team else None
        status_text = _text(holder.select_one(".map-status")).casefold()
        status = (
            "cancelled"
            if "cancel" in status_text
            else "finished"
            if score_one is not None and score_two is not None
            else "live"
            if "live" in status_text
            else "upcoming"
        )
        winner = None
        if status == "finished" and score_one != score_two:
            winner = (
                teams[0].provider_team_id
                if (score_one or 0) > (score_two or 0)
                else teams[1].provider_team_id
            )
        output.append(
            MapResultObservation(
                provider_match_id=provider_match_id,
                map_order=order,
                canonical_map_id=canonical_map_id(map_name),
                map_name=map_name,
                picker_team_id=picker,
                team_one_score=score_one,
                team_two_score=score_two,
                half_time_scores=halves or None,
                overtime=(
                    True
                    if holder.select_one(".overtime") or len(halves) > 2
                    else None
                ),
                status=status,
                winner_team_id=winner,
                observed_at=observed_at,
                source_snapshot_id=source_snapshot_id,
            )
        )
    return output


def parse_match_result(
    soup: BeautifulSoup,
    teams: tuple[MatchDetailTeam, MatchDetailTeam],
    status: str,
) -> MatchResult | None:
    if status not in {"finished", "cancelled"}:
        return None
    score_one = _integer(
        _text(
            soup.select_one(
                ".team1-gradient .team-score, .team1-gradient .won, "
                ".team1-gradient .lost"
            )
        )
    )
    score_two = _integer(
        _text(
            soup.select_one(
                ".team2-gradient .team-score, .team2-gradient .won, "
                ".team2-gradient .lost"
            )
        )
    )
    winner = None
    if soup.select_one(".team1-gradient .won"):
        winner = teams[0].provider_team_id
    elif soup.select_one(".team2-gradient .won"):
        winner = teams[1].provider_team_id
    result_text = _text(soup.select_one(".match-result-note, .countdown"))
    return MatchResult(
        winner_team_id=winner,
        team_one_score=score_one,
        team_two_score=score_two,
        forfeit=True if re.search(r"\bforfeit\b", result_text, re.I) else None,
        walkover=True if re.search(r"\bwalkover|w/o\b", result_text, re.I) else None,
    )


def parse_recent_team_results(
    soup: BeautifulSoup,
    *,
    teams: tuple[MatchDetailTeam, MatchDetailTeam],
    observed_at: datetime,
    source_snapshot_id: str,
) -> list[TeamResultObservation]:
    output: list[TeamResultObservation] = []
    for row in soup.select(".past-matches [data-team-id], .past-match-row"):
        team_id = _integer(row.get("data-team-id"))
        match_link = row.select_one('a[href*="/matches/"]')
        match_url = _absolute(match_link.get("href") if match_link else None)
        provider_match_id = extract_match_id(match_url)
        date_node = row.select_one("[data-unix]")
        match_date = _unix(date_node.get("data-unix") if date_node else None)
        if team_id is None or provider_match_id is None or match_date is None:
            continue
        opponent_link = row.select_one('a[href*="/team/"]')
        opponent_url = _absolute(
            opponent_link.get("href") if opponent_link else None
        )
        event_link = row.select_one('a[href*="/events/"]')
        score = re.search(r"(\d+)\s*[-:]\s*(\d+)", _text(row))
        winner_id = _integer(row.get("data-winner-team-id"))
        format_match = re.search(r"\bBO([1-7])\b", _text(row), re.I)
        output.append(
            TeamResultObservation(
                provider_team_id=team_id,
                provider_match_id=provider_match_id,
                match_date=match_date,
                opponent_team_id=extract_team_id(opponent_url),
                opponent_name=_text(opponent_link) or None,
                provider_event_id=extract_event_id(
                    _absolute(event_link.get("href") if event_link else None)
                ),
                best_of=int(format_match.group(1)) if format_match else None,
                team_score=int(score.group(1)) if score else None,
                opponent_score=int(score.group(2)) if score else None,
                winner_team_id=winner_id,
                maps_played=(
                    int(score.group(1)) + int(score.group(2)) if score else None
                ),
                status=row.get("data-status", "finished"),
                observed_at=observed_at,
                source_snapshot_id=source_snapshot_id,
            )
        )
    return output


def parse_head_to_head(
    soup: BeautifulSoup,
    *,
    teams: tuple[MatchDetailTeam, MatchDetailTeam],
    observed_at: datetime,
    source_snapshot_id: str,
) -> list[HeadToHeadObservation]:
    if teams[0].provider_team_id is None or teams[1].provider_team_id is None:
        return []
    output = []
    for row in soup.select(".head-to-head-listing tr"):
        link = row.select_one('a[href*="/matches/"]')
        match_url = _absolute(link.get("href") if link else None)
        provider_match_id = extract_match_id(match_url)
        date_node = row.select_one("[data-unix]")
        match_date = _unix(date_node.get("data-unix") if date_node else None)
        if provider_match_id is None or match_date is None:
            continue
        event_link = row.select_one('a[href*="/events/"]')
        winner_link = row.select_one('.winner a[href*="/team/"]')
        map_name = _text(
            row.select_one(".dynamic-map-name-short, .map-name")
        ) or None
        output.append(
            HeadToHeadObservation(
                provider_match_id=provider_match_id,
                provider_team_one_id=teams[0].provider_team_id,
                provider_team_two_id=teams[1].provider_team_id,
                match_date=match_date,
                provider_event_id=extract_event_id(
                    _absolute(event_link.get("href") if event_link else None)
                ),
                canonical_map_id=canonical_map_id(map_name),
                map_name=map_name,
                winner_team_id=extract_team_id(
                    _absolute(winner_link.get("href") if winner_link else None)
                ),
                score_text=_text(row.select_one(".result")) or None,
                source_limit="displayed_hltv_match_page_sample",
                observed_at=observed_at,
                source_snapshot_id=source_snapshot_id,
            )
        )
    return output


def _optional_section(
    name: str,
    errors: dict[str, str],
    parser: Callable[[], T],
    default: T,
) -> T:
    try:
        return parser()
    except Exception as exc:
        errors[name] = f"{type(exc).__name__}: {exc}"
        return default


def parse_match_intelligence(
    html: str,
    *,
    url: str,
    observed_at: datetime,
    source_snapshot_id: str,
) -> ParsedMatchIntelligence:
    soup = _soup(html)
    _guard_match_page(soup)
    provider_match_id = extract_match_id(url)
    if provider_match_id is None:
        raise HLTVParseError(
            "The match URL does not expose a stable match ID.",
            parse_state="critical_match_identity_missing",
        )
    header = parse_match_header(soup)
    teams = parse_match_teams(soup)
    errors: dict[str, str] = {}
    event = _optional_section("event", errors, lambda: parse_match_event(soup), None)
    streams = _optional_section(
        "streams", errors, lambda: parse_match_streams(soup), []
    )
    lineups = _optional_section(
        "lineups",
        errors,
        lambda: parse_match_lineups(
            soup,
            provider_match_id=provider_match_id,
            teams=teams,
            observed_at=observed_at,
            effective_at=header["scheduled_at_utc"],
            source_snapshot_id=source_snapshot_id,
        ),
        [],
    )
    vetoes = _optional_section(
        "veto",
        errors,
        lambda: parse_match_vetoes(
            soup,
            provider_match_id=provider_match_id,
            teams=teams,
            observed_at=observed_at,
            source_snapshot_id=source_snapshot_id,
        ),
        [],
    )
    maps = _optional_section(
        "maps",
        errors,
        lambda: parse_map_results(
            soup,
            provider_match_id=provider_match_id,
            teams=teams,
            observed_at=observed_at,
            source_snapshot_id=source_snapshot_id,
        ),
        [],
    )
    result = _optional_section(
        "result",
        errors,
        lambda: parse_match_result(soup, teams, header["status"]),
        None,
    )
    recent_results = _optional_section(
        "recent_results",
        errors,
        lambda: parse_recent_team_results(
            soup,
            teams=teams,
            observed_at=observed_at,
            source_snapshot_id=source_snapshot_id,
        ),
        [],
    )
    head_to_head = _optional_section(
        "head_to_head",
        errors,
        lambda: parse_head_to_head(
            soup,
            teams=teams,
            observed_at=observed_at,
            source_snapshot_id=source_snapshot_id,
        ),
        [],
    )
    detail = MatchDetail(
        provider_match_id=provider_match_id,
        match_url=url,
        event=event,
        team_one=teams[0],
        team_two=teams[1],
        streams=streams,
        result=result,
        observed_at=observed_at,
        effective_at=header["scheduled_at_utc"],
        data_completeness={
            "critical_identity": True,
            "schedule": True,
            "event": event is not None,
            "lineups": bool(lineups),
            "veto": bool(vetoes),
            "maps": bool(maps),
            "result": result is not None,
            "recent_results": bool(recent_results),
            "head_to_head": bool(head_to_head),
        },
        section_errors=errors,
        source_snapshot_id=source_snapshot_id,
        **header,
    )
    return ParsedMatchIntelligence(
        detail=detail,
        lineups=lineups,
        vetoes=vetoes,
        maps=maps,
        recent_results=recent_results,
        head_to_head=head_to_head,
    )


def parse_team_map_stats(
    html: str,
    *,
    provider_team_id: int,
    range_start: datetime,
    range_end: datetime,
    observed_at: datetime,
    source_snapshot_id: str,
) -> list[TeamMapStatObservation]:
    soup = _soup(html)
    rows = soup.select(".map-stat-row, .map-pool-stat")
    parsed: list[dict[str, Any]] = []
    for row in rows:
        map_name = row.get("data-map") or _text(
            row.select_one(".map-name, .map-pool-map-name")
        )
        if not map_name:
            continue
        wins = _integer(row.get("data-wins") or _text(row.select_one(".wins")))
        losses = _integer(
            row.get("data-losses") or _text(row.select_one(".losses"))
        )
        parsed.append(
            {
                "map_name": map_name,
                "matches_played": _integer(
                    row.get("data-matches")
                    or _text(row.select_one(".matches-played"))
                ),
                "wins": wins,
                "losses": losses,
                "win_rate": _percent(
                    row.get("data-win-rate")
                    or _text(row.select_one(".win-rate"))
                ),
                "round_differential": _integer(
                    row.get("data-round-differential")
                    or _text(row.select_one(".round-differential"))
                ),
                "ct_side_win_rate": _percent(
                    row.get("data-ct-win-rate")
                    or _text(row.select_one(".ct-win-rate"))
                ),
                "t_side_win_rate": _percent(
                    row.get("data-t-win-rate")
                    or _text(row.select_one(".t-win-rate"))
                ),
                "opponent_strength_context": (
                    row.get("data-opponent-context")
                    or _text(row.select_one(".opponent-context"))
                    or None
                ),
            }
        )
    if not parsed:
        for column in soup.select(".two-grid .col"):
            map_name = _text(column.select_one(".map-pool-map-name"))
            values = [
                _text(item.select_one(":scope > *:last-child") or item)
                for item in column.select(".stats-rows .stats-row")
            ]
            record = re.search(
                r"(\d+)\s*/\s*(\d+)\s*/\s*(\d+)", values[0] if values else ""
            )
            if map_name and record:
                wins, _draws, losses = map(int, record.groups())
                parsed.append(
                    {
                        "map_name": map_name,
                        "matches_played": wins + int(_draws) + losses,
                        "wins": wins,
                        "losses": losses,
                        "win_rate": _percent(values[1] if len(values) > 1 else ""),
                        "round_differential": None,
                        "ct_side_win_rate": None,
                        "t_side_win_rate": None,
                        "opponent_strength_context": None,
                    }
                )
    if not parsed:
        raise HLTVParseError(
            "Expected bounded team map-stat containers are missing.",
            parse_state="unexpected_layout",
        )
    return [
        TeamMapStatObservation(
            provider_team_id=provider_team_id,
            canonical_map_id=canonical_map_id(record["map_name"]),
            range_start=range_start,
            range_end=range_end,
            observed_at=observed_at,
            source_snapshot_id=source_snapshot_id,
            **record,
        )
        for record in parsed
    ]


def parse_event_detail(
    html: str,
    *,
    url: str,
    observed_at: datetime,
    source_snapshot_id: str,
) -> EventDetail:
    soup = _soup(html)
    provider_event_id = extract_event_id(url)
    name = _text(soup.select_one(".event-hub-title, .event-title"))
    if provider_event_id is None or not name:
        raise HLTVParseError(
            "Stable event identity containers are missing.",
            parse_state="critical_event_identity_missing",
        )
    prize_raw = _text(soup.select_one("td.prizepool, .event-prize-pool")) or None
    amount_match = re.fullmatch(r"\$\s*([\d,]+)", prize_raw or "")
    teams = []
    for node in soup.select(".team-box"):
        link = node.select_one('a[href*="/team/"]')
        team_url = _absolute(link.get("href") if link else None)
        team_id = extract_team_id(team_url)
        logo = node.select_one(".logo")
        team_name = _text(node.select_one(".team-name a")) or (
            logo.get("title") if logo else ""
        )
        if team_id and team_name:
            teams.append(
                EventTeam(
                    provider_team_id=team_id,
                    name=team_name,
                    provider_url=team_url,
                    participation_reason=_text(node.select_one(".sub-text")) or None,
                    rank_during_event=_integer(
                        _text(node.select_one(".event-world-rank"))
                    ),
                )
            )
    format_rows = [
        " — ".join(
            part
            for part in (
                _text(node.select_one(".format-header")),
                _text(node.select_one(".format-data")),
            )
            if part
        )
        for node in soup.select(".formats tr")
    ]
    location = _text(
        soup.select_one(".location .text-ellipsis, .event-location")
    ) or None
    type_text = _text(soup.select_one(".event-type")) or None
    lan = (
        True
        if type_text and "lan" in type_text.casefold()
        else False
        if type_text and "online" in type_text.casefold()
        else None
    )
    start_at = _selected_unix(
        soup, ("td.eventdate span[data-unix]:first-child", ".event-start[data-unix]")
    )
    end_nodes = soup.select("td.eventdate span[data-unix]")
    end_at = (
        _unix(end_nodes[-1].get("data-unix"))
        if end_nodes
        else _selected_unix(soup, (".event-end[data-unix]",))
    )
    return EventDetail(
        provider_event_id=provider_event_id,
        name=name,
        provider_url=url,
        start_at=start_at,
        end_at=end_at,
        prize_pool_raw=prize_raw,
        prize_pool_currency="USD" if amount_match else None,
        prize_pool_amount=(
            int(amount_match.group(1).replace(",", "")) if amount_match else None
        ),
        location=location,
        event_type=type_text,
        lan=lan,
        participating_teams=teams,
        stages=[value for value in format_rows if value],
        tier=_text(soup.select_one(".event-tier")) or None,
        observed_at=observed_at,
        effective_at=start_at,
        data_completeness={
            "identity": True,
            "dates": start_at is not None and end_at is not None,
            "prize_pool": prize_raw is not None,
            "teams": bool(teams),
            "stages": bool(format_rows),
        },
        source_snapshot_id=source_snapshot_id,
    )
