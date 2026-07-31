"""Production process supervisor for the API and bounded scheduler."""

from __future__ import annotations

import logging
import os
import signal
import sys
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import uvicorn

from .app import create_app
from .config import ServiceConfig
from .scheduler import _production_cycle
from .storage import Storage, canonical_json
from .worker import IngestionAlreadyRunning, IngestionBlocked

LOGGER = logging.getLogger("hltv_service.runtime")


class SchedulerController:
    """Own one scheduler thread without coupling failures to the API."""

    def __init__(
        self,
        config: ServiceConfig,
        application: Any,
        *,
        cycle: Callable[[ServiceConfig], dict[str, Any]] = _production_cycle,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.config = config
        self.application = application
        self.cycle = cycle
        self.now = now
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        if self.thread and self.thread.is_alive():
            raise RuntimeError("The scheduler is already running")
        self.stop_event.clear()
        self.thread = threading.Thread(
            target=self._run,
            name="hltv-ingestion-scheduler",
            daemon=False,
        )
        self.thread.start()

    def _set_next_run(self, value: datetime | None) -> None:
        self.application.state.runtime_status["next_scheduled_run"] = (
            value.astimezone(UTC).isoformat() if value else None
        )

    def _run_once(self) -> None:
        started = self.now()
        status = self.application.state.runtime_status
        status["browser_process_status"] = "worker_starting"
        try:
            summary = self.cycle(self.config)
            LOGGER.info(
                canonical_json(
                    {
                        "event": "scheduled_ingestion",
                        "result": "success",
                        "run_id": summary.get("run_id"),
                        "duration_seconds": (
                            self.now() - started
                        ).total_seconds(),
                        "blocked": False,
                    }
                )
            )
        except IngestionBlocked as exc:
            LOGGER.warning(
                canonical_json(
                    {
                        "event": "scheduled_ingestion",
                        "result": "blocked",
                        "duration_seconds": (
                            self.now() - started
                        ).total_seconds(),
                        "blocked": True,
                        "error_type": type(exc).__name__,
                    }
                )
            )
        except IngestionAlreadyRunning as exc:
            LOGGER.warning(
                "Scheduled ingestion skipped because the lock is held: %s", exc
            )
        except Exception:
            LOGGER.exception("Scheduled ingestion failed; API remains available")
        finally:
            status["browser_process_status"] = "stopped"

    def _run(self) -> None:
        status = self.application.state.runtime_status
        status["scheduler_running"] = True
        try:
            if self.config.initial_refresh and not self.stop_event.is_set():
                self._run_once()
            while not self.stop_event.is_set():
                next_run = self.now() + timedelta(
                    seconds=self.config.scheduler_interval_seconds
                )
                self._set_next_run(next_run)
                if self.stop_event.wait(
                    self.config.scheduler_interval_seconds
                ):
                    break
                self._run_once()
        finally:
            status["scheduler_running"] = False
            status["next_scheduled_run"] = None
            status["browser_process_status"] = "stopped"

    def stop(self, timeout: float = 30.0) -> None:
        self.stop_event.set()
        thread = self.thread
        if thread and thread is not threading.current_thread():
            thread.join(timeout=timeout)
            if thread.is_alive():
                LOGGER.error("Scheduler did not stop within %.1f seconds", timeout)


class RuntimeController:
    """Coordinate Uvicorn shutdown with scheduler and storage cleanup."""

    def __init__(
        self,
        config: ServiceConfig,
        *,
        cycle: Callable[[ServiceConfig], dict[str, Any]] = _production_cycle,
    ) -> None:
        self.config = config
        bootstrap = Storage(
            config.database_path,
            busy_timeout_ms=config.sqlite_busy_timeout_ms,
        )
        bootstrap.close()
        self.application = create_app(config)
        self.scheduler = SchedulerController(
            config, self.application, cycle=cycle
        )
        self.server: uvicorn.Server | None = None

    def request_shutdown(
        self, signum: int | None = None, _frame: object | None = None
    ) -> None:
        LOGGER.info("Runtime shutdown requested signal=%s", signum)
        if self.server:
            self.server.should_exit = True
        self.scheduler.stop()

    def run(self) -> None:
        if (
            self.config.runtime_mode == "combined"
            and self.config.scheduler_enabled
        ):
            self.scheduler.start()
        port = int(os.getenv("PORT", "8000"))
        uvicorn_config = uvicorn.Config(
            self.application,
            host="0.0.0.0",
            port=port,
            workers=self.config.api_workers,
            log_level=self.config.log_level.casefold(),
        )
        self.server = uvicorn.Server(uvicorn_config)
        try:
            signal.signal(signal.SIGTERM, self.request_shutdown)
            signal.signal(signal.SIGINT, self.request_shutdown)
            self.server.run()
        finally:
            self.scheduler.stop()
            self.application.state.storage.close()


def main() -> int:
    config = ServiceConfig.from_env()
    logging.basicConfig(
        level=getattr(logging, config.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    RuntimeController(config).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
