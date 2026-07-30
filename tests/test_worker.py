from datetime import UTC, datetime
from pathlib import Path

import pytest

from HLTV.browser import Page
from HLTV.exceptions import HLTVBlockedError, HLTVNavigationError
from hltv_service.config import ServiceConfig
from hltv_service.storage import Storage
from hltv_service.worker import IngestionBlocked, IngestionWorker

FIXTURES = Path(__file__).parent / "fixtures"


class FakeFetcher:
    def __init__(self, responses):
        self.responses = list(responses)
        self.urls = []

    def fetch(self, url):
        self.urls.append(url)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def fixture_page(name, url):
    return Page(
        url=url,
        html=(FIXTURES / name).read_text(encoding="utf-8"),
        title="Fixture | HLTV.org",
    )


def config(path, **changes):
    return ServiceConfig(
        database_path=path,
        maximum_team_profiles_per_run=2,
        retry_attempts=3,
        **changes,
    )


def test_refresh_ingests_rankings_matches_teams_and_evidence(tmp_path):
    ranking_url = "https://www.hltv.org/ranking/teams"
    match_url = "https://www.hltv.org/matches"
    responses = [
        fixture_page("rankings.html", ranking_url),
        fixture_page("matches.html", match_url),
        fixture_page("team-alpha.html", "https://www.hltv.org/team/1001/alpha"),
        fixture_page("team-beta.html", "https://www.hltv.org/team/1002/beta"),
    ]
    store = Storage(tmp_path / "service.sqlite")
    worker = IngestionWorker(
        store,
        config(store.path),
        fetcher=FakeFetcher(responses),
        now=lambda: datetime(2026, 7, 30, 12, tzinfo=UTC),
        sleep_fn=lambda _: None,
    )
    summary = worker.run("refresh")
    assert summary["rankings"] == 2
    assert summary["matches"] == 2
    assert summary["teams"] == 2
    assert summary["rosters"] == 2
    assert summary["events"] == 1
    assert store.status()["data_counts"] == {
        "ranking": 2,
        "team": 2,
        "roster": 2,
        "match": 2,
        "event": 1,
    }
    assert store.matches()[0]["provider_match_id"] == 2400001
    assert store.latest_entity("team", "provider_team_id", 1001)["roster"][0][
        "provider_player_id"
    ] == 501
    store.close()


def test_transient_navigation_is_bounded_and_retried(tmp_path):
    store = Storage(tmp_path / "service.sqlite")
    url = "https://www.hltv.org/matches"
    pauses = []
    fetcher = FakeFetcher(
        [
            HLTVNavigationError("temporary"),
            HLTVNavigationError("temporary"),
            fixture_page("matches.html", url),
        ]
    )
    worker = IngestionWorker(
        store,
        config(store.path),
        fetcher=fetcher,
        now=lambda: datetime(2026, 7, 30, 12, tzinfo=UTC),
        sleep_fn=pauses.append,
    )
    assert worker.run("matches")["matches"] == 2
    assert pauses == [1, 2]
    store.close()


def test_block_is_saved_once_without_retry_or_cache_deletion(tmp_path):
    store = Storage(tmp_path / "service.sqlite")
    blocked_page = Page(
        url="https://www.hltv.org/matches",
        html='<script src="/cdn-cgi/challenge-platform/x"></script>',
        title="Just a moment...",
    )
    fetcher = FakeFetcher(
        [HLTVBlockedError("blocked", page=blocked_page)]
    )
    worker = IngestionWorker(
        store,
        config(store.path),
        fetcher=fetcher,
        now=lambda: datetime(2026, 7, 30, 12, tzinfo=UTC),
        sleep_fn=lambda _: pytest.fail("blocked requests must not retry"),
    )
    with pytest.raises(IngestionBlocked):
        worker.run("matches")
    assert len(fetcher.urls) == 1
    assert store.status()["blocked"] is True
    evidence = store.connection.execute(
        "SELECT snapshot_id FROM raw_snapshots WHERE blocked=1"
    ).fetchone()
    assert store.evidence(evidence[0])["parse_status"] == "blocked"
    assert store.status()["data_counts"]["match"] == 0
    store.close()


def test_malformed_html_is_evidence_and_not_a_valid_empty_schedule(tmp_path):
    store = Storage(tmp_path / "service.sqlite")
    page = Page(
        url="https://www.hltv.org/matches",
        html="<main>layout changed</main>",
        title="HLTV.org",
    )
    worker = IngestionWorker(
        store,
        config(store.path),
        fetcher=FakeFetcher([page]),
        now=lambda: datetime(2026, 7, 30, 12, tzinfo=UTC),
        sleep_fn=lambda _: None,
    )
    with pytest.raises(Exception, match="containers"):
        worker.run("matches")
    snapshot = store.connection.execute(
        "SELECT snapshot_id FROM raw_snapshots"
    ).fetchone()[0]
    assert store.evidence(snapshot)["parse_status"] == "failed"
    assert store.status()["most_recent_attempt"]["status"] == "failed"
    store.close()
