"""Package-specific exceptions."""


class HLTVError(RuntimeError):
    """Base exception for the package."""


class HLTVValidationError(HLTVError, ValueError):
    """Raised when an argument cannot produce a valid HLTV request."""


class HLTVNavigationError(HLTVError):
    """Raised when the browser cannot load a requested page."""


class HLTVBlockedError(HLTVNavigationError):
    """Raised when HLTV or Cloudflare blocks the browser session."""

    def __init__(self, message: str, *, page: object | None = None) -> None:
        super().__init__(message)
        self.page = page


class HLTVNotFoundError(HLTVError, LookupError):
    """Raised when a requested team or article cannot be found."""


class HLTVDeletedError(HLTVNotFoundError):
    """Raised when HLTV identifies a requested match as deleted."""


class HLTVUnavailableError(HLTVNotFoundError):
    """Raised when a requested HLTV page is explicitly unavailable."""


class HLTVParseError(HLTVError):
    """Raised when a loaded HLTV page no longer matches known structures."""

    def __init__(self, message: str, *, parse_state: str = "parser_regression") -> None:
        super().__init__(message)
        self.parse_state = parse_state
