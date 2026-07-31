"""Manual one-navigation live smoke test; never run from automatic CI."""

from __future__ import annotations

import json
import os
import time

from HLTV.browser import SeleniumFetcher
from HLTV.exceptions import HLTVBlockedError
from HLTV.parsers import BASE_URL, parse_rankings
from hltv_service import PARSER_VERSION


def main() -> int:
    started = time.monotonic()
    report: dict[str, object] = {
        "page_type": "rankings",
        "parser_version": PARSER_VERSION,
        "navigation": "failed",
        "blocked": False,
        "parser_success": False,
        "entities_parsed": 0,
    }
    try:
        with SeleniumFetcher(
            browser="chrome",
            headless=os.getenv("HLTV_HEADLESS", "true").casefold() == "true",
            min_interval=5,
            timeout=60,
            profile_dir=None,
        ) as fetcher:
            page = fetcher.fetch(f"{BASE_URL}/ranking/teams")
            report["navigation"] = "success"
            parsed = parse_rankings(page.html, limit=3)
            report["parser_success"] = True
            report["entities_parsed"] = len(parsed.rankings)
        return_code = 0
    except HLTVBlockedError:
        report["navigation"] = "blocked"
        report["blocked"] = True
        return_code = 2
    except Exception as exc:
        report["error_type"] = type(exc).__name__
        return_code = 1
    report["duration_seconds"] = round(time.monotonic() - started, 3)
    print(json.dumps(report, sort_keys=True))
    return return_code


if __name__ == "__main__":
    raise SystemExit(main())
