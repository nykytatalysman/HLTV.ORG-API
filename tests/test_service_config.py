from pathlib import Path

import pytest

from hltv_service.config import ServiceConfig


def test_environment_configuration_is_typed_and_bounded(monkeypatch, tmp_path):
    database = tmp_path / "cache.sqlite"
    values = {
        "HLTV_DATABASE_PATH": str(database),
        "HLTV_BROWSER": "Firefox",
        "HLTV_HEADLESS": "false",
        "HLTV_MINIMUM_REQUEST_INTERVAL": "4.5",
        "HLTV_PAGE_TIMEOUT": "45",
        "HLTV_TEAM_PROFILE_TTL_SECONDS": "3600",
        "HLTV_MAXIMUM_TEAM_PROFILES_PER_RUN": "7",
        "HLTV_ENABLED_REGIONS": "Europe, Denmark",
        "HLTV_LOG_LEVEL": "debug",
        "HLTV_SERVICE_TOKEN": "secret",
        "HLTV_MAX_STALE_SECONDS": "120",
        "HLTV_ALLOW_RAW_EVIDENCE": "true",
        "HLTV_RETRY_ATTEMPTS": "2",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    config = ServiceConfig.from_env()
    assert config.database_path == Path(database)
    assert config.browser == "firefox"
    assert config.headless is False
    assert config.minimum_request_interval == 4.5
    assert config.enabled_regions == ("Europe", "Denmark")
    assert config.allow_raw_evidence is True


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("HLTV_HEADLESS", "maybe"),
        ("HLTV_MINIMUM_REQUEST_INTERVAL", "0"),
        ("HLTV_RETRY_ATTEMPTS", "0"),
    ],
)
def test_invalid_environment_values_fail_closed(monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError):
        ServiceConfig.from_env()


def test_personal_chrome_profile_path_is_rejected(monkeypatch):
    monkeypatch.setenv(
        "HLTV_BROWSER_PROFILE_PATH",
        r"C:\Users\person\AppData\Local\Google\Chrome\User Data",
    )
    with pytest.raises(ValueError, match="dedicated service profile"):
        ServiceConfig.from_env()
