from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from hashlib import sha256
import json
from typing import Any, Callable, Mapping

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from .ingestion_contracts import IngestionAction
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


class EmptyPayload(BaseModel):
    pass


class UnknownIngestionActionError(ValueError):
    """The caller supplied an action that is not in the static registry."""


class InvalidIngestionResultError(ValueError):
    """An executor returned a result outside the stable ingestion contract."""


@dataclass(frozen=True)
class ActionMetadata:
    is_experimental: bool = False
    research_eligible: bool = True
    execution_enabled: bool = True


@dataclass(frozen=True)
class IngestionCommand:
    action: IngestionAction
    payload: dict[str, Any]
    payload_hash: str


ProviderFactory = Callable[[str | None], Any]
Executor = Callable[[IngestionCommand, Session, ProviderFactory, Mapping[str, Any]], dict[str, Any]]


@dataclass(frozen=True)
class ActionSpec:
    identity: IngestionAction
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
    from .main import execute_legacy_sync_job_action

    return execute_legacy_sync_job_action(command.action.value, command.payload, db)


def _trade_calendar_executor(
    command: IngestionCommand,
    db: Session,
    provider_factory: ProviderFactory,
    secrets: Mapping[str, Any],
) -> dict[str, Any]:
    payload = SyncTradeCalendarRequest.model_validate(command.payload)
    pro = provider_factory(_secret_value(secrets, "token"))
    exchange = payload.exchange or ""
    frame = pro.trade_cal(
        exchange=exchange,
        start_date=tushare_date(payload.start_date),
        end_date=tushare_date(payload.end_date),
        fields="exchange,cal_date,is_open,pretrade_date",
    )
    rows = [
        row
        for item in frame.to_dict("records")
        if (row := trade_calendar_record_to_row(item, fallback_exchange=exchange or "SSE"))
    ]
    rows = _dedupe_rows(rows, ("exchange", "cal_date"))
    upserted = _upsert_trade_calendar_rows(db, rows)
    _record_trade_calendar_run(db, payload.start_date, payload.end_date, upserted)
    return {"status": "ok", "rows_upserted": upserted}


_DEFAULT_SECRETS = frozenset({"token"})
_EXPERIMENTAL = ActionMetadata(
    is_experimental=True,
    research_eligible=False,
    execution_enabled=False,
)

# action identity、payload contract、secret、executor 和 projection metadata 的唯一登记点。
ACTION_REGISTRY: dict[IngestionAction, ActionSpec] = {
    IngestionAction.STOCK_LISTINGS: ActionSpec(IngestionAction.STOCK_LISTINGS, SyncStockListingsRequest, _DEFAULT_SECRETS, _legacy_executor),
    IngestionAction.TRADE_CALENDAR: ActionSpec(
        IngestionAction.TRADE_CALENDAR,
        SyncTradeCalendarRequest,
        _DEFAULT_SECRETS,
        _trade_calendar_executor,
        allow_sync_http=True,
    ),
    IngestionAction.MARKET_BUNDLE: ActionSpec(IngestionAction.MARKET_BUNDLE, SyncMarketDataRequest, _DEFAULT_SECRETS, _legacy_executor),
    IngestionAction.DAILY_MARKET: ActionSpec(IngestionAction.DAILY_MARKET, SyncMarketDataRequest, _DEFAULT_SECRETS, _legacy_executor),
    IngestionAction.MARKET_FUNDAMENTALS: ActionSpec(IngestionAction.MARKET_FUNDAMENTALS, SyncMarketFundamentalsRequest, _DEFAULT_SECRETS, _legacy_executor),
    IngestionAction.US_SAMPLE: ActionSpec(IngestionAction.US_SAMPLE, EmptyPayload, _DEFAULT_SECRETS, _legacy_executor),
    IngestionAction.US_EXPERIMENT_UNIVERSE: ActionSpec(IngestionAction.US_EXPERIMENT_UNIVERSE, EmptyPayload, _DEFAULT_SECRETS, _legacy_executor, _EXPERIMENTAL),
    IngestionAction.US_EXPERIMENT_TARGETED_UNIVERSE: ActionSpec(IngestionAction.US_EXPERIMENT_TARGETED_UNIVERSE, SyncUsExperimentTargetedUniverseRequest, _DEFAULT_SECRETS, _legacy_executor, _EXPERIMENTAL),
    IngestionAction.US_EXPERIMENT_PRICES: ActionSpec(IngestionAction.US_EXPERIMENT_PRICES, SyncUsExperimentPricesRequest, _DEFAULT_SECRETS, _legacy_executor, _EXPERIMENTAL),
    IngestionAction.US_EXPERIMENT_OVERVIEW_REFRESH: ActionSpec(IngestionAction.US_EXPERIMENT_OVERVIEW_REFRESH, EmptyPayload, _DEFAULT_SECRETS, _legacy_executor, _EXPERIMENTAL),
}


def get_action_spec(action: str | IngestionAction) -> ActionSpec:
    try:
        identity = action if isinstance(action, IngestionAction) else IngestionAction(action)
        return ACTION_REGISTRY[identity]
    except (ValueError, KeyError) as exc:
        raise UnknownIngestionActionError(f"不支持的同步动作: {action}") from exc


def actions_with_metadata(*, is_experimental: bool) -> tuple[str, ...]:
    return tuple(
        action.value
        for action, spec in ACTION_REGISTRY.items()
        if spec.metadata.is_experimental is is_experimental
    )


def build_command(action: str | IngestionAction, raw_payload: Mapping[str, Any]) -> IngestionCommand:
    spec = get_action_spec(action)
    persistent_payload = {
        key: value for key, value in raw_payload.items() if key not in spec.secret_fields
    }
    request = spec.payload_model.model_validate(persistent_payload)
    payload = request.model_dump(mode="json", exclude=spec.secret_fields)
    canonical = json.dumps(
        {"action": spec.identity.value, "payload": payload},
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
    action: str | IngestionAction,
    payload: Mapping[str, Any],
    db: Session,
    *,
    provider_factory: ProviderFactory | None = None,
    secrets: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    command = build_command(action, payload)
    spec = get_action_spec(command.action)
    result = spec.executor(command, db, provider_factory or get_pro_api, secrets or {})
    return normalize_result(result)


def normalize_result(raw_result: Any, *, allow_legacy_missing_status: bool = True) -> dict[str, Any]:
    result = json_safe_value(raw_result)
    if not isinstance(result, dict):
        raise InvalidIngestionResultError("同步执行结果必须是 JSON 对象")
    normalize_status(result.get("status"), allow_legacy_missing=allow_legacy_missing_status)
    rows = result_rows(result)
    if rows < 0:
        raise InvalidIngestionResultError("rows_upserted 不能小于 0")
    return dict(result)


def normalize_status(status: Any, *, allow_legacy_missing: bool = True) -> str:
    if status is None and allow_legacy_missing:
        return "ok"
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


def _upsert_trade_calendar_rows(db: Session, rows: list[dict[str, Any]]) -> int:
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


def _dedupe_rows(
    rows: list[dict[str, Any]], keys: tuple[str, ...]
) -> list[dict[str, Any]]:
    deduped: dict[tuple[Any, ...], dict[str, Any]] = {}
    for row in rows:
        deduped[tuple(row.get(key) for key in keys)] = row
    return list(deduped.values())


def _secret_value(secrets: Mapping[str, Any], key: str) -> str | None:
    value = secrets.get(key)
    return str(value) if value is not None else None
