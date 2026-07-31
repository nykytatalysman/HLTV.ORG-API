"""Conservative HLTV map-name normalization.

Only established aliases map to a canonical ID. Unknown display values remain
available to callers with a null canonical ID.
"""

from __future__ import annotations

import re

_ALIASES = {
    "ancient": "ancient",
    "anubis": "anubis",
    "cache": "cache",
    "cobblestone": "cobblestone",
    "cbble": "cobblestone",
    "dust2": "dust2",
    "dustii": "dust2",
    "inferno": "inferno",
    "mirage": "mirage",
    "nuke": "nuke",
    "overpass": "overpass",
    "train": "train",
    "vertigo": "vertigo",
}


def canonical_map_id(value: str | None) -> str | None:
    if not value:
        return None
    key = re.sub(r"[^a-z0-9]", "", value.casefold().removeprefix("de_"))
    return _ALIASES.get(key)

