"""Backward-compatible imports for the original single-module package."""

from .api import Matches, News, Teams
from .browser import SeleniumFetcher as hltv

__all__ = ["Matches", "News", "Teams", "hltv"]
