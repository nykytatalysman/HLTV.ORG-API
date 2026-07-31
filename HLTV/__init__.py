"""Modern, unofficial Python client for public HLTV.org pages."""

from .api import HLTVClient, Matches, News, Teams
from .exceptions import (
    HLTVBlockedError,
    HLTVDeletedError,
    HLTVError,
    HLTVNavigationError,
    HLTVNotFoundError,
    HLTVParseError,
    HLTVUnavailableError,
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
    "HLTVDeletedError",
    "HLTVNavigationError",
    "HLTVNotFoundError",
    "HLTVParseError",
    "HLTVUnavailableError",
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
