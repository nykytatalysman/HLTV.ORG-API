from datetime import UTC, datetime
from pathlib import Path

import pytest

from HLTV.browser import Page
from HLTV.exceptions import HLTVBlockedError
from hltv_service.config import ServiceConfig
from hltv_service.storage import Storage
from hltv_service.worker import IngestionBlocked, IngestionWorker

FIXTURES = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 7, 30, 12, tzinfo=UTC)


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


def page(name: str, url: str) -> Page:
    return Page(
        url=url,
        html=(FIXTURES / name).read_text(encoding="utf-8"),
        title="Fixture | HLTV.org",
    )


def config(path, **changes) -> ServiceConfig:
    values = {
        "database_path": path,
        "maximum_team_profiles_per_run": 0,
        "maximum_match_details_per_run": 2,
        "maximum_event_details_per_run": 0,
        "maximum_team_stats_per_run": 0,
        "retry_attempts": 1,
    }
    values.update(changes)
    return ServiceConfig(**values)


def seed_matches(store: Storage) -> None:
    worker = IngestionWorker(
        store,
        config(store.path, maximum_match_details_per_run=0),
        fetcher=FakeFetcher(
            [page("matches.html", "https://www.hltv.org/matches")]
        ),
        now=lambda: NOW,
        sleep_fn=lambda _: None,
    )
    worker.run("matches")


def test_priority_queue_processes_live_before_upcoming_and_honors_limit(
    tmp_path,
) -> None:
    store = Storage(tmp_path / "priority.sqlite")
    seed_matches(store)
    fetcher = FakeFetcher(
        [
            page(
                "match-detail-live.html",
                "https://www.hltv.org/matches/2400001/alpha-vs-beta",
            )
        ]
    )
    worker = IngestionWorker(
        store,
        config(store.path, maximum_match_details_per_run=1),
        fetcher=fetcher,
        now=lambda: NOW,
        sleep_fn=lambda _: None,
    )

    summary = worker.run("details")

    assert summary["match_details"] == 1
    assert fetcher.urls == [
        "https://www.hltv.org/matches/2400001/alpha-vs-beta"
    ]
    assert store.match_detail(2400002) == []
    store.close()


def test_partial_detail_failure_keeps_successful_items_and_audit_rows(
    tmp_path,
) -> None:
    store = Storage(tmp_path / "partial.sqlite")
    seed_matches(store)
    fetcher = FakeFetcher(
        [
            Page(
                url="https://www.hltv.org/matches/2400001/alpha-vs-beta",
                html="<main>layout changed</main>",
                title="Fixture",
            ),
            page(
                "match-detail-upcoming.html",
                "https://www.hltv.org/matches/2400002/alpha-vs-beta-rematch",
            ),
        ]
    )
    worker = IngestionWorker(
        store,
        config(store.path),
        fetcher=fetcher,
        now=lambda: NOW,
        sleep_fn=lambda _: None,
    )

    summary = worker.run("details")

    assert summary["item_failures"] == 1
    assert summary["match_details"] == 1
    statuses = {
        row[0]
        for row in store.connection.execute(
            "SELECT status FROM ingestion_item_results"
        )
    }
    assert statuses == {"failed", "success"}
    assert store.match_detail(2400002)[0]["provider_match_id"] == 2400002
    store.close()


def test_detail_block_stops_browser_work_but_preserves_existing_cache(
    tmp_path,
) -> None:
    store = Storage(tmp_path / "blocked.sqlite")
    seed_matches(store)
    blocked_page = Page(
        url="https://www.hltv.org/matches/2400001/alpha-vs-beta",
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
        now=lambda: NOW,
        sleep_fn=lambda _: pytest.fail("a block must not retry"),
    )

    with pytest.raises(IngestionBlocked):
        worker.run("details")

    assert len(fetcher.urls) == 1
    assert store.status()["data_counts"]["match"] == 2
    assert store.status()["blocked"] is True
    assert store.connection.execute(
        "SELECT status FROM ingestion_item_results"
    ).fetchone()[0] == "blocked"
    store.close()


def test_team_stat_and_event_queues_are_independently_bounded(tmp_path) -> None:
    store = Storage(tmp_path / "secondary.sqlite")
    seed_matches(store)
    stats_fetcher = FakeFetcher(
        [
            page(
                "team-map-stats.html",
                "https://www.hltv.org/stats/teams/maps/1001/-",
            )
        ]
    )
    stats_worker = IngestionWorker(
        store,
        config(store.path, maximum_team_stats_per_run=1),
        fetcher=stats_fetcher,
        now=lambda: NOW,
        sleep_fn=lambda _: None,
    )
    assert stats_worker.run("stats")["team_map_stats"] == 2
    assert len(stats_fetcher.urls) == 1

    event_fetcher = FakeFetcher(
        [
            page(
                "event-detail.html",
                "https://www.hltv.org/events/7001/summer-open",
            )
        ]
    )
    event_worker = IngestionWorker(
        store,
        config(store.path, maximum_event_details_per_run=1),
        fetcher=event_fetcher,
        now=lambda: NOW,
        sleep_fn=lambda _: None,
    )
    assert event_worker.run("events")["events"] == 1
    assert store.event_detail(7001)[0]["provider_event_id"] == 7001
    store.close()

