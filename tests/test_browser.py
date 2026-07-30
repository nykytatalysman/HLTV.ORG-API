from HLTV.browser import Page, is_cloudflare_challenge


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
