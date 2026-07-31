"""Environment-backed service configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().casefold()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean")


def _int(name: str, default: int, *, minimum: int = 0) -> int:
    value = int(os.getenv(name, str(default)))
    if value < minimum:
        raise ValueError(f"{name} must be at least {minimum}")
    return value


def _float(name: str, default: float, *, minimum: float = 0) -> float:
    value = float(os.getenv(name, str(default)))
    if value < minimum:
        raise ValueError(f"{name} must be at least {minimum}")
    return value


@dataclass(frozen=True, slots=True)
class ServiceConfig:
    database_path: Path = Path("data/hltv-service.sqlite")
    browser: str = "chrome"
    headless: bool = True
    browser_profile_path: str = ""
    minimum_request_interval: float = 3.0
    page_timeout: float = 30.0
    team_profile_ttl_seconds: int = 21_600
    maximum_team_profiles_per_run: int = 20
    map_stats_lookback_days: int = 90
    maximum_match_details_per_run: int = 25
    maximum_event_details_per_run: int = 10
    maximum_team_stats_per_run: int = 20
    match_detail_ttl_seconds: int = 900
    finished_match_refresh_ttl_seconds: int = 86_400
    team_stats_ttl_seconds: int = 21_600
    ingestion_lock_ttl_seconds: int = 3_600
    scheduler_interval_seconds: int = 900
    enabled_regions: tuple[str, ...] = ()
    log_level: str = "INFO"
    api_token: str = ""
    max_stale_seconds: int = 86_400
    allow_raw_evidence: bool = False
    retry_attempts: int = 3
    runtime_mode: str = "combined"
    scheduler_enabled: bool = True
    initial_refresh: bool = False
    api_workers: int = 1
    persistent_directory: Path = Path("/data")
    production: bool = False
    sqlite_busy_timeout_ms: int = 5_000
    raw_evidence_retention_days: int = 90
    failed_evidence_retention_days: int = 180
    normalized_history_retention_days: int = 0
    retention_batch_size: int = 250

    def __post_init__(self) -> None:
        if self.runtime_mode not in {"combined", "api"}:
            raise ValueError("HLTV_RUNTIME_MODE must be combined or api")
        if self.api_workers != 1:
            raise ValueError("HLTV_API_WORKERS must be 1 when using SQLite")
        if self.runtime_mode == "api" and self.scheduler_enabled:
            raise ValueError(
                "HLTV_SCHEDULER_ENABLED must be false in API-only mode"
            )
        if self.scheduler_interval_seconds < 300:
            raise ValueError(
                "HLTV_SCHEDULER_INTERVAL_SECONDS must be at least 300"
            )
        if self.normalized_history_retention_days != 0:
            raise ValueError(
                "HLTV_NORMALIZED_HISTORY_RETENTION_DAYS must remain 0"
            )
        if self.production:
            database = self.database_path.resolve()
            persistent = self.persistent_directory.resolve()
            if persistent != database.parent and persistent not in database.parents:
                raise ValueError(
                    "HLTV_DATABASE_PATH must be inside HLTV_PERSISTENT_DIRECTORY "
                    "in production"
                )

    @classmethod
    def from_env(cls) -> ServiceConfig:
        regions = tuple(
            item.strip()
            for item in os.getenv("HLTV_ENABLED_REGIONS", "").split(",")
            if item.strip()
        )
        profile = os.getenv("HLTV_BROWSER_PROFILE_PATH", "").strip()
        lowered_profile = profile.casefold().replace("\\", "/")
        if profile and any(
            marker in lowered_profile
            for marker in ("/google/chrome/user data", "/microsoft/edge/user data")
        ):
            raise ValueError(
                "HLTV_BROWSER_PROFILE_PATH must be a dedicated service profile, "
                "not a personal browser profile"
            )
        production = _bool("HLTV_PRODUCTION", False) or bool(
            os.getenv("RAILWAY_ENVIRONMENT")
        )
        return cls(
            database_path=Path(
                os.getenv("HLTV_DATABASE_PATH", "data/hltv-service.sqlite")
            ),
            browser=os.getenv("HLTV_BROWSER", "chrome").strip().casefold(),
            headless=_bool("HLTV_HEADLESS", True),
            browser_profile_path=profile,
            minimum_request_interval=_float(
                "HLTV_MINIMUM_REQUEST_INTERVAL", 3.0, minimum=1.0
            ),
            page_timeout=_float("HLTV_PAGE_TIMEOUT", 30.0, minimum=1.0),
            team_profile_ttl_seconds=_int(
                "HLTV_TEAM_PROFILE_TTL_SECONDS", 21_600, minimum=60
            ),
            maximum_team_profiles_per_run=_int(
                "HLTV_MAXIMUM_TEAM_PROFILES_PER_RUN", 20, minimum=0
            ),
            map_stats_lookback_days=_int(
                "HLTV_MAP_STATS_LOOKBACK_DAYS", 90, minimum=1
            ),
            maximum_match_details_per_run=_int(
                "HLTV_MAX_MATCH_DETAILS_PER_RUN", 25, minimum=0
            ),
            maximum_event_details_per_run=_int(
                "HLTV_MAX_EVENT_DETAILS_PER_RUN", 10, minimum=0
            ),
            maximum_team_stats_per_run=_int(
                "HLTV_MAX_TEAM_STATS_PER_RUN", 20, minimum=0
            ),
            match_detail_ttl_seconds=_int(
                "HLTV_MATCH_DETAIL_TTL_SECONDS", 900, minimum=60
            ),
            finished_match_refresh_ttl_seconds=_int(
                "HLTV_FINISHED_MATCH_REFRESH_TTL_SECONDS",
                86_400,
                minimum=300,
            ),
            team_stats_ttl_seconds=_int(
                "HLTV_TEAM_STATS_TTL_SECONDS", 21_600, minimum=300
            ),
            ingestion_lock_ttl_seconds=_int(
                "HLTV_INGESTION_LOCK_TTL_SECONDS", 3_600, minimum=300
            ),
            scheduler_interval_seconds=_int(
                "HLTV_SCHEDULER_INTERVAL_SECONDS", 900, minimum=300
            ),
            enabled_regions=regions,
            log_level=os.getenv("HLTV_LOG_LEVEL", "INFO").strip().upper(),
            api_token=os.getenv("HLTV_SERVICE_TOKEN", ""),
            max_stale_seconds=_int(
                "HLTV_MAX_STALE_SECONDS", 86_400, minimum=0
            ),
            allow_raw_evidence=_bool("HLTV_ALLOW_RAW_EVIDENCE", False),
            retry_attempts=_int("HLTV_RETRY_ATTEMPTS", 3, minimum=1),
            runtime_mode=os.getenv("HLTV_RUNTIME_MODE", "combined")
            .strip()
            .casefold(),
            scheduler_enabled=_bool("HLTV_SCHEDULER_ENABLED", True),
            initial_refresh=_bool("HLTV_INITIAL_REFRESH", False),
            api_workers=_int("HLTV_API_WORKERS", 1, minimum=1),
            persistent_directory=Path(
                os.getenv("HLTV_PERSISTENT_DIRECTORY", "/data")
            ),
            production=production,
            sqlite_busy_timeout_ms=_int(
                "HLTV_SQLITE_BUSY_TIMEOUT_MS", 5_000, minimum=100
            ),
            raw_evidence_retention_days=_int(
                "HLTV_RAW_EVIDENCE_RETENTION_DAYS", 90, minimum=0
            ),
            failed_evidence_retention_days=_int(
                "HLTV_FAILED_EVIDENCE_RETENTION_DAYS", 180, minimum=0
            ),
            normalized_history_retention_days=_int(
                "HLTV_NORMALIZED_HISTORY_RETENTION_DAYS", 0, minimum=0
            ),
            retention_batch_size=_int(
                "HLTV_RETENTION_BATCH_SIZE", 250, minimum=1
            ),
        )
