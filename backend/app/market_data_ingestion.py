from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date
from hashlib import sha256
from typing import Any, Literal, NotRequired, Required, TypedDict

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from .json_safety import json_safe_value
from .models import DataSyncRun, TradeCalendar
from .schemas import (
    SyncMarketDataRequest,
    SyncMarketFundamentalsRequest,
    SyncStockListingsRequest,
    SyncTradeCalendarRequest,
    SyncUsExperimentPricesRequest,
    SyncUsExperimentTargetedUniverseRequest,
)
from .tushare_client import get_pro_api, parse_tushare_date, tushare_date

TRADE_CALENDAR_FIELDS = "exchange,cal_date,is_open,pretrade_date"


class EmptyPayload(BaseModel):
    pass


class UnknownIngestionActionError(ValueError):
    """The caller supplied an action that is not in the static registry."""


class InvalidIngestionResultError(ValueError):
    """An executor returned a result outside the stable ingestion contract."""


class IngestionResult(TypedDict, total=False):
    status: Required[Literal["ok", "partial", "failed"]]
    rows_upserted: Required[int]
    message: NotRequired[str]
    details: NotRequired[dict[str, Any]]
    retryable: NotRequired[bool]


@dataclass(frozen=True)
class ActionMetadata:
    is_experimental: bool = False
    research_eligible: bool = True
    execution_enabled: bool = True


@dataclass(frozen=True)
class IngestionCommand:
    action: str
    payload: dict[str, Any]
    payload_hash: str


ProviderFactory = Callable[[str | None], Any]
Executor = Callable[
    [IngestionCommand, Session, ProviderFactory, Mapping[str, Any]], Mapping[str, Any]
]


@dataclass(frozen=True)
class ActionSpec:
    identity: str
    payload_model: type[BaseModel]
    secret_fields: frozenset[str]
    executor: Executor
    metadata: ActionMetadata = ActionMetadata()
    allow_sync_http: bool = False
    allow_worker: bool = True
    allow_cli: bool = False


def _legacy_executor(
    command: IngestionCommand,
    db: Session,
    _provider_factory: ProviderFactory,
    _secrets: Mapping[str, Any],
) -> dict[str, Any]:
    # Expand 阶段的兼容 adapter；后续 action-family 票逐批替换。
    from .legacy_market_data_ingestion import execute_legacy_sync_job_action

    return normalize_legacy_result(
        execute_legacy_sync_job_action(command.action, command.payload, db)
    )


def _trade_calendar_executor(
    command: IngestionCommand,
    db: Session,
    provider_factory: ProviderFactory,
    secrets: Mapping[str, Any],
) -> IngestionResult:
    payload = SyncTradeCalendarRequest.model_validate(command.payload)
    pro = provider_factory(_secret_value(secrets, "token"))
    exchange = payload.exchange or ""
    frame = pro.trade_cal(
        exchange=exchange,
        start_date=tushare_date(payload.start_date),
        end_date=tushare_date(payload.end_date),
        fields=TRADE_CALENDAR_FIELDS,
    )
    rows = [
        row
        for item in frame.to_dict("records")
        if (
            row := trade_calendar_record_to_row(
                item, fallback_exchange=exchange or "SSE"
            )
        )
    ]
    rows = dedupe_rows(rows, ("exchange", "cal_date"))
    upserted = upsert_trade_calendar_rows(db, rows)
    _record_trade_calendar_run(db, payload.start_date, payload.end_date, upserted)
    return {"status": "ok", "rows_upserted": upserted}


_DEFAULT_SECRETS = frozenset({"token"})
_EXPERIMENTAL = ActionMetadata(
    is_experimental=True,
    research_eligible=False,
    execution_enabled=False,
)

# action identity、payload contract、secret、executor 和 projection metadata 的唯一登记点。
ACTION_SPECS = (
    ActionSpec(
        "stock_listings", SyncStockListingsRequest, _DEFAULT_SECRETS, _legacy_executor
    ),
    ActionSpec(
        "trade_calendar",
        SyncTradeCalendarRequest,
        _DEFAULT_SECRETS,
        _trade_calendar_executor,
        allow_sync_http=True,
    ),
    ActionSpec(
        "market_bundle", SyncMarketDataRequest, _DEFAULT_SECRETS, _legacy_executor
    ),
    ActionSpec(
        "daily_market", SyncMarketDataRequest, _DEFAULT_SECRETS, _legacy_executor
    ),
    ActionSpec(
        "market_fundamentals",
        SyncMarketFundamentalsRequest,
        _DEFAULT_SECRETS,
        _legacy_executor,
    ),
    ActionSpec("us_sample", EmptyPayload, _DEFAULT_SECRETS, _legacy_executor),
    ActionSpec(
        "us_experiment_universe",
        EmptyPayload,
        _DEFAULT_SECRETS,
        _legacy_executor,
        _EXPERIMENTAL,
    ),
    ActionSpec(
        "us_experiment_targeted_universe",
        SyncUsExperimentTargetedUniverseRequest,
        _DEFAULT_SECRETS,
        _legacy_executor,
        _EXPERIMENTAL,
    ),
    ActionSpec(
        "us_experiment_prices",
        SyncUsExperimentPricesRequest,
        _DEFAULT_SECRETS,
        _legacy_executor,
        _EXPERIMENTAL,
    ),
    ActionSpec(
        "us_experiment_overview_refresh",
        EmptyPayload,
        _DEFAULT_SECRETS,
        _legacy_executor,
        _EXPERIMENTAL,
    ),
)
ACTION_REGISTRY = {spec.identity: spec for spec in ACTION_SPECS}


def get_action_spec(action: str) -> ActionSpec:
    try:
        return ACTION_REGISTRY[action]
    except KeyError as exc:
        raise UnknownIngestionActionError(f"不支持的同步动作: {action}") from exc


def projection_metadata(action: str) -> ActionMetadata | None:
    """Return registry-owned projection metadata without breaking legacy job reads."""
    try:
        return get_action_spec(action).metadata
    except UnknownIngestionActionError:
        return None


def actions_with_metadata(*, is_experimental: bool) -> tuple[str, ...]:
    return tuple(
        action
        for action, spec in ACTION_REGISTRY.items()
        if spec.metadata.is_experimental is is_experimental
    )


def common_projection_metadata(actions: tuple[str, ...]) -> dict[str, bool]:
    metadata = {get_action_spec(action).metadata for action in actions}
    if len(metadata) != 1:
        raise ValueError("动作集合没有一致的 projection metadata")
    common = metadata.pop()
    return {
        "isExperimental": common.is_experimental,
        "researchEligible": common.research_eligible,
        "executionEnabled": common.execution_enabled,
    }


def build_command(action: str, raw_payload: Mapping[str, Any]) -> IngestionCommand:
    spec = get_action_spec(action)
    persistent_payload = {
        key: value
        for key, value in raw_payload.items()
        if key not in spec.secret_fields
    }
    request = spec.payload_model.model_validate(persistent_payload)
    payload = request.model_dump(mode="json", exclude=spec.secret_fields)
    canonical = json.dumps(
        {"action": spec.identity, "payload": payload},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return IngestionCommand(
        action=spec.identity,
        payload=payload,
        payload_hash=sha256(canonical.encode("utf-8")).hexdigest(),
    )


def execute_command(
    action: str,
    payload: Mapping[str, Any],
    db: Session,
    *,
    provider_factory: ProviderFactory | None = None,
    secrets: Mapping[str, Any] | None = None,
    executor: Executor | None = None,
) -> dict[str, Any]:
    command = build_command(action, payload)
    spec = get_action_spec(command.action)
    operation = executor or spec.executor
    result = operation(command, db, provider_factory or get_pro_api, secrets or {})
    return normalize_result(result)


def normalize_result(raw_result: Any) -> dict[str, Any]:
    result = json_safe_value(raw_result)
    if not isinstance(result, dict):
        raise InvalidIngestionResultError("同步执行结果必须是 JSON 对象")
    normalized = dict(result)
    normalized["status"] = normalize_status(normalized.get("status"))
    rows = result_rows(normalized)
    if rows < 0:
        raise InvalidIngestionResultError("rows_upserted 不能小于 0")
    return normalized


def normalize_legacy_result(raw_result: Any) -> dict[str, Any]:
    result = json_safe_value(raw_result)
    if not isinstance(result, dict):
        raise InvalidIngestionResultError("同步执行结果必须是 JSON 对象")
    normalized = dict(result)
    normalized.setdefault("status", "ok")
    return normalize_result(normalized)


def normalize_status(status: Any) -> str:
    if status in {"ok", "partial", "failed"}:
        return str(status)
    raise InvalidIngestionResultError(f"未知同步任务状态：{status}")


def result_rows(result: Any) -> int:
    if not isinstance(result, dict):
        return 0
    if "rows_upserted" in result:
        return int(result.get("rows_upserted") or 0)
    summary = result.get("summary")
    if isinstance(summary, dict):
        return sum(int(value or 0) for value in summary.values())
    return 0


def trade_calendar_record_to_row(
    item: dict[str, Any], fallback_exchange: str = "SSE"
) -> dict[str, Any] | None:
    cal_date = parse_tushare_date(item.get("cal_date"))
    if not cal_date:
        return None
    return {
        "exchange": str(item.get("exchange") or fallback_exchange),
        "cal_date": cal_date,
        "is_open": bool(int(item.get("is_open") or 0)),
        "pretrade_date": parse_tushare_date(item.get("pretrade_date")),
    }


def upsert_trade_calendar_rows(db: Session, rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    conflict_columns = ["exchange", "cal_date"]
    if db.bind and db.bind.dialect.name == "postgresql":
        for offset in range(0, len(rows), 1000):
            chunk = rows[offset : offset + 1000]
            statement = pg_insert(TradeCalendar.__table__).values(chunk)
            statement = statement.on_conflict_do_update(
                index_elements=conflict_columns,
                set_={
                    "is_open": statement.excluded.is_open,
                    "pretrade_date": statement.excluded.pretrade_date,
                },
            )
            db.execute(statement)
        db.commit()
        return len(rows)

    for row in rows:
        existing = db.scalar(
            select(TradeCalendar).where(
                TradeCalendar.exchange == row["exchange"],
                TradeCalendar.cal_date == row["cal_date"],
            )
        )
        if existing:
            existing.is_open = row["is_open"]
            existing.pretrade_date = row["pretrade_date"]
        else:
            db.add(TradeCalendar(**row))
    db.commit()
    return len(rows)


def _record_trade_calendar_run(
    db: Session, start_date: date, end_date: date, rows_upserted: int
) -> None:
    db.add(
        DataSyncRun(
            source="tushare",
            target="trade_calendar",
            start_date=start_date,
            end_date=end_date,
            rows_upserted=rows_upserted,
            status="ok",
        )
    )
    db.commit()


def dedupe_rows(
    rows: list[dict[str, Any]], keys: tuple[str, ...]
) -> list[dict[str, Any]]:
    deduped: dict[tuple[Any, ...], dict[str, Any]] = {}
    for row in rows:
        deduped[tuple(row.get(key) for key in keys)] = row
    return list(deduped.values())


def _secret_value(secrets: Mapping[str, Any], key: str) -> str | None:
    value = secrets.get(key)
    return str(value) if value is not None else None
