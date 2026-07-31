"""FastAPI read service over the normalized SQLite cache."""

from __future__ import annotations

import hmac
import sqlite3
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
from typing import Annotated, Any, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Path, Query, Request
from fastapi.responses import JSONResponse

from . import PARSER_VERSION, SCHEMA_VERSION, SERVICE_VERSION, V2_SCHEMA_VERSION
from .config import ServiceConfig
from .storage import Storage


def _timestamp(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        raise HTTPException(
            status_code=422, detail="timestamps must include a timezone"
        )
    return value.astimezone(UTC).isoformat()


def _meta(
    records: list[dict[str, Any]],
    config: ServiceConfig,
    *,
    limit: int | None = None,
    offset: int | None = None,
) -> dict[str, Any]:
    observed = [
        datetime.fromisoformat(item["observed_at"])
        for item in records
        if item.get("observed_at")
    ]
    newest = max(observed) if observed else None
    age = (
        max(0, int((datetime.now(UTC) - newest).total_seconds()))
        if newest
        else None
    )
    snapshot_ids = {
        item.get("source_snapshot_id")
        for item in records
        if item.get("source_snapshot_id")
    }
    return {
        "data_age_seconds": age,
        "is_stale": age is None or age > config.max_stale_seconds,
        "source_snapshot_id": (
            next(iter(snapshot_ids)) if len(snapshot_ids) == 1 else None
        ),
        "pagination": (
            {
                "limit": limit,
                "offset": offset,
                "next_offset": offset + limit if len(records) == limit else None,
            }
            if limit is not None and offset is not None
            else None
        ),
    }


def _envelope(
    data: object, meta: dict[str, Any]
) -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "data": data, "meta": meta}


def _v2_response(
    records: list[dict[str, Any]],
    config: ServiceConfig,
    *,
    singular: bool = False,
    limit: int | None = None,
    offset: int | None = None,
) -> dict[str, Any]:
    last_verified_values = [
        datetime.fromisoformat(item["_last_verified_at"])
        for item in records
        if item.get("_last_verified_at")
    ]
    last_verified = max(last_verified_values) if last_verified_values else None
    age = (
        max(0, int((datetime.now(UTC) - last_verified).total_seconds()))
        if last_verified
        else None
    )
    evidence = sorted(
        {
            item["source_snapshot_id"]
            for item in records
            if item.get("source_snapshot_id")
        }
    )
    cleaned = [
        {
            key: value
            for key, value in item.items()
            if not key.startswith("_")
        }
        for item in records
    ]
    return {
        "schema_version": V2_SCHEMA_VERSION,
        "data": (cleaned[0] if singular and cleaned else None)
        if singular
        else cleaned,
        "meta": {
            "data_age_seconds": age,
            "is_stale": age is None or age > config.max_stale_seconds,
            "last_verified_at": (
                last_verified.astimezone(UTC).isoformat()
                if last_verified
                else None
            ),
            "source_snapshot_id": evidence[0] if len(evidence) == 1 else None,
            "evidence_references": evidence,
            "pagination": (
                {
                    "limit": limit,
                    "offset": offset,
                    "next_offset": (
                        offset + limit
                        if limit is not None
                        and offset is not None
                        and len(records) == limit
                        else None
                    ),
                }
                if limit is not None and offset is not None
                else None
            ),
        },
    }


def create_app(
    config: ServiceConfig | None = None, storage: Storage | None = None
) -> FastAPI:
    settings = config or ServiceConfig.from_env()
    owns_storage = storage is None
    cache = storage or Storage(
        settings.database_path,
        busy_timeout_ms=settings.sqlite_busy_timeout_ms,
    )

    @asynccontextmanager
    async def lifespan(_application: FastAPI):
        yield
        if owns_storage:
            cache.close()

    application = FastAPI(
        title="HLTV Data Service",
        version=SERVICE_VERSION,
        description="Read-only normalized HLTV cache. Browser work is worker-only.",
        lifespan=lifespan,
    )
    application.state.storage = cache
    application.state.config = settings
    application.state.runtime_status = {
        "scheduler_running": False,
        "next_scheduled_run": None,
        "browser_process_status": "not_started",
    }

    @application.middleware("http")
    async def correlation_id(request: Request, call_next: Any) -> Any:
        try:
            response = await call_next(request)
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc).casefold():
                raise
            return JSONResponse(
                status_code=503,
                content={
                    "detail": "Cache is temporarily busy; retry shortly"
                },
            )
        request_id = request.headers.get("x-request-id", "")
        if request_id and len(request_id) <= 128:
            response.headers["x-request-id"] = request_id
        return response

    def authorize(
        authorization: Annotated[str | None, Header()] = None,
        x_internal_api_token: Annotated[str | None, Header()] = None,
    ) -> None:
        if not settings.api_token:
            return
        bearer = ""
        if authorization and authorization.casefold().startswith("bearer "):
            bearer = authorization[7:]
        supplied = bearer or (x_internal_api_token or "")
        if not hmac.compare_digest(supplied, settings.api_token):
            raise HTTPException(status_code=401, detail="Invalid service token")

    secured = [Depends(authorize)]

    @application.get("/health")
    def health() -> dict[str, Any]:
        try:
            cache.connection.execute("SELECT 1").fetchone()
            return {"status": "ok", "database_available": True}
        except Exception:
            return JSONResponse(
                status_code=503,
                content={"status": "unavailable", "database_available": False},
            )

    @application.get("/v1/status", dependencies=secured)
    def status() -> dict[str, Any]:
        current = cache.status()
        newest = (
            datetime.fromisoformat(current["newest_observed_at"])
            if current["newest_observed_at"]
            else None
        )
        cache_age = (
            max(0, int((datetime.now(UTC) - newest).total_seconds()))
            if newest
            else None
        )
        return _envelope(
            {
                "service_version": SERVICE_VERSION,
                "database_available": current["database_available"],
                "most_recent_successful_ingestion": current["most_recent_success"],
                "most_recent_attempted_ingestion": current["most_recent_attempt"],
                "most_recent_blocked_ingestion": current["most_recent_blocked"],
                "blocked": current["blocked"],
                "cache_age_seconds": cache_age,
                "parser_version": PARSER_VERSION,
                "data_counts": current["data_counts"],
                "current_lock_owner": (
                    current["current_lock"]["owner_id"]
                    if current["current_lock"]
                    else None
                ),
                "lock_expiry": (
                    current["current_lock"]["expires_at"]
                    if current["current_lock"]
                    else None
                ),
                "scheduler_running": application.state.runtime_status[
                    "scheduler_running"
                ],
                "next_scheduled_run": application.state.runtime_status[
                    "next_scheduled_run"
                ],
                "run_duration_seconds": current[
                    "last_run_duration_seconds"
                ],
                "records_fetched": current["records_fetched"],
                "records_inserted": current["records_inserted"],
                "records_unchanged": current["records_unchanged"],
                "records_rejected": current["records_rejected"],
                "parser_failures_by_page_type": current[
                    "parser_failures_by_page_type"
                ],
                "section_parser_error_counts": current[
                    "section_parser_errors"
                ],
                "cache_freshness_by_data_type": current["cache_freshness"],
                "queue_depth_by_priority": current[
                    "queue_depth_by_priority"
                ],
                "database_file_size": current["database_file_size"],
                "raw_evidence_size": current["raw_evidence_size"],
                "browser_process_status": application.state.runtime_status[
                    "browser_process_status"
                ],
            },
            {
                "data_age_seconds": cache_age,
                "is_stale": cache_age is None
                or cache_age > settings.max_stale_seconds,
                "source_snapshot_id": None,
                "pagination": None,
            },
        )

    @application.get("/v1/operations", dependencies=secured)
    def operations() -> dict[str, Any]:
        current = cache.status()
        return _envelope(
            {
                **current,
                **application.state.runtime_status,
            },
            {
                "data_age_seconds": None,
                "is_stale": False,
                "source_snapshot_id": None,
                "pagination": None,
            },
        )

    def ranking_response(
        ranking_date: str | None,
        region: str | None,
        observed_before: datetime | None,
        limit: int,
        offset: int,
    ) -> dict[str, Any]:
        records = cache.rankings(
            ranking_date=ranking_date,
            region=region,
            observed_before=_timestamp(observed_before),
            limit=limit,
            offset=offset,
        )
        if not records and not cache.has_records("ranking"):
            raise HTTPException(
                status_code=503, detail="No usable ranking cache is available"
            )
        return _envelope(
            records, _meta(records, settings, limit=limit, offset=offset)
        )

    @application.get("/v1/rankings", dependencies=secured)
    def rankings(
        region: Annotated[
            str | None, Query(min_length=1, max_length=80)
        ] = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 30,
        offset: Annotated[int, Query(ge=0)] = 0,
        observed_before: Annotated[
            datetime | None, Query(alias="observed-before")
        ] = None,
    ) -> dict[str, Any]:
        return ranking_response(None, region, observed_before, limit, offset)

    @application.get("/v1/rankings/{ranking_date}", dependencies=secured)
    def rankings_by_date(
        ranking_date: date,
        region: Annotated[
            str | None, Query(min_length=1, max_length=80)
        ] = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 30,
        offset: Annotated[int, Query(ge=0)] = 0,
        observed_before: Annotated[
            datetime | None, Query(alias="observed-before")
        ] = None,
    ) -> dict[str, Any]:
        date_value = datetime.combine(ranking_date, datetime.min.time(), tzinfo=UTC)
        return ranking_response(
            date_value.isoformat().replace("+00:00", "Z"),
            region,
            observed_before,
            limit,
            offset,
        )

    @application.get("/v1/matches", dependencies=secured)
    def matches(
        status: Literal["live", "upcoming", "finished", "postponed", "cancelled"]
        | None = None,
        from_timestamp: Annotated[
            datetime | None, Query(alias="from")
        ] = None,
        to_timestamp: Annotated[datetime | None, Query(alias="to")] = None,
        team_id: Annotated[int | None, Query(ge=1)] = None,
        event_id: Annotated[int | None, Query(ge=1)] = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> dict[str, Any]:
        if from_timestamp and to_timestamp and from_timestamp > to_timestamp:
            raise HTTPException(status_code=422, detail="from must not be after to")
        records = cache.matches(
            status=status,
            from_timestamp=_timestamp(from_timestamp),
            to_timestamp=_timestamp(to_timestamp),
            team_id=team_id,
            event_id=event_id,
            limit=limit,
            offset=offset,
        )
        if not records and not cache.has_records("match"):
            raise HTTPException(
                status_code=503, detail="No usable match cache is available"
            )
        return _envelope(
            records, _meta(records, settings, limit=limit, offset=offset)
        )

    @application.get("/v1/matches/{provider_match_id}", dependencies=secured)
    def match(provider_match_id: int) -> dict[str, Any]:
        record = cache.latest_entity(
            "match", "provider_match_id", provider_match_id
        )
        if record is None:
            raise HTTPException(status_code=404, detail="Match not found")
        return _envelope(record, _meta([record], settings))

    @application.get("/v1/teams/{provider_team_id}", dependencies=secured)
    def team(provider_team_id: int) -> dict[str, Any]:
        record = cache.latest_entity("team", "provider_team_id", provider_team_id)
        if record is None:
            raise HTTPException(status_code=404, detail="Team not found")
        return _envelope(record, _meta([record], settings))

    @application.get("/v1/teams/{provider_team_id}/history", dependencies=secured)
    def team_history(
        provider_team_id: int,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> dict[str, Any]:
        records = cache.history(
            "team", "provider_team_id", provider_team_id, limit, offset
        )
        if not records:
            raise HTTPException(status_code=404, detail="Team not found")
        return _envelope(
            records, _meta(records, settings, limit=limit, offset=offset)
        )

    @application.get("/v1/teams/{provider_team_id}/roster", dependencies=secured)
    def roster(provider_team_id: int) -> dict[str, Any]:
        record = cache.latest_entity(
            "roster", "provider_team_id", provider_team_id
        )
        if record is None:
            raise HTTPException(status_code=404, detail="Roster not found")
        return _envelope(record, _meta([record], settings))

    @application.get("/v1/evidence/{source_snapshot_id}", dependencies=secured)
    def evidence(source_snapshot_id: str) -> dict[str, Any]:
        record = cache.evidence(
            source_snapshot_id, include_html=settings.allow_raw_evidence
        )
        if record is None:
            raise HTTPException(status_code=404, detail="Evidence not found")
        return _envelope(
            record,
            {
                "data_age_seconds": max(
                    0,
                    int(
                        (
                            datetime.now(UTC)
                            - datetime.fromisoformat(record["captured_at"])
                        ).total_seconds()
                    ),
                ),
                "is_stale": False,
                "source_snapshot_id": source_snapshot_id,
                "pagination": None,
            },
        )

    def v2_cutoff(value: datetime | None) -> str | None:
        return _timestamp(value)

    def require_v2(
        records: list[dict[str, Any]], resource: str
    ) -> list[dict[str, Any]]:
        if not records:
            raise HTTPException(
                status_code=503,
                detail=f"No usable cached {resource} is available",
            )
        return records

    @application.get("/v2/matches/{provider_match_id}", dependencies=secured)
    def match_detail(
        provider_match_id: Annotated[int, Path(ge=1)],
        observed_before: Annotated[
            datetime | None, Query(alias="observed-before")
        ] = None,
    ) -> dict[str, Any]:
        records = require_v2(
            cache.match_detail(
                provider_match_id, v2_cutoff(observed_before)
            ),
            "match detail",
        )
        return _v2_response(records, settings, singular=True)

    @application.get(
        "/v2/matches/{provider_match_id}/lineups", dependencies=secured
    )
    def match_lineups(
        provider_match_id: Annotated[int, Path(ge=1)],
        observed_before: Annotated[
            datetime | None, Query(alias="observed-before")
        ] = None,
    ) -> dict[str, Any]:
        records = require_v2(
            cache.match_lineups(
                provider_match_id, v2_cutoff(observed_before)
            ),
            "match lineups",
        )
        return _v2_response(records, settings)

    @application.get(
        "/v2/matches/{provider_match_id}/veto", dependencies=secured
    )
    def match_veto(
        provider_match_id: Annotated[int, Path(ge=1)],
        observed_before: Annotated[
            datetime | None, Query(alias="observed-before")
        ] = None,
    ) -> dict[str, Any]:
        records = require_v2(
            cache.match_vetoes(
                provider_match_id, v2_cutoff(observed_before)
            ),
            "match veto",
        )
        return _v2_response(records, settings)

    @application.get(
        "/v2/matches/{provider_match_id}/maps", dependencies=secured
    )
    def match_maps(
        provider_match_id: Annotated[int, Path(ge=1)],
        observed_before: Annotated[
            datetime | None, Query(alias="observed-before")
        ] = None,
    ) -> dict[str, Any]:
        records = require_v2(
            cache.match_maps(
                provider_match_id, v2_cutoff(observed_before)
            ),
            "match maps",
        )
        return _v2_response(records, settings)

    @application.get(
        "/v2/teams/{provider_team_id}/map-stats", dependencies=secured
    )
    def team_map_stats(
        provider_team_id: Annotated[int, Path(ge=1)],
        observed_before: Annotated[
            datetime | None, Query(alias="observed-before")
        ] = None,
    ) -> dict[str, Any]:
        records = require_v2(
            cache.team_map_stats(
                provider_team_id, v2_cutoff(observed_before)
            ),
            "team map statistics",
        )
        return _v2_response(records, settings)

    @application.get(
        "/v2/teams/{provider_team_id}/results", dependencies=secured
    )
    def team_results(
        provider_team_id: Annotated[int, Path(ge=1)],
        observed_before: Annotated[
            datetime | None, Query(alias="observed-before")
        ] = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> dict[str, Any]:
        records = require_v2(
            cache.team_results(
                provider_team_id,
                v2_cutoff(observed_before),
                limit,
                offset,
            ),
            "team results",
        )
        return _v2_response(
            records, settings, limit=limit, offset=offset
        )

    @application.get("/v2/head-to-head", dependencies=secured)
    def head_to_head(
        team_one_id: Annotated[int, Query(alias="team-one-id", ge=1)],
        team_two_id: Annotated[int, Query(alias="team-two-id", ge=1)],
        observed_before: Annotated[
            datetime | None, Query(alias="observed-before")
        ] = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> dict[str, Any]:
        if team_one_id == team_two_id:
            raise HTTPException(
                status_code=422, detail="Two distinct team IDs are required"
            )
        records = require_v2(
            cache.head_to_head(
                team_one_id,
                team_two_id,
                v2_cutoff(observed_before),
                limit,
                offset,
            ),
            "head-to-head observations",
        )
        return _v2_response(
            records, settings, limit=limit, offset=offset
        )

    @application.get("/v2/events/{provider_event_id}", dependencies=secured)
    def event_detail(
        provider_event_id: Annotated[int, Path(ge=1)],
        observed_before: Annotated[
            datetime | None, Query(alias="observed-before")
        ] = None,
    ) -> dict[str, Any]:
        records = require_v2(
            cache.event_detail(
                provider_event_id, v2_cutoff(observed_before)
            ),
            "event detail",
        )
        return _v2_response(records, settings, singular=True)

    return application


app = create_app()
