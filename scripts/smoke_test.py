"""Manual, single-request live smoke test. Never run this from routine CI."""

from __future__ import annotations

from HLTV.browser import SeleniumFetcher
from HLTV.parsers import BASE_URL, parse_rankings


def main() -> None:
    with SeleniumFetcher(
        browser="chrome", headless=False, min_interval=5, timeout=120
    ) as fetcher:
        page = fetcher.fetch(f"{BASE_URL}/ranking/teams")
        rankings = parse_rankings(page.html, limit=3)
        print(
            {
                "final_url": page.url,
                "teams": [team.name for team in rankings.rankings],
            }
        )


if __name__ == "__main__":
    main()
