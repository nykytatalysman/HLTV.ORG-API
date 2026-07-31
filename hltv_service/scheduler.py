"""Bounded single-writer scheduler for cache refreshes."""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from collections.abc import Callable
from typing import Any

from .config import ServiceConfig
from .storage import Storage
from .worker import (
    IngestionAlreadyRunning,
    IngestionBlocked,
    IngestionWorker,
)

LOGGER = logging.getLogger(__name__)


def run_cycle(worker_factory: Callable[[], IngestionWorker]) -> dict[str, Any]:
    """Run one refresh through an injected worker factory."""
    worker = worker_factory()
    return worker.run("refresh")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Schedule bounded HLTV cache refreshes"
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run one refresh cycle and exit.",
    )
    return parser


def _production_cycle(config: ServiceConfig) -> dict[str, Any]:
    from HLTV.browser import SeleniumFetcher

    storage = Storage(
        config.database_path,
        busy_timeout_ms=config.sqlite_busy_timeout_ms,
    )
    fetcher = SeleniumFetcher(
        browser=config.browser,
        headless=config.headless,
        timeout=config.page_timeout,
        min_interval=config.minimum_request_interval,
        profile_dir=config.browser_profile_path or None,
    )
    try:
        return IngestionWorker(storage, config, fetcher=fetcher).run("refresh")
    finally:
        fetcher.close()
        storage.close()


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    config = ServiceConfig.from_env()
    logging.basicConfig(
        level=getattr(logging, config.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    while True:
        exit_code = 0
        try:
            print(json.dumps(_production_cycle(config), sort_keys=True))
        except IngestionBlocked as exc:
            exit_code = 2
            LOGGER.error(
                "HLTV ingestion is blocked; retaining cache until next "
                "scheduled cycle: %s",
                exc,
            )
        except IngestionAlreadyRunning as exc:
            exit_code = 3
            LOGGER.warning("HLTV refresh skipped because a worker is active: %s", exc)
        except Exception as exc:
            exit_code = 1
            LOGGER.exception("HLTV scheduled refresh failed: %s", exc)
        if args.once:
            return exit_code
        time.sleep(config.scheduler_interval_seconds)


if __name__ == "__main__":
    sys.exit(main())
