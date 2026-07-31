from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from hltv_service.admin import verify_restore
from hltv_service.app import create_app
from hltv_service.config import ServiceConfig
from hltv_service.runtime import RuntimeController, SchedulerController
from hltv_service.schemas import RankingEntry, RankingTeam
from hltv_service.storage import Storage


def config(database: Path, **updates: object) -> ServiceConfig:
    values: dict[str, object] = {
        "database_path": database,
        "runtime_mode": "api",
        "scheduler_enabled": False,
    }
    values.update(updates)
    return ServiceConfig(**values)  # type: ignore[arg-type]


def ranking(snapshot_id: str, observed_at: datetime) -> RankingEntry:
    return RankingEntry(
        ranking_date=observed_at,
        region="World",
        position=1,
        points=900,
        team=RankingTeam(
            provider_team_id=100,
            name="Fixture",
            provider_url="https://www.hltv.org/team/100/fixture",
        ),
        observed_at=observed_at,
        source_snapshot_id=snapshot_id,
    )


def test_sqlite_enables_wal_foreign_keys_and_busy_timeout(tmp_path) -> None:
    store = Storage(tmp_path / "service.sqlite", busy_timeout_ms=3210)
    assert store.connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert store.connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert store.connection.execute("PRAGMA busy_timeout").fetchone()[0] == 3210
    store.close()


def test_concurrent_api_reads_remain_available_during_writes(tmp_path) -> None:
    database = tmp_path / "service.sqlite"
    store = Storage(database)
    seed_time = datetime(2025, 12, 31, tzinfo=UTC)
    seed_snapshot = store.save_raw_snapshot(
        requested_url="https://www.hltv.org/ranking/teams/seed",
        final_url="https://www.hltv.org/ranking/teams/seed",
        page_type="rankings",
        navigation_outcome="success",
        title="fixture",
        captured_at=seed_time,
        html="<html>seed</html>",
    )
    assert store.insert_model("ranking", ranking(seed_snapshot, seed_time))
    app = create_app(config(database), store)
    failures: list[BaseException] = []

    def writer() -> None:
        try:
            writer_store = Storage(database)
            for index in range(25):
                observed = datetime(2026, 1, 1, tzinfo=UTC) + timedelta(
                    minutes=index
                )
                snapshot = writer_store.save_raw_snapshot(
                    requested_url=f"https://www.hltv.org/ranking/teams/{index}",
                    final_url=f"https://www.hltv.org/ranking/teams/{index}",
                    page_type="rankings",
                    navigation_outcome="success",
                    title="fixture",
                    captured_at=observed,
                    html=f"<html>{index}</html>",
                )
                writer_store.insert_model("ranking", ranking(snapshot, observed))
            writer_store.close()
        except BaseException as exc:
            failures.append(exc)

    thread = threading.Thread(target=writer)
    thread.start()
    with TestClient(app) as client:
        while thread.is_alive():
            response = client.get("/v1/rankings")
            assert response.status_code == 200
    thread.join()
    assert failures == []
    assert len(store.history("ranking", "provider_team_id", 100, 100, 0)) == 26
    store.close()


def test_expired_lock_is_reclaimed_and_overlap_is_rejected(tmp_path) -> None:
    store = Storage(tmp_path / "service.sqlite")
    then = datetime(2026, 1, 1, tzinfo=UTC)
    assert store.acquire_worker_lock("one", acquired_at=then, ttl_seconds=300)
    assert not store.acquire_worker_lock(
        "two", acquired_at=then + timedelta(seconds=1), ttl_seconds=300
    )
    assert store.acquire_worker_lock(
        "two", acquired_at=then + timedelta(seconds=301), ttl_seconds=300
    )
    store.close()


def test_retention_backup_integrity_and_restore_verification(tmp_path) -> None:
    store = Storage(tmp_path / "service.sqlite")
    old = datetime(2020, 1, 1, tzinfo=UTC)
    snapshot = store.save_raw_snapshot(
        requested_url="https://www.hltv.org/matches",
        final_url="https://www.hltv.org/matches",
        page_type="matches",
        navigation_outcome="success",
        title="fixture",
        captured_at=old,
        html="<html>old</html>",
    )
    store.save_parse_result(snapshot, "success")
    result = store.retain_raw_evidence(
        ordinary_days=90,
        failed_days=180,
        batch_size=10,
        now=datetime(2026, 1, 1, tzinfo=UTC),
    )
    assert result == {"scanned": 1, "deleted": 1}
    assert store.integrity_check() == "ok"
    backup = store.backup(tmp_path / "backups" / "verified.sqlite")
    assert verify_restore(backup)["status"] == "ok"
    store.close()


def test_runtime_modes_validation_and_single_worker_enforcement(tmp_path) -> None:
    database = tmp_path / "service.sqlite"
    assert config(database).runtime_mode == "api"
    with pytest.raises(ValueError, match="API-only"):
        ServiceConfig(database_path=database, runtime_mode="api")
    with pytest.raises(ValueError, match="API_WORKERS"):
        ServiceConfig(database_path=database, api_workers=2)
    with pytest.raises(ValueError, match="at least 300"):
        ServiceConfig(database_path=database, scheduler_interval_seconds=299)
    with pytest.raises(ValueError, match="PERSISTENT"):
        ServiceConfig(
            database_path=database,
            persistent_directory=tmp_path / "volume",
            production=True,
        )


def test_scheduler_failure_does_not_change_api_health(tmp_path) -> None:
    settings = ServiceConfig(
        database_path=tmp_path / "service.sqlite",
        initial_refresh=True,
    )
    app = create_app(settings)

    def fail(_config: ServiceConfig) -> dict[str, object]:
        raise RuntimeError("fixture scheduler failure")

    scheduler = SchedulerController(settings, app, cycle=fail)
    scheduler.start()
    assert scheduler.thread is not None
    scheduler.thread.join(timeout=2)
    scheduler.stop()
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200


def test_runtime_signal_stops_scheduler_and_server(tmp_path) -> None:
    controller = RuntimeController(config(tmp_path / "service.sqlite"))
    controller.server = SimpleNamespace(should_exit=False)  # type: ignore[assignment]
    controller.request_shutdown(15)
    assert controller.server.should_exit is True


def test_container_entrypoint_drops_privileges_after_safe_mount_setup() -> None:
    script = Path("docker-entrypoint.sh").read_text()
    assert 'install -d -m 0750 -o hltv -g hltv "$database_directory"' in script
    assert 'exec gosu hltv "$@"' in script
    assert "Refusing to prepare the filesystem root" in script
