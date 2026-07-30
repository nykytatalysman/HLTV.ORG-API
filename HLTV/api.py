"""Public API and compatibility layer."""

from __future__ import annotations

import calendar
import datetime as dt
from typing import Any
from urllib.parse import quote, quote_plus, urlparse

from .browser import Page, SeleniumFetcher
from .exceptions import HLTVNotFoundError, HLTVValidationError
from .models import MatchesResult, NewsArticle, NewsList, TeamProfile, TopTeamsResult
from .parsers import (
    BASE_URL,
    parse_matches,
    parse_news_article,
    parse_news_list,
    parse_rankings,
    parse_team_profile,
    parse_team_search,
)

MONTHS = [
    "january",
    "february",
    "march",
    "april",
    "may",
    "june",
    "july",
    "august",
    "september",
    "october",
    "november",
    "december",
]


class _BaseClient:
    def __init__(
        self,
        *,
        browser: str = "auto",
        headless: bool = False,
        timeout: float = 30,
        min_interval: float = 2.0,
        profile_dir: str | None = None,
        driver: Any | None = None,
        fetcher: Any | None = None,
    ) -> None:
        self.fetcher = fetcher or SeleniumFetcher(
            browser=browser,
            headless=headless,
            timeout=timeout,
            min_interval=min_interval,
            profile_dir=profile_dir,
            driver=driver,
        )

    def close(self) -> None:
        close = getattr(self.fetcher, "close", None)
        if close:
            close()

    def __enter__(self) -> _BaseClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _fetch(self, url: str) -> Page:
        page = self.fetcher.fetch(url)
        if isinstance(page, Page):
            return page
        # Small custom fetchers can return a raw HTML string.
        return Page(url=url, html=str(page))


class Teams(_BaseClient):
    def top_teams(
        self, location: str = "World", size: int = 30, date: str = ""
    ) -> TopTeamsResult:
        try:
            size = int(size)
        except (TypeError, ValueError) as exc:
            raise HLTVValidationError("size must be an integer from 1 to 30") from exc
        if not 1 <= size <= 30:
            raise HLTVValidationError("size must be from 1 to 30")

        location = str(location).strip()
        if not location:
            raise HLTVValidationError("location cannot be empty")

        if location.casefold().startswith("url:"):
            url = location[4:].strip()
            parsed = urlparse(url)
            if parsed.scheme != "https" or parsed.hostname not in {
                "hltv.org",
                "www.hltv.org",
            }:
                raise HLTVValidationError(
                    "custom ranking URLs must use https://www.hltv.org"
                )
            return parse_rankings(self._fetch(url).html, limit=size)

        if date:
            ranking_date = _ranking_date(date)
            url = (
                f"{BASE_URL}/ranking/teams/{ranking_date.year}/"
                f"{MONTHS[ranking_date.month - 1]}/{ranking_date.day}"
            )
            if location.casefold() != "world":
                url = f"{url}/country/{quote(location, safe='')}"
            page = self._fetch(url)
        elif location.casefold() != "world":
            today = dt.date.today()
            ranking_date = today - dt.timedelta(days=today.weekday())
            url = (
                f"{BASE_URL}/ranking/teams/{ranking_date.year}/"
                f"{MONTHS[ranking_date.month - 1]}/{ranking_date.day}/country/"
                f"{quote(location, safe='')}"
            )
            page = self._fetch(url)
        else:
            page = self._fetch(f"{BASE_URL}/ranking/teams")
        return parse_rankings(page.html, limit=size)

    def GetTopTeams(
        self, location: str = "World", size: int = 30, date: str = ""
    ) -> TopTeamsResult:
        return self.top_teams(location=location, size=size, date=date)

    def team_content(self, team: str) -> TeamProfile:
        team = str(team).strip()
        if not team:
            raise HLTVValidationError("team cannot be empty")
        search = self._fetch(f"{BASE_URL}/search?query={quote_plus(team)}")
        url = parse_team_search(search.html, team)
        page = self._fetch(url)
        return parse_team_profile(page.html, url=page.url)

    def TeamContent(self, team: str = "") -> TeamProfile:
        return self.team_content(team)


class Matches(_BaseClient):
    def live(self) -> MatchesResult:
        return parse_matches(self._fetch(f"{BASE_URL}/matches").html, status="live")

    def upcoming(self) -> MatchesResult:
        return parse_matches(
            self._fetch(f"{BASE_URL}/matches").html, status="upcoming"
        )

    def all(self) -> MatchesResult:
        return parse_matches(self._fetch(f"{BASE_URL}/matches").html, status="all")

    def OnGoingMatches(self) -> MatchesResult:
        return self.live()

    def FutureMatches(self) -> MatchesResult:
        return self.upcoming()


class News(_BaseClient):
    def today(self) -> NewsList:
        return parse_news_list(self._fetch(BASE_URL).html)

    def by_date(self, year: int | str = "", month: int | str = "") -> NewsList:
        selected = _archive_date(year, month)
        url = f"{BASE_URL}/news/archive/{selected.year}/{MONTHS[selected.month - 1]}"
        return parse_news_list(self._fetch(url).html)

    def article_by_url(self, url: str) -> NewsArticle:
        parsed = urlparse(str(url))
        if (
            parsed.scheme != "https"
            or parsed.hostname not in {"hltv.org", "www.hltv.org"}
            or not parsed.path.startswith("/news/")
        ):
            raise HLTVValidationError(
                "url must be an https://www.hltv.org/news/... URL"
            )
        page = self._fetch(url)
        return parse_news_article(page.html, url=page.url)

    def _find_article(self, articles: NewsList, title: str) -> NewsArticle:
        title = title.strip()
        if not title:
            raise HLTVValidationError("title cannot be empty")
        match = next(
            (
                article
                for article in articles.articles
                if title.casefold() in article.title.casefold()
            ),
            None,
        )
        if not match:
            raise HLTVNotFoundError(f"No news article contains {title!r}")
        return self.article_by_url(match.url)

    def NewsContentByURL(self, url: str = "") -> NewsArticle:
        return self.article_by_url(url)

    def NewsContentByDate(
        self, title: str = "", year: int | str = "", month: int | str = ""
    ) -> NewsArticle:
        return self._find_article(self.by_date(year, month), title)

    def GetNewsByDate(
        self, year: int | str = "", month: int | str = ""
    ) -> NewsList:
        return self.by_date(year, month)

    def GetTodayNews(self) -> NewsList:
        return self.today()

    def TodayNewsContent(self, title: str = "") -> NewsArticle:
        return self._find_article(self.today(), title)


class HLTVClient:
    """Single shared-browser facade for teams, matches, and news."""

    def __init__(self, **options: Any) -> None:
        fetcher = options.pop("fetcher", None) or SeleniumFetcher(**options)
        self.teams = Teams(fetcher=fetcher)
        self.matches = Matches(fetcher=fetcher)
        self.news = News(fetcher=fetcher)
        self._fetcher = fetcher

    def close(self) -> None:
        close = getattr(self._fetcher, "close", None)
        if close:
            close()

    def __enter__(self) -> HLTVClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def _ranking_date(value: str) -> dt.date:
    try:
        selected = dt.date.fromisoformat(str(value).strip()[:10])
    except ValueError as exc:
        raise HLTVValidationError("date must use YYYY-MM-DD format") from exc
    if selected > dt.date.today():
        raise HLTVValidationError("date cannot be in the future")
    if selected < dt.date(2015, 10, 1):
        raise HLTVValidationError("HLTV rankings start in October 2015")
    if calendar.weekday(selected.year, selected.month, selected.day) != calendar.MONDAY:
        raise HLTVValidationError("HLTV historical ranking dates must be Mondays")
    return selected


def _archive_date(year: int | str, month: int | str) -> dt.date:
    today = dt.date.today()
    if year == "" and month == "":
        return today.replace(day=1)
    try:
        selected = dt.date(int(year), int(month), 1)
    except (TypeError, ValueError) as exc:
        raise HLTVValidationError("year and month must form a valid date") from exc
    if selected > today.replace(day=1):
        raise HLTVValidationError("news archive date cannot be in the future")
    if selected < dt.date(2005, 9, 1):
        raise HLTVValidationError("HLTV news archives start in September 2005")
    return selected
