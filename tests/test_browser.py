import pytest

from HLTV.browser import (
    Page,
    SeleniumFetcher,
    is_cloudflare_challenge,
    validate_navigation,
)
from HLTV.exceptions import HLTVNavigationError


def test_detects_cloudflare_titles_case_insensitively():
    assert is_cloudflare_challenge(
        Page(
            url="https://www.hltv.org/?__cf_chl_rt_tk=token",
            html="<html></html>",
            title="Just a moment...",
        )
    )
    assert is_cloudflare_challenge(
        Page(
            url="https://www.hltv.org/",
            html='<script src="/cdn-cgi/challenge-platform/h/g/orchestrate"></script>',
            title="HLTV.org",
        )
    )
    assert is_cloudflare_challenge(
        Page(
            url="https://www.hltv.org/",
            html="<html></html>",
            title="ATTENTION REQUIRED! | CLOUDFLARE",
        )
    )


def test_does_not_flag_normal_hltv_pages():
    assert not is_cloudflare_challenge(
        Page(
            url="https://www.hltv.org/matches",
            html="<main>matches</main>",
            title="Counter-Strike Matches & livescore | HLTV.org",
        )
    )


def test_navigation_validation_requires_intended_hltv_page():
    validate_navigation(
        "https://www.hltv.org/team/6665/astralis",
        "https://www.hltv.org/team/6665/astralis",
    )
    with pytest.raises(HLTVNavigationError):
        validate_navigation(
            "https://www.hltv.org/team/6665/astralis",
            "https://www.hltv.org/",
        )
    with pytest.raises(HLTVNavigationError):
        validate_navigation(
            "https://www.hltv.org/matches",
            "https://example.com/matches",
        )


def test_chromium_configuration_does_not_conceal_automation():
    class Options:
        def __init__(self):
            self.arguments = []
            self.page_load_strategy = None

        def add_argument(self, value):
            self.arguments.append(value)

    options = Options()
    SeleniumFetcher._configure_chromium(options)
    assert all("AutomationControlled" not in value for value in options.arguments)
    assert options.page_load_strategy == "eager"
