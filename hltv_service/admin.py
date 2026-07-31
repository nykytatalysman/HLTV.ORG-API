"""Bounded database maintenance commands."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

from .config import ServiceConfig
from .storage import SCHEMA_VERSION, Storage


def verify_restore(path: Path) -> dict[str, object]:
    if not path.is_file():
        raise ValueError(f"Backup does not exist: {path}")
    connection = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    try:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        version_row = connection.execute(
            "SELECT MAX(version) FROM schema_migrations"
        ).fetchone()
        version = version_row[0] if version_row else None
    finally:
        connection.close()
    if integrity != "ok":
        raise ValueError(f"Backup integrity check failed: {integrity}")
    if version != SCHEMA_VERSION:
        raise ValueError(
            f"Backup schema version {version!r} does not match {SCHEMA_VERSION}"
        )
    return {
        "status": "ok",
        "path": str(path),
        "schema_version": version,
        "integrity": integrity,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="HLTV SQLite administration")
    commands = parser.add_subparsers(dest="command", required=True)
    backup = commands.add_parser("backup")
    backup.add_argument("--output", type=Path)
    commands.add_parser("integrity-check")
    restore = commands.add_parser("restore-verify")
    restore.add_argument("path", type=Path)
    commands.add_parser("retention")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    config = ServiceConfig.from_env()
    storage = Storage(
        config.database_path,
        busy_timeout_ms=config.sqlite_busy_timeout_ms,
    )
    try:
        if args.command == "backup":
            timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
            output = args.output or (
                config.database_path.parent
                / "backups"
                / f"hltv-service-{timestamp}.sqlite"
            )
            storage.backup(output)
            result = verify_restore(output)
        elif args.command == "integrity-check":
            result = {
                "status": "ok",
                "integrity": storage.integrity_check(),
                "schema_version": SCHEMA_VERSION,
            }
            if result["integrity"] != "ok":
                raise ValueError(
                    f"Database integrity check failed: {result['integrity']}"
                )
        elif args.command == "restore-verify":
            result = verify_restore(args.path)
        else:
            result = storage.retain_raw_evidence(
                ordinary_days=config.raw_evidence_retention_days,
                failed_days=config.failed_evidence_retention_days,
                batch_size=config.retention_batch_size,
            )
        print(json.dumps(result, sort_keys=True))
        return 0
    except (sqlite3.Error, ValueError) as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}), file=sys.stderr)
        return 1
    finally:
        storage.close()


if __name__ == "__main__":
    sys.exit(main())
