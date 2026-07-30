"""Stable HLTV provider-identifier extraction."""

from __future__ import annotations

import re
from urllib.parse import urlparse

_PATHS = {
    "team": "team",
    "player": "player",
    "match": "matches",
    "event": "events",
    "news": "news",
}


def extract_provider_id(url: str | None, entity_type: str) -> int | None:
    """Extract a positive numeric HLTV ID from an absolute or relative URL."""
    if not url:
        return None
    path_name = _PATHS.get(entity_type)
    if path_name is None:
        raise ValueError(f"Unsupported HLTV entity type: {entity_type}")
    parsed = urlparse(str(url).strip())
    if parsed.scheme and (
        parsed.scheme != "https" or parsed.hostname not in {"hltv.org", "www.hltv.org"}
    ):
        return None
    match = re.match(rf"^/{re.escape(path_name)}/([1-9]\d*)(?:/|$)", parsed.path)
    return int(match.group(1)) if match else None


def extract_team_id(url: str | None) -> int | None:
    return extract_provider_id(url, "team")


def extract_player_id(url: str | None) -> int | None:
    return extract_provider_id(url, "player")


def extract_match_id(url: str | None) -> int | None:
    return extract_provider_id(url, "match")


def extract_event_id(url: str | None) -> int | None:
    return extract_provider_id(url, "event")


def extract_news_id(url: str | None) -> int | None:
    return extract_provider_id(url, "news")
