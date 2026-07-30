import pytest

from HLTV.exceptions import HLTVBlockedError, HLTVParseError
from HLTV.ids import (
    extract_event_id,
    extract_match_id,
    extract_news_id,
    extract_player_id,
    extract_team_id,
)
from HLTV.parsers import (
    _absolute,
    parse_matches,
    parse_news_article,
    parse_news_list,
    parse_rankings,
    parse_team_profile,
    parse_team_search,
)

RANKING_HTML = """
<div class="ranked-team standard-box">
  <div class="ranking-header">
    <span class="position wide-position">#1</span>
    <span class="team-logo"><img src="https://cdn/team.png"></span>
    <div class="teamLine"><span class="name">Falcons</span>
      <span class="points">(901 HLTV points)</span>
    </div>
    <div class="rankingNicknames"><span>karrigan</span></div>
    <div class="rankingNicknames"><span>NiKo</span></div>
    <div class="change neutral">-</div>
  </div>
  <div class="lineup-con">
    <a class="moreLink" href="/team/11283/falcons">HLTV Team profile</a>
  </div>
</div>
"""

TEAM_HTML = """
<div class="teamProfile">
  <div class="bodyshot-team">
    <div class="playerFlagName">device</div>
    <div class="playerFlagName">stavn</div>
  </div>
  <div class="profileTopBox">
    <img class="teamlogo" src="https://cdn/astralis.svg">
    <div class="team-country">Denmark</div>
    <h1 class="profile-team-name">Astralis</h1>
    <div class="profile-team-stat">Valve ranking Beta #22</div>
    <div class="profile-team-stat">World ranking #15</div>
    <div class="profile-team-stat">Weeks in top30 for core 58</div>
    <div class="profile-team-stat">Average player age 24.4</div>
  </div>
  <div class="ranking-info">Current ranking #15 Peak #1 Time at peak 121 weeks</div>
  <div class="tab-content">Astralis's last 5 matches Won Lost Won Won Lost</div>
</div>
"""

MATCHES_HTML = """
<div class="match-wrapper live-match-container"><div class="match">
  <a class="match-top" href="/matches/1/a-vs-b"><div class="match-event">Event One</div></a>
  <div class="match-rating"><i class="fa fa-star"></i><i class="fa fa-star faded"></i></div>
  <a class="match-info"><div class="match-meta match-meta-live">Live</div><div class="match-meta">bo3</div></a>
  <a class="match-teams"><div class="match-teamname">Alpha</div><div class="match-teamname">Beta</div></a>
  <a class="match-team-livescore"><div class="match-team-score">9 (0)</div><div class="match-team-score">7 (1)</div></a>
</div></div>
<div class="match-wrapper"><div class="match">
  <a class="match-top" href="/matches/2/c-vs-d"><div class="match-event">Event Two</div></a>
  <a class="match-info"><div class="match-meta">21:30</div><div class="match-meta">bo1</div></a>
  <a class="match-teams"><div class="match-teamname">Gamma</div><div class="match-teamname">Delta</div></a>
</div></div>
"""

NEWS_LIST_HTML = """
<div class="standard-box standard-list">
  <a class="newsline article" href="/news/10/story">
    <div class="newstext">A current story</div>
    <div class="newstc"><div class="newsrecent">an hour ago</div><div>15 comments</div></div>
  </a>
</div>
"""

ARTICLE_HTML = """
<article class="newsitem standard-box">
  <h1 class="headline">A current story</h1>
  <div class="article-info"><div>Author</div><div class="date">30-7-2026 20:03</div></div>
  <div class="newsdsl"><p class="news-block">First paragraph.</p>
    <img class="image" src="https://cdn/photo.jpg">
    <img class="flag" src="/flag.gif">
  </div>
</article>
"""


def test_rankings_and_legacy_properties():
    result = parse_rankings(RANKING_HTML)
    assert result.teams == ["Falcons"]
    assert result.score == [901]
    assert result.players == [["karrigan", "NiKo"]]
    assert result.rankings[0].team_url.endswith("/team/11283/falcons")


def test_team_search_and_profile():
    search = '<div class="search"><table><tr><td><a href="/team/6665/astralis">Astralis</a></td></tr></table></div>'
    assert parse_team_search(search, "Astralis").endswith("/team/6665/astralis")
    profile = parse_team_profile(TEAM_HTML, url="https://www.hltv.org/team/6665/astralis")
    assert profile.name == "Astralis"
    assert profile.current_rank == "15"
    assert profile.players == ["device", "stavn"]
    assert profile.current_form == [3, 2]
    assert profile.peak == "1"


def test_live_and_upcoming_matches():
    result = parse_matches(MATCHES_HTML)
    assert len(result.matches) == 2
    assert result.matches[0].teams == ("Alpha", "Beta")
    assert result.matches[0].scores == ("9 (0)", "7 (1)")
    assert result.matches[0].stars == 1
    assert result.matches[1].status == "upcoming"
    assert result.matches[1].time == "21:30"


def test_news_list_and_article():
    listing = parse_news_list(NEWS_LIST_HTML)
    assert listing.news_titles == ["A current story"]
    assert listing.comments == ["15 comments"]
    article = parse_news_article(ARTICLE_HTML, url=listing.articles[0].url)
    assert article.author == "Author"
    assert article.content == "First paragraph."
    assert article.images == ["https://cdn/photo.jpg"]


def test_missing_urls_stay_missing_instead_of_becoming_the_homepage():
    assert _absolute(None) == ""
    assert _absolute("") == ""
    assert _absolute("/team/1/example") == "https://www.hltv.org/team/1/example"
    ranking = parse_rankings(RANKING_HTML.replace('<img src="https://cdn/team.png">', ""))
    assert ranking.rankings[0].logo_url == ""
    profile = parse_team_profile(TEAM_HTML.replace('<img class="teamlogo" src="https://cdn/astralis.svg">', ""))
    assert profile.team_logo == ""
    matches = parse_matches(MATCHES_HTML.replace('href="/matches/2/c-vs-d"', ""))
    assert matches.matches[1].url == ""
    article = parse_news_article(ARTICLE_HTML.replace('<img class="image" src="https://cdn/photo.jpg">', ""))
    assert article.images == []


@pytest.mark.parametrize(
    ("extractor", "url", "expected"),
    [
        (extract_team_id, "/team/6665/astralis", 6665),
        (extract_player_id, "https://www.hltv.org/player/7592/device", 7592),
        (extract_match_id, "/matches/2379999/a-vs-b", 2379999),
        (extract_event_id, "/events/7557/major", 7557),
        (extract_news_id, "/news/40123/story", 40123),
        (extract_team_id, "/team/not-a-number/name", None),
        (extract_match_id, None, None),
        (extract_event_id, "https://example.com/events/7/name", None),
    ],
)
def test_provider_id_extraction(extractor, url, expected):
    assert extractor(url) == expected


def test_match_parser_deduplicates_by_numeric_match_id_not_team_names():
    duplicate = MATCHES_HTML + MATCHES_HTML.replace(
        "/matches/2/c-vs-d", "/matches/3/c-vs-d-rematch"
    )
    result = parse_matches(duplicate)
    assert [match.provider_id for match in result.matches] == [1, 2, 3]


def test_match_parser_distinguishes_empty_blocked_and_broken_pages():
    assert parse_matches(
        '<main class="matches-page"><div class="no-matches">No upcoming matches</div></main>'
    ).matches == []
    with pytest.raises(HLTVBlockedError):
        parse_matches('<script src="/cdn-cgi/challenge-platform/x"></script>')
    with pytest.raises(HLTVParseError, match="containers") as layout:
        parse_matches("<main>unrelated page</main>")
    assert layout.value.parse_state == "unexpected_layout"
    with pytest.raises(HLTVParseError) as regression:
        parse_matches('<main class="matches-page"><div class="match-wrapper"></div></main>')
    assert regression.value.parse_state == "parser_regression"
