"""Controlled Selenium ingestion worker.

Selenium is imported and instantiated only from this module's CLI path. The
FastAPI application reads SQLite and never calls this worker.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import Callable
from datetime import datetime, timedelta
from time import sleep
from typing import Any
from urllib.parse import quote

from HLTV.browser import Page
from HLTV.exceptions import HLTVBlockedError, HLTVNavigationError
from HLTV.parsers import BASE_URL, parse_matches, parse_rankings, parse_team_profile

from .config import ServiceConfig
from .normalize import normalize_match, normalize_ranking, normalize_team
from .storage import Storage, stable_id, utc_now

LOGGER = logging.getLogger("hltv_service.worker")


class IngestionBlocked(RuntimeError):
    """Signal a preserved, auditable upstream blocked state."""


class IngestionWorker:
    def __init__(
        self,
        storage: Storage,
        config: ServiceConfig,
        *,
        fetcher: Any,
        now: Callable[[], datetime] = utc_now,
        sleep_fn: Callable[[float], None] = sleep,
    ) -> None:
        self.storage = storage
        self.config = config
        self.fetcher = fetcher
        self.now = now
        self.sleep_fn = sleep_fn
        self.summary: dict[str, Any] = {
            "snapshots": 0,
            "rankings": 0,
            "matches": 0,
            "teams": 0,
            "rosters": 0,
            "events": 0,
            "duplicates": 0,
            "errors": [],
        }

    def _fetch(self, url: str, page_type: str) -> tuple[Page, str, datetime]:
        for attempt in range(1, self.config.retry_attempts + 1):
            captured_at = self.now()
            try:
                result = self.fetcher.fetch(url)
                page = (
                    result
                    if isinstance(result, Page)
                    else Page(url=url, html=str(result))
                )
                snapshot_id = self.storage.save_raw_snapshot(
                    requested_url=url,
                    final_url=page.url,
                    page_type=page_type,
                    navigation_outcome="success",
                    title=page.title,
                    captured_at=captured_at,
                    html=page.html,
                )
                self.summary["snapshots"] += 1
                return page, snapshot_id, captured_at
            except HLTVBlockedError as exc:
                page = (
                    exc.page
                    if isinstance(exc.page, Page)
                    else Page(url=url, html="")
                )
                snapshot_id = self.storage.save_raw_snapshot(
                    requested_url=url,
                    final_url=page.url,
                    page_type=page_type,
                    navigation_outcome="blocked",
                    title=page.title,
                    captured_at=captured_at,
                    html=page.html,
                    blocked=True,
                    parse_status="blocked",
                    parse_error=str(exc),
                )
                self.storage.save_parse_result(snapshot_id, "blocked", str(exc))
                raise IngestionBlocked(str(exc)) from exc
            except HLTVNavigationError:
                if attempt >= self.config.retry_attempts:
                    raise
                self.sleep_fn(2 ** (attempt - 1))
        raise AssertionError("retry loop exhausted")

    def ingest_rankings(self) -> None:
        today = self.now().date()
        ranking_date = today - timedelta(days=today.weekday())
        regions = ("World", *self.config.enabled_regions)
        for region in regions:
            if region == "World":
                url = f"{BASE_URL}/ranking/teams"
            else:
                month = ranking_date.strftime("%B").casefold()
                url = (
                    f"{BASE_URL}/ranking/teams/{ranking_date.year}/{month}/"
                    f"{ranking_date.day}/country/{quote(region, safe='')}"
                )
            page, snapshot_id, observed_at = self._fetch(url, "rankings")
            try:
                parsed = parse_rankings(page.html)
                for item in parsed.rankings:
                    model = normalize_ranking(
                        item,
                        ranking_date=ranking_date,
                        region=region,
                        observed_at=observed_at,
                        snapshot_id=snapshot_id,
                    )
                    if self.storage.insert_model("ranking", model):
                        self.summary["rankings"] += 1
                    else:
                        self.summary["duplicates"] += 1
                self.storage.save_parse_result(snapshot_id, "success")
            except Exception as exc:
                self.storage.save_parse_result(snapshot_id, "failed", str(exc))
                raise

    def ingest_matches(self) -> None:
        url = f"{BASE_URL}/matches"
        page, snapshot_id, observed_at = self._fetch(url, "matches")
        try:
            parsed = parse_matches(page.html)
            for item in parsed.matches:
                model, event = normalize_match(
                    item, observed_at=observed_at, snapshot_id=snapshot_id
                )
                if self.storage.insert_model("match", model):
                    self.summary["matches"] += 1
                else:
                    self.summary["duplicates"] += 1
                if event:
                    if self.storage.insert_model("event", event):
                        self.summary["events"] += 1
                    else:
                        self.summary["duplicates"] += 1
            self.storage.save_parse_result(snapshot_id, "success")
        except Exception as exc:
            self.storage.save_parse_result(snapshot_id, "failed", str(exc))
            raise

    def ingest_teams(self) -> None:
        cutoff = self.now() - timedelta(
            seconds=self.config.team_profile_ttl_seconds
        )
        eligible = []
        for team in self.storage.known_team_profiles():
            observed = self.storage.latest_team_observed_at(team["provider_team_id"])
            if observed is None or observed < cutoff:
                eligible.append(team)
        for team in eligible[: self.config.maximum_team_profiles_per_run]:
            if not team["profile_url"]:
                continue
            page, snapshot_id, observed_at = self._fetch(
                team["profile_url"], "team"
            )
            try:
                profile = parse_team_profile(page.html, url=page.url)
                model, roster = normalize_team(
                    profile, observed_at=observed_at, snapshot_id=snapshot_id
                )
                if self.storage.insert_model("team", model):
                    self.summary["teams"] += 1
                else:
                    self.summary["duplicates"] += 1
                if self.storage.insert_model("roster", roster):
                    self.summary["rosters"] += 1
                else:
                    self.summary["duplicates"] += 1
                self.storage.save_parse_result(snapshot_id, "success")
            except Exception as exc:
                self.storage.save_parse_result(snapshot_id, "failed", str(exc))
                raise

    def run(self, command: str) -> dict[str, Any]:
        attempted_at = self.now()
        run_id = stable_id("run", command, attempted_at.isoformat())
        self.storage.save_run(
            run_id=run_id,
            command=command,
            attempted_at=attempted_at,
            status="running",
            summary=self.summary,
        )
        try:
            if command in {"rankings", "refresh"}:
                self.ingest_rankings()
            if command in {"matches", "refresh"}:
                self.ingest_matches()
            if command in {"teams", "refresh"}:
                self.ingest_teams()
        except IngestionBlocked as exc:
            self.summary["errors"].append(
                {"type": "blocked", "message": str(exc)}
            )
            self.storage.save_run(
                run_id=run_id,
                command=command,
                attempted_at=attempted_at,
                completed_at=self.now(),
                status="blocked",
                blocked=True,
                summary=self.summary,
                error=str(exc),
            )
            raise
        except Exception as exc:
            self.summary["errors"].append(
                {"type": type(exc).__name__, "message": str(exc)}
            )
            self.storage.save_run(
                run_id=run_id,
                command=command,
                attempted_at=attempted_at,
                completed_at=self.now(),
                status="failed",
                summary=self.summary,
                error=str(exc),
            )
            raise
        self.storage.save_run(
            run_id=run_id,
            command=command,
            attempted_at=attempted_at,
            completed_at=self.now(),
            status="success",
            summary=self.summary,
        )
        return {**self.summary, "run_id": run_id, "status": "success"}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Ingest HLTV into the SQLite cache")
    parser.add_argument("command", choices=("rankings", "matches", "teams", "refresh"))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    config = ServiceConfig.from_env()
    logging.basicConfig(
        level=getattr(logging, config.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    from HLTV.browser import SeleniumFetcher

    storage = Storage(config.database_path)
    fetcher = SeleniumFetcher(
        browser=config.browser,
        headless=config.headless,
        timeout=config.page_timeout,
        min_interval=config.minimum_request_interval,
        profile_dir=config.browser_profile_path or None,
    )
    worker = IngestionWorker(storage, config, fetcher=fetcher)
    try:
        summary = worker.run(args.command)
        print(json.dumps(summary, sort_keys=True))
        return 0
    except IngestionBlocked as exc:
        LOGGER.error("HLTV ingestion blocked: %s", exc)
        return 2
    except Exception as exc:
        LOGGER.exception("HLTV ingestion failed: %s", exc)
        return 1
    finally:
        fetcher.close()
        storage.close()


if __name__ == "__main__":
    sys.exit(main())
