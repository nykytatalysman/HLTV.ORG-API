"""Modern, unofficial Python client for public HLTV.org pages."""

from .api import HLTVClient, Matches, News, Teams
from .exceptions import (
    HLTVBlockedError,
    HLTVError,
    HLTVNavigationError,
    HLTVNotFoundError,
    HLTVParseError,
    HLTVValidationError,
)
from .models import (
    Match,
    MatchesResult,
    NewsArticle,
    NewsItem,
    NewsList,
    Player,
    RankedTeam,
    TeamProfile,
    TopTeamsResult,
)

__all__ = [
    "HLTVClient",
    "HLTVError",
    "HLTVBlockedError",
    "HLTVNavigationError",
    "HLTVNotFoundError",
    "HLTVParseError",
    "HLTVValidationError",
    "Match",
    "Matches",
    "MatchesResult",
    "News",
    "NewsArticle",
    "NewsItem",
    "NewsList",
    "Player",
    "RankedTeam",
    "TeamProfile",
    "Teams",
    "TopTeamsResult",
]

__version__ = "1.0.0"
