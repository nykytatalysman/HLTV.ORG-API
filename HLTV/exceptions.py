"""Package-specific exceptions."""


class HLTVError(RuntimeError):
    """Base exception for the package."""


class HLTVValidationError(HLTVError, ValueError):
    """Raised when an argument cannot produce a valid HLTV request."""


class HLTVNavigationError(HLTVError):
    """Raised when the browser cannot load a requested page."""


class HLTVBlockedError(HLTVNavigationError):
    """Raised when HLTV or Cloudflare blocks the browser session."""


class HLTVNotFoundError(HLTVError, LookupError):
    """Raised when a requested team or article cannot be found."""


class HLTVParseError(HLTVError):
    """Raised when a loaded HLTV page no longer matches known structures."""
