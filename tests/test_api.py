import datetime as dt

import pytest

from HLTV import HLTVClient, Matches, News, Teams
from HLTV.api import _archive_date, _ranking_date
from HLTV.browser import Page
from HLTV.exceptions import HLTVNotFoundError, HLTVValidationError

RANKING = """
<div class="ranked-team">
  <div class="ranking-header">
    <span class="position">#1</span>
    <div class="teamLine"><span class="name">Falcons</span>
      <span class="points">(901 HLTV points)</span></div>
    <div class="rankingNicknames">karrigan</div>
  </div>
  <a class="moreLink" href="/team/11283/falcons">profile</a>
</div>
"""

SEARCH = """
<div class="search"><table><tr><td>
  <a href="/team/6665/astralis">Astralis</a>
</td></tr></table></div>
"""

PROFILE = """
<div class="teamProfile">
  <div class="bodyshot-team"><div class="playerFlagName">device</div></div>
  <div class="profileTopBox">
    <div class="team-country">Denmark</div>
    <h1 class="profile-team-name">Astralis</h1>
    <div class="profile-team-stat">World ranking #15</div>
  </div>
</div>
"""

MATCHES = """
<div class="match-wrapper live-match-container">
  <a class="match-top" href="/matches/1/a-vs-b">
    <div class="match-event">Event One</div>
  </a>
  <div class="match-info"><div class="match-meta">bo3</div></div>
  <div class="match-teamname">Alpha</div>
  <div class="match-teamname">Beta</div>
</div>
<div class="match-wrapper">
  <a class="match-top" href="/matches/2/c-vs-d">
    <div class="match-event">Event Two</div>
  </a>
  <div class="match-info"><div class="match-meta">21:30</div></div>
  <div class="match-teamname">Gamma</div>
  <div class="match-teamname">Delta</div>
</div>
"""

NEWS_LIST = """
<a class="newsline article" href="/news/10/story">
  <div class="newstext">A current story</div>
  <div class="newstc"><div class="newsrecent">today</div><div>3 comments</div></div>
</a>
"""

ARTICLE = """
<article class="newsitem">
  <h1 class="headline">A current story</h1>
  <div class="article-info"><div>Author</div><div class="date">Today</div></div>
  <div class="newsdsl"><p>Story body.</p></div>
</article>
"""


class FakeFetcher:
    def __init__(self, responses):
        self.responses = list(responses)
        self.urls = []
        self.closed = False

    def fetch(self, url):
        self.urls.append(url)
        response = self.responses.pop(0)
        if isinstance(response, Page):
            return response
        return Page(url=url, html=response)

    def close(self):
        self.closed = True


def test_regional_ranking_uses_one_current_monday_request():
    fetcher = FakeFetcher([RANKING])
    teams = Teams(fetcher=fetcher)
    result = teams.GetTopTeams(location="Europe", size=1)
    monday = dt.date.today() - dt.timedelta(days=dt.date.today().weekday())
    assert result.teams == ["Falcons"]
    assert len(fetcher.urls) == 1
    assert f"/{monday.year}/" in fetcher.urls[0]
    assert fetcher.urls[0].endswith("/country/Europe")


def test_legacy_team_content_uses_search_result():
    fetcher = FakeFetcher([SEARCH, PROFILE])
    with Teams(fetcher=fetcher) as teams:
        profile = teams.TeamContent("Astralis")
    assert profile.name == "Astralis"
    assert profile.current_rank == "15"
    assert fetcher.urls[1].endswith("/team/6665/astralis")
    assert fetcher.closed


def test_ranking_rejects_unsafe_custom_url():
    teams = Teams(fetcher=FakeFetcher([]))
    with pytest.raises(HLTVValidationError):
        teams.GetTopTeams(location="url:https://example.com/ranking")


def test_news_url_validation_happens_before_fetch():
    fetcher = FakeFetcher([])
    news = News(fetcher=fetcher)
    with pytest.raises(HLTVValidationError):
        news.NewsContentByURL("https://example.com/news/1")
    assert fetcher.urls == []


def test_match_clients_filter_live_and_upcoming_results():
    live = Matches(fetcher=FakeFetcher([MATCHES])).OnGoingMatches()
    upcoming = Matches(fetcher=FakeFetcher([MATCHES])).FutureMatches()
    assert live.teams == [["Alpha", "Beta"]]
    assert upcoming.teams == [["Gamma", "Delta"]]


def test_news_archive_and_title_lookup():
    fetcher = FakeFetcher([NEWS_LIST, ARTICLE])
    news = News(fetcher=fetcher)
    result = news.NewsContentByDate("current", 2020, 1)
    assert result.title == "A current story"
    assert result.content == "Story body."
    assert fetcher.urls[0].endswith("/news/archive/2020/january")
    assert fetcher.urls[1].endswith("/news/10/story")


def test_missing_news_title_has_specific_error():
    news = News(fetcher=FakeFetcher([NEWS_LIST]))
    with pytest.raises(HLTVNotFoundError):
        news.TodayNewsContent("not present")


def test_date_validation_helpers():
    assert _ranking_date("2020-01-06") == dt.date(2020, 1, 6)
    assert _archive_date("2020", "2") == dt.date(2020, 2, 1)
    with pytest.raises(HLTVValidationError):
        _ranking_date("2020-01-07")
    with pytest.raises(HLTVValidationError):
        _archive_date("2005", "8")


def test_client_facade_shares_and_closes_fetcher():
    fetcher = FakeFetcher([])
    client = HLTVClient(fetcher=fetcher)
    assert client.teams.fetcher is fetcher
    assert client.matches.fetcher is fetcher
    assert client.news.fetcher is fetcher
    client.close()
    assert fetcher.closed
