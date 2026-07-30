# HLTV.ORG API

![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)
![License GPL-3.0-or-later](https://img.shields.io/badge/License-GPL--3.0--or--later-blue)

A cache-backed ingestion and read service for public pages on
[HLTV.org](https://www.hltv.org/), plus the backward-compatible Python client.
It provides normalized rankings, team profiles, rosters, live/upcoming
matches, evidence provenance, and stable HLTV numeric provider identities.

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
- append-only raw evidence and normalized SQLite observations
- a controlled ingestion worker and a read-only FastAPI service
- stale-cache serving, optional internal bearer authentication, and Docker
  support

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

For development and API contract tests, install `.[dev]`.

## Service architecture

```text
HLTV.org
  -> controlled ingestion worker (the only Selenium process)
  -> raw evidence + normalized versioned SQLite observations
  -> FastAPI read service
  -> internal consumers such as CounterSignal
```

HTTP requests never start Selenium and never scrape HLTV. The API reads only
the SQLite cache. The worker saves raw evidence before parsing, so a blocked or
malformed page remains auditable. Normalized rows link back to the evidence
with `source_snapshot_id`.

The legacy Python client remains available, but production consumers should
use the service boundary so user-facing requests cannot create upstream load.

## Worker usage

Run one ingestion scope:

```bash
python -m hltv_service.worker rankings
python -m hltv_service.worker matches
python -m hltv_service.worker teams
```

Run the bounded full refresh:

```bash
python -m hltv_service.worker refresh
```

`refresh` ingests world and configured regional rankings, live/upcoming
matches, discovered events, and a TTL-limited number of discovered team
profiles. Navigation retries are bounded and apply only to transient browser
errors. A Cloudflare block is saved once, marks the run `blocked`, preserves
all cached rows, and exits with status `2`.

The live smoke test is manual-only and makes one modestly paced request:

```bash
python scripts/smoke_test.py
```

It is deliberately not part of CI.

## FastAPI usage

Start the read service:

```bash
uvicorn hltv_service.app:app --host 0.0.0.0 --port 8000
```

The interactive OpenAPI document is available at `/docs`. A committed,
consumer-focused example is in
[docs/openapi-example.yaml](docs/openapi-example.yaml).

All `/v1` responses use this envelope:

```json
{
  "schema_version": "1.0",
  "data": [],
  "meta": {
    "data_age_seconds": 42,
    "is_stale": false,
    "source_snapshot_id": null,
    "pagination": {"limit": 50, "offset": 0, "next_offset": null}
  }
}
```

Endpoints:

| Endpoint | Purpose |
| --- | --- |
| `GET /health` | Process and database liveness |
| `GET /v1/status` | Ingestion attempts, blocked state, cache age, versions and counts |
| `GET /v1/rankings` | Latest cached rankings with region/limit/cutoff filters |
| `GET /v1/rankings/{ranking_date}` | Rankings for an ISO date |
| `GET /v1/matches` | Cached matches filtered by status, time, team or event |
| `GET /v1/matches/{provider_match_id}` | One current match observation |
| `GET /v1/teams/{provider_team_id}` | Latest normalized team profile |
| `GET /v1/teams/{provider_team_id}/history` | Versioned team history |
| `GET /v1/teams/{provider_team_id}/roster` | Latest roster observation |
| `GET /v1/evidence/{source_snapshot_id}` | Evidence metadata and SHA-256 hash |

Evidence responses omit raw HTML by default. `HLTV_ALLOW_RAW_EVIDENCE=true`
may enable it only in a controlled development environment.

When cache records exist, stale data is returned with `is_stale=true`. A
collection returns `503` only if the service has no usable cached observations;
it does not fabricate an empty successful ingestion.

## Environment variables

| Variable | Default | Meaning |
| --- | --- | --- |
| `HLTV_DATABASE_PATH` | `data/hltv-service.sqlite` | Persistent SQLite path |
| `HLTV_BROWSER` | `chrome` | Worker browser |
| `HLTV_HEADLESS` | `true` | Worker browser mode |
| `HLTV_BROWSER_PROFILE_PATH` | empty | Dedicated service profile only; personal profiles are rejected |
| `HLTV_MINIMUM_REQUEST_INTERVAL` | `3` | Minimum seconds between navigations |
| `HLTV_PAGE_TIMEOUT` | `30` | Page timeout in seconds |
| `HLTV_TEAM_PROFILE_TTL_SECONDS` | `21600` | Team refresh TTL |
| `HLTV_MAXIMUM_TEAM_PROFILES_PER_RUN` | `20` | Bounded team work per run |
| `HLTV_ENABLED_REGIONS` | empty | Comma-separated regional ranking names |
| `HLTV_RETRY_ATTEMPTS` | `3` | Transient navigation attempt limit |
| `HLTV_LOG_LEVEL` | `INFO` | Worker log level |
| `HLTV_SERVICE_TOKEN` | empty | Optional internal bearer/API token |
| `HLTV_MAX_STALE_SECONDS` | `86400` | API stale threshold |
| `HLTV_ALLOW_RAW_EVIDENCE` | `false` | Development-only raw HTML access |

Tokens are compared without logging. Consumers may send
`Authorization: Bearer ...` or `X-Internal-API-Token`.

## Docker and Railway

Build and run the non-root Chromium image:

```bash
docker build -t hltv-data-service .
docker run --rm -p 8000:8000 \
  -e HLTV_DATABASE_PATH=/data/hltv-service.sqlite \
  -v hltv-data:/data \
  hltv-data-service
```

Railway should attach a persistent volume at `/data` and set
`HLTV_DATABASE_PATH=/data/hltv-service.sqlite`. Configure separate Railway
processes from the same image.

API process:

```bash
uvicorn hltv_service.app:app --host 0.0.0.0 --port "$PORT"
```

Worker process (run on a controlled schedule, not per request):

```bash
python -m hltv_service.worker refresh
```

Do not deploy the API without a persistent volume: ephemeral SQLite would
discard the usable stale cache during a blocked ingestion period.

## CounterSignal integration

CounterSignal consumes the service over its internal HTTP adapter. Numeric
HLTV IDs remain provider identities and are mapped through CounterSignal's
existing canonical provider-mapping and review-queue system. Observation,
source-update, effective, and schedule timestamps flow into its append-only
historical pipeline so pre-match features use only knowledge available before
the match cutoff.

See the detailed audit and boundary decisions in
[docs/counter-signal-integration.md](docs/counter-signal-integration.md).

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
pattern. The service does not bypass it: ingestion records an explicit blocked
state and stops without repeated retries. For authorized manual library use, a
dedicated non-personal browser profile may be used and a human may complete a
presented challenge:

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

The required service checks are:

```bash
ruff check .
pytest --cov=HLTV --cov=hltv_service --cov-report=term-missing
python -m build
```

The newly added service package targets at least 85% meaningful line coverage.

## Contributing

Bug reports and parser updates are welcome. Read
[CONTRIBUTING.md](CONTRIBUTING.md) before opening an issue or pull request.

## Credits

This project is a modernization of
[jclge/HLTV.ORG-API](https://github.com/jclge/HLTV.ORG-API), originally created
by **JCLGE (Julien Calenge)** in 2020. The original public API design and project
name are credited to that work.

HLTV.org and its trademarks belong to their respective owners.

Use this software responsibly: keep collection scoped and modestly paced,
honor access restrictions, do not solve CAPTCHAs automatically, do not rotate
proxies or fingerprints, and confirm that your use complies with applicable
terms and law.

## License

GPL-3.0-or-later. See [LICENSE.txt](LICENSE.txt). The Python service remains a
separate GPL network service in the CounterSignal architecture; consumers
should not copy or vendor its source into differently licensed applications.
