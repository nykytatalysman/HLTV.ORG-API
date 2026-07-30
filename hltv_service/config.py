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
    enabled_regions: tuple[str, ...] = ()
    log_level: str = "INFO"
    api_token: str = ""
    max_stale_seconds: int = 86_400
    allow_raw_evidence: bool = False
    retry_attempts: int = 3

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
            enabled_regions=regions,
            log_level=os.getenv("HLTV_LOG_LEVEL", "INFO").strip().upper(),
            api_token=os.getenv("HLTV_SERVICE_TOKEN", ""),
            max_stale_seconds=_int(
                "HLTV_MAX_STALE_SECONDS", 86_400, minimum=0
            ),
            allow_raw_evidence=_bool("HLTV_ALLOW_RAW_EVIDENCE", False),
            retry_attempts=_int("HLTV_RETRY_ATTEMPTS", 3, minimum=1),
        )
