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

from . import PARSER_VERSION
from .config import ServiceConfig
from .normalize import normalize_match, normalize_ranking, normalize_team
from .parsers_v2 import (
    parse_event_detail,
    parse_match_intelligence,
    parse_team_map_stats,
)
from .storage import Storage, canonical_json, stable_id, utc_now

LOGGER = logging.getLogger("hltv_service.worker")


class IngestionBlocked(RuntimeError):
    """Signal a preserved, auditable upstream blocked state."""

    def __init__(self, message: str, snapshot_id: str | None = None) -> None:
        super().__init__(message)
        self.snapshot_id = snapshot_id


class IngestionAlreadyRunning(RuntimeError):
    """Raised when the cross-process SQLite ingestion lock is held."""


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
            "match_details": 0,
            "lineups": 0,
            "vetoes": 0,
            "map_results": 0,
            "team_map_stats": 0,
            "team_results": 0,
            "head_to_head": 0,
            "item_failures": 0,
            "item_blocked": 0,
            "duplicates": 0,
            "errors": [],
        }
        self.run_id: str | None = None

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
                LOGGER.info(
                    canonical_json(
                        {
                            "event": "fetch_observation",
                            "ingestion_run_id": self.run_id,
                            "fetch_observation_id": snapshot_id,
                            "page_type": page_type,
                            "parser_version": PARSER_VERSION,
                            "result": "success",
                            "blocked": False,
                            "duration_seconds": max(
                                0.0,
                                (self.now() - captured_at).total_seconds(),
                            ),
                        }
                    )
                )
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
                LOGGER.warning(
                    canonical_json(
                        {
                            "event": "fetch_observation",
                            "ingestion_run_id": self.run_id,
                            "fetch_observation_id": snapshot_id,
                            "page_type": page_type,
                            "parser_version": PARSER_VERSION,
                            "result": "blocked",
                            "blocked": True,
                            "duration_seconds": max(
                                0.0,
                                (self.now() - captured_at).total_seconds(),
                            ),
                        }
                    )
                )
                raise IngestionBlocked(str(exc), snapshot_id) from exc
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

    def _item_result(
        self,
        *,
        item_type: str,
        provider_id: int | None,
        url: str,
        attempted_at: datetime,
        status: str,
        snapshot_id: str | None = None,
        error: Exception | None = None,
    ) -> None:
        if self.run_id is None:
            return
        self.storage.record_item_result(
            run_id=self.run_id,
            item_type=item_type,
            provider_id=provider_id,
            requested_url=url,
            attempted_at=attempted_at,
            completed_at=self.now(),
            status=status,
            source_snapshot_id=snapshot_id,
            error=error,
        )
        LOGGER.info(
            canonical_json(
                {
                    "event": "ingestion_item",
                    "ingestion_run_id": self.run_id,
                    "fetch_observation_id": snapshot_id,
                    "provider_entity_id": provider_id,
                    "page_type": item_type,
                    "parser_version": PARSER_VERSION,
                    "result": status,
                    "duration_seconds": max(
                        0.0, (self.now() - attempted_at).total_seconds()
                    ),
                    "blocked": status == "blocked",
                }
            )
        )

    def _match_priority(self, match: dict[str, Any]) -> tuple[int, str, int]:
        status = match["status"]
        scheduled = (
            datetime.fromisoformat(match["scheduled_at_utc"])
            if match.get("scheduled_at_utc")
            else None
        )
        now = self.now()
        if status == "live":
            priority = 0
        elif (
            status == "upcoming"
            and scheduled
            and scheduled <= now + timedelta(hours=24)
        ):
            priority = 1
        elif self.storage.latest_intelligence_verified_at(
            "match_detail", "provider_match_id", match["provider_match_id"]
        ) is None:
            priority = 2
        elif status == "finished":
            detail = self.storage.match_detail(match["provider_match_id"])
            priority = 3 if not detail or not detail[0].get("result") else 5
        else:
            priority = 4
        return (
            priority,
            match.get("scheduled_at_utc") or "9999-12-31T23:59:59+00:00",
            match["provider_match_id"],
        )

    def _match_detail_due(self, match: dict[str, Any]) -> bool:
        last_verified = self.storage.latest_intelligence_verified_at(
            "match_detail", "provider_match_id", match["provider_match_id"]
        )
        if last_verified is None:
            return True
        ttl = (
            self.config.finished_match_refresh_ttl_seconds
            if match["status"] == "finished"
            else self.config.match_detail_ttl_seconds
        )
        detail = self.storage.match_detail(match["provider_match_id"])
        if (
            match["status"] == "finished"
            and detail
            and detail[0].get("result")
        ):
            return False
        return last_verified <= self.now() - timedelta(seconds=ttl)

    def ingest_match_details(self) -> None:
        matches = sorted(
            (
                item
                for item in self.storage.matches(limit=10_000)
                if item.get("match_url") and self._match_detail_due(item)
            ),
            key=self._match_priority,
        )[: self.config.maximum_match_details_per_run]
        for match in matches:
            attempted_at = self.now()
            snapshot_id: str | None = None
            provider_id = match["provider_match_id"]
            url = match["match_url"]
            try:
                page, snapshot_id, observed_at = self._fetch(url, "match_detail")
                parsed = parse_match_intelligence(
                    page.html,
                    url=page.url,
                    observed_at=observed_at,
                    source_snapshot_id=snapshot_id,
                )
                for kind, records, summary_key in (
                    ("match_detail", [parsed.detail], "match_details"),
                    ("match_lineup", parsed.lineups, "lineups"),
                    ("match_veto", parsed.vetoes, "vetoes"),
                    ("map_result", parsed.maps, "map_results"),
                    ("team_result", parsed.recent_results, "team_results"),
                    ("head_to_head", parsed.head_to_head, "head_to_head"),
                ):
                    for record in records:
                        if self.storage.insert_intelligence(kind, record):
                            self.summary[summary_key] += 1
                        else:
                            self.summary["duplicates"] += 1
                self.storage.save_parse_result(snapshot_id, "success")
                self._item_result(
                    item_type="match_detail",
                    provider_id=provider_id,
                    url=url,
                    attempted_at=attempted_at,
                    status="success",
                    snapshot_id=snapshot_id,
                )
            except IngestionBlocked as exc:
                self.summary["item_blocked"] += 1
                self._item_result(
                    item_type="match_detail",
                    provider_id=provider_id,
                    url=url,
                    attempted_at=attempted_at,
                    status="blocked",
                    snapshot_id=exc.snapshot_id,
                    error=exc,
                )
                raise
            except Exception as exc:
                self.summary["item_failures"] += 1
                self.summary["errors"].append(
                    {
                        "item_type": "match_detail",
                        "provider_id": provider_id,
                        "type": type(exc).__name__,
                        "message": str(exc),
                    }
                )
                if snapshot_id:
                    self.storage.save_parse_result(snapshot_id, "failed", str(exc))
                self._item_result(
                    item_type="match_detail",
                    provider_id=provider_id,
                    url=url,
                    attempted_at=attempted_at,
                    status="failed",
                    snapshot_id=snapshot_id,
                    error=exc,
                )

    def ingest_team_map_stats(self) -> None:
        now = self.now()
        range_start = now - timedelta(days=self.config.map_stats_lookback_days)
        candidates = []
        for team in self.storage.known_team_profiles():
            last_verified = self.storage.latest_intelligence_verified_at(
                "team_map_stat", "provider_team_id", team["provider_team_id"]
            )
            if (
                last_verified is None
                or last_verified
                <= now - timedelta(seconds=self.config.team_stats_ttl_seconds)
            ):
                candidates.append(team)
        for team in candidates[: self.config.maximum_team_stats_per_run]:
            attempted_at = self.now()
            provider_id = team["provider_team_id"]
            url = (
                f"{BASE_URL}/stats/teams/maps/{provider_id}/-"
                f"?startDate={range_start.date().isoformat()}"
                f"&endDate={now.date().isoformat()}"
            )
            snapshot_id: str | None = None
            try:
                page, snapshot_id, observed_at = self._fetch(url, "team_map_stats")
                records = parse_team_map_stats(
                    page.html,
                    provider_team_id=provider_id,
                    range_start=range_start,
                    range_end=now,
                    observed_at=observed_at,
                    source_snapshot_id=snapshot_id,
                )
                for record in records:
                    if self.storage.insert_intelligence("team_map_stat", record):
                        self.summary["team_map_stats"] += 1
                    else:
                        self.summary["duplicates"] += 1
                self.storage.save_parse_result(snapshot_id, "success")
                self._item_result(
                    item_type="team_map_stats",
                    provider_id=provider_id,
                    url=url,
                    attempted_at=attempted_at,
                    status="success",
                    snapshot_id=snapshot_id,
                )
            except IngestionBlocked as exc:
                self.summary["item_blocked"] += 1
                self._item_result(
                    item_type="team_map_stats",
                    provider_id=provider_id,
                    url=url,
                    attempted_at=attempted_at,
                    status="blocked",
                    snapshot_id=exc.snapshot_id,
                    error=exc,
                )
                raise
            except Exception as exc:
                self.summary["item_failures"] += 1
                if snapshot_id:
                    self.storage.save_parse_result(snapshot_id, "failed", str(exc))
                self._item_result(
                    item_type="team_map_stats",
                    provider_id=provider_id,
                    url=url,
                    attempted_at=attempted_at,
                    status="failed",
                    snapshot_id=snapshot_id,
                    error=exc,
                )

    def ingest_event_details(self) -> None:
        candidates = []
        for event in self.storage.known_event_profiles():
            last_verified = self.storage.latest_intelligence_verified_at(
                "event_detail", "provider_event_id", event["provider_event_id"]
            )
            if (
                last_verified is None
                or last_verified
                <= self.now() - timedelta(seconds=self.config.team_profile_ttl_seconds)
            ):
                candidates.append(event)
        for event in candidates[: self.config.maximum_event_details_per_run]:
            attempted_at = self.now()
            provider_id = event["provider_event_id"]
            url = event["provider_url"] or f"{BASE_URL}/events/{provider_id}/-"
            snapshot_id: str | None = None
            try:
                page, snapshot_id, observed_at = self._fetch(url, "event_detail")
                record = parse_event_detail(
                    page.html,
                    url=page.url,
                    observed_at=observed_at,
                    source_snapshot_id=snapshot_id,
                )
                if self.storage.insert_intelligence("event_detail", record):
                    self.summary["events"] += 1
                else:
                    self.summary["duplicates"] += 1
                self.storage.save_parse_result(snapshot_id, "success")
                self._item_result(
                    item_type="event_detail",
                    provider_id=provider_id,
                    url=url,
                    attempted_at=attempted_at,
                    status="success",
                    snapshot_id=snapshot_id,
                )
            except IngestionBlocked as exc:
                self.summary["item_blocked"] += 1
                self._item_result(
                    item_type="event_detail",
                    provider_id=provider_id,
                    url=url,
                    attempted_at=attempted_at,
                    status="blocked",
                    snapshot_id=exc.snapshot_id,
                    error=exc,
                )
                raise
            except Exception as exc:
                self.summary["item_failures"] += 1
                if snapshot_id:
                    self.storage.save_parse_result(snapshot_id, "failed", str(exc))
                self._item_result(
                    item_type="event_detail",
                    provider_id=provider_id,
                    url=url,
                    attempted_at=attempted_at,
                    status="failed",
                    snapshot_id=snapshot_id,
                    error=exc,
                )

    def run(self, command: str) -> dict[str, Any]:
        attempted_at = self.now()
        run_id = stable_id("run", command, attempted_at.isoformat())
        self.run_id = run_id
        self.storage.save_run(
            run_id=run_id,
            command=command,
            attempted_at=attempted_at,
            status="running",
            summary=self.summary,
        )
        if not self.storage.acquire_worker_lock(
            run_id,
            acquired_at=attempted_at,
            ttl_seconds=self.config.ingestion_lock_ttl_seconds,
        ):
            error = IngestionAlreadyRunning(
                "Another HLTV ingestion worker already holds the lock."
            )
            self.storage.save_run(
                run_id=run_id,
                command=command,
                attempted_at=attempted_at,
                completed_at=self.now(),
                status="skipped",
                summary=self.summary,
                error=str(error),
            )
            raise error
        try:
            if command in {"rankings", "refresh"}:
                self.ingest_rankings()
            if command in {"matches", "refresh"}:
                self.ingest_matches()
            if command in {"details", "intelligence", "refresh"}:
                self.ingest_match_details()
            if command in {"teams", "refresh"}:
                self.ingest_teams()
            if command in {"stats", "intelligence", "refresh"}:
                self.ingest_team_map_stats()
            if command in {"events", "intelligence", "refresh"}:
                self.ingest_event_details()
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
        finally:
            self.storage.release_worker_lock(run_id)
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
    parser.add_argument(
        "command",
        choices=(
            "rankings",
            "matches",
            "teams",
            "details",
            "stats",
            "events",
            "intelligence",
            "refresh",
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    config = ServiceConfig.from_env()
    logging.basicConfig(
        level=getattr(logging, config.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    from HLTV.browser import SeleniumFetcher

    storage = Storage(
        config.database_path,
        busy_timeout_ms=config.sqlite_busy_timeout_ms,
    )
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
    except IngestionAlreadyRunning as exc:
        LOGGER.error("HLTV ingestion skipped: %s", exc)
        return 3
    except Exception as exc:
        LOGGER.exception("HLTV ingestion failed: %s", exc)
        return 1
    finally:
        fetcher.close()
        storage.close()


if __name__ == "__main__":
    sys.exit(main())
