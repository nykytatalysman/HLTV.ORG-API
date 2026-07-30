"""HTML parsers isolated from browser automation for fast, reliable tests."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

from .exceptions import HLTVBlockedError, HLTVNotFoundError, HLTVParseError
from .ids import extract_event_id, extract_match_id, extract_player_id, extract_team_id
from .models import (
    Match,
    MatchesResult,
    NewsArticle,
    NewsItem,
    NewsList,
    Player,
    RankedTeam,
    TeamProfile,
    TopTeamsResult,
)

BASE_URL = "https://www.hltv.org"


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


def _text(node: Tag | None, separator: str = " ") -> str:
    return node.get_text(separator, strip=True) if node else ""


def _absolute(value: str | None, base_url: str = BASE_URL) -> str:
    if not value:
        return ""
    return urljoin(base_url, value)


def _integer(value: str) -> int | None:
    match = re.search(r"-?\d+", value.replace(",", ""))
    return int(match.group()) if match else None


def parse_rankings(html: str, *, limit: int = 30) -> TopTeamsResult:
    soup = _soup(html)
    rankings: list[RankedTeam] = []
    for card in soup.select(".ranked-team")[:limit]:
        position_text = _text(card.select_one(".position"))
        points_text = _text(card.select_one(".points"))
        position_match = re.search(r"\d+", position_text)
        points_match = re.search(r"([\d,]+)\s+HLTV points", points_text, re.I)
        name = _text(card.select_one(".teamLine .name, .ranking-header .name"))
        if not (position_match and points_match and name):
            continue

        player_nodes = card.select(".rankingNicknames")
        if not player_nodes:
            player_nodes = card.select(".lineup .nick")
        players = [_text(node) for node in player_nodes if _text(node)]
        # Responsive markup can repeat the same five names.
        players = list(dict.fromkeys(players))
        link = card.select_one('a.moreLink[href^="/team/"]')
        logo = card.select_one(".team-logo img")
        team_url = _absolute(link.get("href") if link else None)
        change = _text(card.select_one(".change"))
        previous = None
        movement = _integer(change)
        if movement is not None:
            candidate = int(position_match.group()) + movement
            previous = candidate if candidate >= 1 else None
        rankings.append(
            RankedTeam(
                position=int(position_match.group()),
                name=name,
                points=int(points_match.group(1).replace(",", "")),
                players=players,
                change=change,
                team_url=team_url,
                logo_url=_absolute(logo.get("src") if logo else None),
                provider_id=extract_team_id(team_url),
                previous_position=previous,
            )
        )

    if not rankings:
        raise HLTVParseError(
            "No rankings were found. HLTV may have changed its ranking markup."
        )
    return TopTeamsResult(rankings)


def parse_team_search(html: str, query: str) -> str:
    soup = _soup(html)
    links = soup.select('.search table a[href^="/team/"]')
    if not links:
        search = soup.select_one(".search")
        if search and re.search(
            r"\b(?:no results|nothing found)\b", _text(search), re.I
        ):
            raise HLTVNotFoundError(f"No HLTV team found for {query!r}")
        raise HLTVParseError(
            "Expected team-search result containers were missing.",
            parse_state="unexpected_layout",
        )
    exact = next(
        (link for link in links if _text(link).casefold() == query.casefold()), links[0]
    )
    return _absolute(exact.get("href"))


def _stat_value(top: Tag, label: str) -> str:
    for node in top.select(".profile-team-stat"):
        value = _text(node)
        if value.casefold().startswith(label.casefold()):
            return value[len(label) :].strip().lstrip("#").strip()
    return ""


def parse_team_profile(html: str, *, url: str = "") -> TeamProfile:
    soup = _soup(html)
    top = soup.select_one(".teamProfile .profileTopBox, .profileTopBox")
    name = _text(soup.select_one(".profile-team-name"))
    country = _text(soup.select_one(".team-country"))
    if not (top and name):
        raise HLTVParseError(
            "No team profile was found. HLTV may have changed its team markup."
        )

    roster: list[Player] = []
    player_cards = soup.select(
        ".bodyshot-team a[href^='/player/'], "
        ".players-table a[href^='/player/'], "
        ".player-holder a[href^='/player/']"
    )
    for card in player_cards:
        player_url = _absolute(card.get("href"))
        nickname = _text(
            card.select_one(
                ".playerFlagName, .playersBox-playernick, .player-nick, .text-ellipsis"
            )
        ) or _text(card)
        if not nickname:
            continue
        image = card.select_one("img")
        flag = card.select_one("[title]")
        roster.append(
            Player(
                name=nickname,
                url=player_url,
                country=str(flag.get("title") or "") if flag else "",
                image_url=_absolute(
                    image.get("src") or image.get("data-src") if image else None
                ),
                status="active",
                provider_id=extract_player_id(player_url),
            )
        )
    players = [player.name for player in roster]
    if not players:
        players = [
            _text(node)
            for node in soup.select(".bodyshot-team .playerFlagName")
            if _text(node)
        ]
    if not players:
        players = [
            _text(node)
            for node in soup.select(".players-table .playersBox-playernick")
            if _text(node)
        ]
    players = list(dict.fromkeys(players))

    ranking_info = soup.select_one(".teamProfile .ranking-info")
    ranking_text = _text(ranking_info)
    peak_match = re.search(r"Peak\s*#?(\d+)", ranking_text, re.I)
    time_match = re.search(
        r"Time at peak\s*(\d+\s+(?:weeks|week|months|month|years|year))",
        ranking_text,
        re.I,
    )
    form_text = ""
    for content in soup.select(".teamProfile .tab-content"):
        value = _text(content)
        if "last 5 matches" in value.casefold():
            form_text = value
            break
    wins = len(re.findall(r"\bWon\b", form_text))
    losses = len(re.findall(r"\bLost\b", form_text))

    logo = soup.select_one(".profile-team-logo-container img, img.teamlogo")
    return TeamProfile(
        name=name,
        country=country,
        players=players,
        current_rank=_stat_value(top, "World ranking") or "0",
        valve_rank=_stat_value(top, "Valve ranking Beta") or "0",
        weeks_in_top_30=_stat_value(top, "Weeks in top30 for core") or "0",
        players_age=_stat_value(top, "Average player age") or "0",
        peak=peak_match.group(1) if peak_match else "0",
        time_at_peak=time_match.group(1) if time_match else "0 weeks",
        current_form=[wins, losses],
        team_logo=_absolute(logo.get("src") if logo else None),
        url=url,
        provider_id=extract_team_id(url),
        roster=roster,
    )


def _parse_match(card: Tag, *, live: bool) -> Match | None:
    event = _text(card.select_one(".match-event"))
    teams = [_text(node) for node in card.select(".match-teamname")]
    if len(teams) < 2:
        return None
    match_link = card.select_one('a.match-top[href^="/matches/"]')
    match_url = _absolute(match_link.get("href") if match_link else None)
    team_links = card.select('a[href^="/team/"]')
    team_urls = tuple(_absolute(link.get("href")) for link in team_links[:2])
    while len(team_urls) < 2:
        team_urls += ("",)
    event_link = card.select_one('a[href^="/events/"]')
    event_url = _absolute(event_link.get("href") if event_link else None)
    meta = [_text(node) for node in card.select(".match-info .match-meta")]
    format_value = next(
        (value for value in meta if re.fullmatch(r"bo\d+", value, re.I)), ""
    )
    time_value = next(
        (value for value in meta if re.fullmatch(r"\d{1,2}:\d{2}", value)), ""
    )
    scores = [
        value if re.search(r"\d", value) else ""
        for value in (_text(node) for node in card.select(".match-team-score"))
    ]
    while len(scores) < 2:
        scores.append("")
    stars = len(card.select(".match-rating .fa-star:not(.faded)"))
    scheduled_at = None
    unix_node = card.select_one("[data-unix]")
    if unix_node:
        try:
            unix_value = int(str(unix_node.get("data-unix")))
            if unix_value > 10_000_000_000:
                unix_value //= 1000
            scheduled_at = datetime.fromtimestamp(unix_value, UTC).isoformat()
        except (TypeError, ValueError, OSError):
            scheduled_at = None
    return Match(
        event=event,
        teams=(teams[0], teams[1]),
        format=format_value,
        status="live" if live else "upcoming",
        url=match_url,
        scores=(scores[0], scores[1]),
        time=time_value,
        stars=stars,
        provider_id=extract_match_id(match_url),
        team_ids=(extract_team_id(team_urls[0]), extract_team_id(team_urls[1])),
        team_urls=(team_urls[0], team_urls[1]),
        event_id=extract_event_id(event_url),
        event_url=event_url,
        scheduled_at_utc=scheduled_at,
    )


def parse_matches(html: str, *, status: str = "all") -> MatchesResult:
    if status not in {"all", "live", "upcoming"}:
        raise ValueError("status must be one of: all, live, upcoming")
    lowered = html.casefold()
    if any(
        marker in lowered
        for marker in (
            "/cdn-cgi/challenge-platform/",
            'id="challenge-running"',
            'class="cf-chl-',
        )
    ):
        raise HLTVBlockedError("HLTV returned a Cloudflare challenge page.")
    soup = _soup(html)
    cards = soup.select(".match-wrapper")
    page_container = soup.select_one(
        ".matches-page, .matches-list, .upcomingMatchesSection, "
        ".liveMatchesSection, [data-page='matches']"
    )
    empty_marker = soup.select_one(".no-matches, .empty-state, .matches-empty")
    if not cards:
        has_empty_copy = re.search(
            r"\bno\s+(?:upcoming\s+)?matches\b",
            _text(page_container),
            re.I,
        )
        if page_container and (empty_marker or has_empty_copy):
            return MatchesResult([])
        raise HLTVParseError(
            "Expected match-page containers were missing or contained no "
            "recognizable empty-state marker.",
            parse_state="unexpected_layout",
        )
    matches: list[Match] = []
    malformed = 0
    if status in {"all", "live"}:
        for card in soup.select(".live-match-container"):
            parsed = _parse_match(card, live=True)
            if parsed:
                matches.append(parsed)
            else:
                malformed += 1
    if status in {"all", "upcoming"}:
        for card in soup.select(".match-wrapper:not(.live-match-container)"):
            parsed = _parse_match(card, live=False)
            if parsed:
                matches.append(parsed)
            else:
                malformed += 1
    if not matches and malformed:
        raise HLTVParseError(
            "Match cards were present but none matched the parser contract.",
            parse_state="parser_regression",
        )
    deduplicated: list[Match] = []
    seen_ids: set[int] = set()
    for match in matches:
        if match.provider_id is not None:
            if match.provider_id in seen_ids:
                continue
            seen_ids.add(match.provider_id)
        deduplicated.append(match)
    return MatchesResult(deduplicated)


def parse_news_list(html: str) -> NewsList:
    soup = _soup(html)
    articles: list[NewsItem] = []
    seen: set[str] = set()
    for link in soup.select('a.newsline.article[href^="/news/"]'):
        url = _absolute(link.get("href"))
        if url in seen:
            continue
        title = _text(link.select_one(".newstext"))
        if not title:
            continue
        time = _text(link.select_one(".newsrecent"))
        comments_node = link.select_one(".newstc")
        comments = ""
        if comments_node:
            children = comments_node.find_all("div", recursive=False)
            comments = _text(children[-1]) if children else _text(comments_node)
            if time and comments == _text(comments_node):
                comments = comments.removeprefix(time).strip()
        articles.append(NewsItem(title=title, time=time, comments=comments, url=url))
        seen.add(url)
    if not articles:
        raise HLTVParseError(
            "No news articles were found. HLTV may have changed its news markup."
        )
    return NewsList(articles)


def parse_news_article(html: str, *, url: str = "") -> NewsArticle:
    soup = _soup(html)
    article = soup.select_one("article.newsitem, .newsitem")
    title = _text(soup.select_one("h1.headline"))
    body = soup.select_one(".newsdsl")
    if not (article and title and body):
        raise HLTVParseError(
            "No news article was found. HLTV may have changed its article markup."
        )
    info = soup.select_one(".article-info")
    author = ""
    date = ""
    if info:
        children = info.find_all("div", recursive=False)
        if children:
            author = _text(children[0])
        date_node = info.select_one(".date")
        date = _text(date_node) or (_text(children[1]) if len(children) > 1 else "")
    images = []
    for image in body.select("img.image"):
        source = (
            image.get("src")
            or image.get("data-cookieblock-src")
            or image.get("data-image-overlay")
            or str(image.get("srcset") or "").split(" ", 1)[0]
        )
        if source:
            images.append(_absolute(source))
    return NewsArticle(
        title=title,
        author=author,
        date=date,
        content=_text(body, "\n"),
        images=list(dict.fromkeys(images)),
        url=url,
    )
