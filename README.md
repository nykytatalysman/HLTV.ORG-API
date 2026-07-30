# HLTV.ORG API

![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)
![License GPL-3.0-or-later](https://img.shields.io/badge/License-GPL--3.0--or--later-blue)

A modern, unofficial Python client for public pages on
[HLTV.org](https://www.hltv.org/). It provides structured access to team
rankings, team profiles, live and upcoming matches, news listings, archives,
and articles while keeping the original package's public method names working.

> This project is not affiliated with or endorsed by HLTV.org. Page scraping
> can break when upstream markup changes. Use a modest request rate and review
> HLTV's terms before using the package in production.

## Features

- Selenium 4 with automatic Chrome, Edge, or Firefox driver management
- lazy browser startup and deterministic cleanup through context managers
- current ranking, team, match, search, archive, and article parsers
- typed dataclass return models
- original `Teams`, `Matches`, and `News` APIs retained for compatibility
- implemented `FutureMatches`, which was unfinished in the 2020 release
- isolated Beautiful Soup parsers with browser-free unit tests
- explicit exceptions instead of terminating the process with `exit(1)`
- optional persistent browser profiles for Cloudflare clearance

## Requirements

- Python 3.10 or newer
- a current installation of Chrome, Edge, or Firefox

Selenium Manager locates or downloads the matching browser driver
automatically.

## Installation

Clone the repository and install it into a virtual environment:

```bash
git clone https://github.com/nykytatalysman/HLTV.ORG-API.git
cd HLTV.ORG-API
python -m venv .venv
```

Activate the environment:

```bash
# Windows
.venv\Scripts\activate

# Linux or macOS
source .venv/bin/activate
```

Then install:

```bash
python -m pip install --upgrade pip
python -m pip install -e .
```

## Quick start

```python
from HLTV import HLTVClient

with HLTVClient(browser="auto") as client:
    ranking = client.teams.top_teams(size=5)
    for team in ranking.rankings:
        print(team.position, team.name, team.points, team.players)

    for match in client.matches.live().matches:
        print(match.teams, match.scores, match.event)

    for article in client.news.today().articles:
        print(article.title, article.url)
```

Visible browser mode is the default because HLTV may challenge automated
headless sessions. Pass `headless=True` only if it works in your environment.

## Cloudflare challenges

Cloudflare can present a challenge based on the browser, network, or request
pattern. The supported approach is to use a dedicated persistent browser
profile, complete any challenge yourself in the opened browser, and let the
browser retain its clearance:

```python
from HLTV import HLTVClient

with HLTVClient(
    browser="chrome",
    profile_dir=".hltv-profile",
    timeout=180,
    min_interval=3,
) as client:
    print(client.teams.team_content("Astralis"))
```

Do not use your everyday browser profile, share clearance cookies, rotate
identities, or automate CAPTCHA solving. See
[Cloudflare usage and troubleshooting](docs/cloudflare.md) for details.

## Backward-compatible API

Existing code can continue using the original class and method names:

```python
from HLTV import Matches, News, Teams

with Teams() as teams:
    result = teams.GetTopTeams(location="Europe", size=10)
    print(result.teams)
    print(result.score)
    print(result.players)

with Matches() as matches:
    print(matches.OnGoingMatches().teams)
    print(matches.FutureMatches().events)

with News() as news:
    print(news.GetTodayNews().news_titles)
```

## API overview

| Client | Modern method | Compatibility method | Returns |
| --- | --- | --- | --- |
| `Teams` | `top_teams()` | `GetTopTeams()` | `TopTeamsResult` |
| `Teams` | `team_content()` | `TeamContent()` | `TeamProfile` |
| `Matches` | `live()` | `OnGoingMatches()` | `MatchesResult` |
| `Matches` | `upcoming()` | `FutureMatches()` | `MatchesResult` |
| `Matches` | `all()` | — | `MatchesResult` |
| `News` | `today()` | `GetTodayNews()` | `NewsList` |
| `News` | `by_date()` | `GetNewsByDate()` | `NewsList` |
| `News` | `article_by_url()` | `NewsContentByURL()` | `NewsArticle` |
| `News` | — | `NewsContentByDate()` | `NewsArticle` |
| `News` | — | `TodayNewsContent()` | `NewsArticle` |

All package failures derive from `HLTVError`, with specific subclasses for
invalid input, missing content, navigation failures, Cloudflare challenges,
and upstream markup changes.

## Development and testing

Install development dependencies and run the checks:

```bash
python -m pip install -e ".[dev]"
ruff check .
pytest
python -m build
```

Parser tests do not open a browser or contact HLTV. Live browser checks are
intentionally manual so routine CI does not put unnecessary load on the site.

## Contributing

Bug reports and parser updates are welcome. Read
[CONTRIBUTING.md](CONTRIBUTING.md) before opening an issue or pull request.

## Credits

This project is a modernization of
[jclge/HLTV.ORG-API](https://github.com/jclge/HLTV.ORG-API), originally created
by **JCLGE (Julien Calenge)** in 2020. The original public API design and project
name are credited to that work.

HLTV.org and its trademarks belong to their respective owners.

## License

GPL-3.0-or-later. See [LICENSE.txt](LICENSE.txt).
