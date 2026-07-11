from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Iterable

from sqlalchemy import and_, exists, func, inspect, or_, select
from sqlalchemy.orm import Session

from ..models import (
    Fund,
    FundAdjustFactor,
    FundDailyBar,
    Index as MarketIndex,
    IndexDailyBar,
    StockAdjustFactor,
    StockDailyBar,
    StockDailyBasic,
    StockFinancialIndicator,
    StockLimitPrice,
    StockListing,
    StockSuspendEvent,
    TradeCalendar,
)
from .contracts import MAX_SAMPLE_ISSUES, QualityCheckContract, QualityRuleResult


@dataclass(frozen=True)
class TableSpec:
    model: type[Any]
    natural_key: tuple[str, ...]


TABLE_SPECS: dict[str, TableSpec] = {
    "trade_calendars": TableSpec(TradeCalendar, ("exchange", "cal_date")),
    "stock_listings": TableSpec(StockListing, ("ts_code",)),
    "stock_daily_bars": TableSpec(StockDailyBar, ("ts_code", "trade_date")),
    "stock_adjust_factors": TableSpec(StockAdjustFactor, ("ts_code", "trade_date")),
    "stock_limit_prices": TableSpec(StockLimitPrice, ("ts_code", "trade_date")),
    "stock_suspend_events": TableSpec(StockSuspendEvent, ("ts_code", "trade_date", "suspend_type", "suspend_timing")),
    "stock_daily_basic": TableSpec(StockDailyBasic, ("ts_code", "trade_date")),
    "stock_financial_indicators": TableSpec(StockFinancialIndicator, ("ts_code", "end_date", "ann_date")),
    "funds": TableSpec(Fund, ("ts_code",)),
    "fund_daily_bars": TableSpec(FundDailyBar, ("ts_code", "trade_date")),
    "fund_adjust_factors": TableSpec(FundAdjustFactor, ("ts_code", "trade_date")),
    "indices": TableSpec(MarketIndex, ("ts_code",)),
    "index_daily_bars": TableSpec(IndexDailyBar, ("ts_code", "trade_date")),
}


def evaluate_quality_rules(db: Session, contract: QualityCheckContract) -> list[QualityRuleResult]:
    results = check_schema_contract(db, contract)
    if any(result.status == "blocked" for result in results):
        return results

    results.extend(check_natural_key_uniqueness(db, contract))
    results.extend(check_domain(db, contract))
    results.extend(check_referential_integrity(db, contract))
    results.extend(check_calendar_coverage(db, contract))
    results.extend(check_point_in_time_contract(contract))
    results.extend(check_value_sanity(db, contract))
    results.extend(check_adjustment_continuity(db, contract))
    results.extend(check_freshness(db, contract))
    results.append(check_benchmark_overlap(db, contract))
    results.append(check_universe_provenance(contract))
    return results


def check_schema_contract(db: Session, contract: QualityCheckContract) -> list[QualityRuleResult]:
    inspector = inspect(db.get_bind())
    available_tables = set(inspector.get_table_names())
    results: list[QualityRuleResult] = []
    for table_name in contract.datasets:
        spec = TABLE_SPECS[table_name]
        if table_name not in available_tables:
            results.append(
                QualityRuleResult.blocked(
                    "schema.contract",
                    table_name,
                    failed_rows=1,
                    sample_issues=[{"issue": "missing_table", "tableName": table_name}],
                )
            )
            continue

        reflected = {column["name"]: column for column in inspector.get_columns(table_name)}
        issues: list[dict[str, Any]] = []
        for column in spec.model.__table__.columns:
            actual = reflected.get(column.name)
            if actual is None:
                issues.append({"issue": "missing_column", "column": column.name})
                continue
            expected_affinity = column.type._type_affinity
            actual_affinity = actual["type"]._type_affinity
            if expected_affinity is not actual_affinity:
                issues.append(
                    {
                        "issue": "column_type_mismatch",
                        "column": column.name,
                        "expected": expected_affinity.__name__,
                        "actual": actual_affinity.__name__,
                    }
                )

        unique_keys = _reflected_unique_keys(inspector, table_name)
        if spec.natural_key not in unique_keys:
            issues.append({"issue": "missing_natural_key", "columns": list(spec.natural_key)})

        checked = len(spec.model.__table__.columns) + 1
        results.append(
            _quality_result(
                rule_id="schema.contract",
                table_name=table_name,
                severity="blocker",
                checked_rows=checked,
                failed_rows=len(issues),
                samples=issues,
            )
        )
    return results


def check_natural_key_uniqueness(db: Session, contract: QualityCheckContract) -> list[QualityRuleResult]:
    results: list[QualityRuleResult] = []
    for table_name in contract.datasets:
        spec = TABLE_SPECS[table_name]
        filters = _scope_filters(spec.model, contract)
        key_columns = [getattr(spec.model, name) for name in spec.natural_key]
        checked_rows = _count_rows(db, spec.model, filters)
        grouped = (
            select(*key_columns, func.count().label("duplicate_count"))
            .where(*filters)
            .group_by(*key_columns)
            .having(func.count() > 1)
            .subquery()
        )
        failed_rows = int(db.scalar(select(func.coalesce(func.sum(grouped.c.duplicate_count - 1), 0))) or 0)
        sample_rows = db.execute(select(grouped).order_by(*[grouped.c[name] for name in spec.natural_key]).limit(MAX_SAMPLE_ISSUES)).mappings().all()
        samples = [
            {
                **{_camel_key(name): _json_value(row[name]) for name in spec.natural_key},
                "duplicateCount": int(row["duplicate_count"]),
            }
            for row in sample_rows
        ]
        results.append(
            _quality_result(
                rule_id="uniqueness.natural_key",
                table_name=table_name,
                severity="blocker",
                checked_rows=checked_rows,
                failed_rows=failed_rows,
                samples=samples,
            )
        )
    return results


def check_domain(db: Session, contract: QualityCheckContract) -> list[QualityRuleResult]:
    if contract.scope == "a_share_cross_section":
        return [
            _unregistered_code_result(
                db,
                source_model=StockLimitPrice,
                source_date=StockLimitPrice.trade_date,
                master_model=StockListing,
                table_name="stock_limit_prices",
                contract=contract,
            )
        ]
    return [
        _unregistered_code_result(
            db,
            source_model=model,
            source_date=model.trade_date,
            master_model=Fund,
            table_name=table_name,
            contract=contract,
        )
        for table_name, model in [("fund_daily_bars", FundDailyBar), ("fund_adjust_factors", FundAdjustFactor)]
    ]


def check_referential_integrity(db: Session, contract: QualityCheckContract) -> list[QualityRuleResult]:
    master_model = StockListing if contract.scope == "a_share_cross_section" else Fund
    present = set(db.scalars(select(master_model.ts_code).where(master_model.ts_code.in_(contract.universe))).all())
    missing = sorted(set(contract.universe) - present)
    return [
        _quality_result(
            rule_id="referential.universe",
            table_name=master_model.__tablename__,
            severity="blocker",
            checked_rows=len(contract.universe),
            failed_rows=len(missing),
            samples=[{"tsCode": code, "issue": "missing_master_record"} for code in missing],
        )
    ]


def check_calendar_coverage(db: Session, contract: QualityCheckContract) -> list[QualityRuleResult]:
    open_dates = _open_dates(db, contract)
    if not open_dates:
        return [
            QualityRuleResult.blocked(
                "calendar.open_dates",
                "trade_calendars",
                failed_rows=1,
                sample_issues=[
                    {
                        "startDate": contract.start_date.isoformat(),
                        "endDate": contract.end_date.isoformat(),
                        "issue": "no_open_trade_dates",
                    }
                ],
            )
        ]

    if contract.scope == "a_share_cross_section":
        primary = StockDailyBar
        master = StockListing
        coverage_table = "stock_daily_bars"
    else:
        primary = FundDailyBar
        master = Fund
        coverage_table = "fund_daily_bars"

    results = [
        _calendar_primary_coverage(db, contract, open_dates, primary, master, coverage_table),
        _closed_day_rows(db, contract, primary, coverage_table),
        _outside_listing_rows(db, contract, primary, master, coverage_table),
    ]
    if contract.scope == "a_share_cross_section":
        results.extend(
            [
                _same_key_coverage(
                    db,
                    contract,
                    primary=StockDailyBar,
                    dependent=StockAdjustFactor,
                    table_name="stock_adjust_factors",
                    rule_id="calendar.adjust_factor_coverage",
                ),
                _same_key_coverage(
                    db,
                    contract,
                    primary=StockDailyBar,
                    dependent=StockLimitPrice,
                    table_name="stock_limit_prices",
                    rule_id="calendar.limit_price_coverage",
                ),
            ]
        )
        if "stock_daily_basic" in contract.required_datasets:
            results.append(
                _same_key_coverage(
                    db,
                    contract,
                    primary=StockDailyBar,
                    dependent=StockDailyBasic,
                    table_name="stock_daily_basic",
                    rule_id="calendar.daily_basic_coverage",
                )
            )
        if "stock_financial_indicators" in contract.required_datasets:
            results.append(_financial_coverage(db, contract))
    else:
        results.append(
            _same_key_coverage(
                db,
                contract,
                primary=FundDailyBar,
                dependent=FundAdjustFactor,
                table_name="fund_adjust_factors",
                rule_id="calendar.adjust_factor_coverage",
            )
        )
    return results


def check_value_sanity(db: Session, contract: QualityCheckContract) -> list[QualityRuleResult]:
    primary = StockDailyBar if contract.scope == "a_share_cross_section" else FundDailyBar
    results = [
        _ohlcv_sanity(db, contract, primary, primary.__tablename__),
        _volume_amount_missing(db, contract, primary, primary.__tablename__),
    ]
    if contract.benchmark:
        results.extend(
            [
                _ohlcv_sanity(db, contract, IndexDailyBar, "index_daily_bars"),
                _volume_amount_missing(db, contract, IndexDailyBar, "index_daily_bars"),
            ]
        )
    if contract.scope == "a_share_cross_section":
        invalid_limit = or_(
            StockLimitPrice.up_limit.is_(None),
            StockLimitPrice.down_limit.is_(None),
            StockLimitPrice.up_limit <= 0,
            StockLimitPrice.down_limit <= 0,
            StockLimitPrice.up_limit < StockLimitPrice.down_limit,
        )
        filters = _scope_filters(StockLimitPrice, contract)
        results.append(
            _condition_result(
                db,
                rule_id="value.limit_price_sanity",
                table_name="stock_limit_prices",
                model=StockLimitPrice,
                filters=filters,
                failure_condition=invalid_limit,
                sample_columns=(StockLimitPrice.ts_code, StockLimitPrice.trade_date),
            )
        )
    return results


def check_point_in_time_contract(contract: QualityCheckContract) -> list[QualityRuleResult]:
    if "stock_financial_indicators" not in contract.required_datasets:
        return []
    return [
        QualityRuleResult.blocked(
            "point_in_time.financial_revision_history",
            "stock_financial_indicators",
            checked_rows=len(contract.universe),
            failed_rows=len(contract.universe),
            sample_issues=[
                {
                    "issue": "financial_revision_history_unavailable",
                    "limitation": "vendor_revisions_are_overwritten_by_natural_key_upsert",
                }
            ],
        )
    ]


def check_adjustment_continuity(db: Session, contract: QualityCheckContract) -> list[QualityRuleResult]:
    factor_model = StockAdjustFactor if contract.scope == "a_share_cross_section" else FundAdjustFactor
    table_name = factor_model.__tablename__
    filters = _scope_filters(factor_model, contract)
    invalid = or_(factor_model.adj_factor.is_(None), factor_model.adj_factor <= 0)
    positive = _condition_result(
        db,
        rule_id="adjustment.factor_positive",
        table_name=table_name,
        model=factor_model,
        filters=filters,
        failure_condition=invalid,
        sample_columns=(factor_model.ts_code, factor_model.trade_date),
    )

    windowed = (
        select(
            factor_model.ts_code.label("ts_code"),
            factor_model.trade_date.label("trade_date"),
            factor_model.adj_factor.label("adj_factor"),
            func.lag(factor_model.adj_factor)
            .over(partition_by=factor_model.ts_code, order_by=factor_model.trade_date)
            .label("previous_factor"),
        )
        .where(*filters)
        .subquery()
    )
    jump_condition = and_(
        windowed.c.previous_factor.is_not(None),
        windowed.c.previous_factor > 0,
        windowed.c.adj_factor > 0,
        or_(
            windowed.c.adj_factor > windowed.c.previous_factor * Decimal("1.5"),
            windowed.c.adj_factor * Decimal("1.5") < windowed.c.previous_factor,
        ),
    )
    checked_rows = int(db.scalar(select(func.count()).select_from(windowed)) or 0)
    failed_rows = int(db.scalar(select(func.count()).select_from(windowed).where(jump_condition)) or 0)
    sample_rows = db.execute(
        select(windowed)
        .where(jump_condition)
        .order_by(windowed.c.ts_code, windowed.c.trade_date)
        .limit(MAX_SAMPLE_ISSUES)
    ).mappings().all()
    samples = [
        {
            "tsCode": row["ts_code"],
            "tradeDate": row["trade_date"].isoformat(),
            "previousFactor": _json_value(row["previous_factor"]),
            "adjFactor": _json_value(row["adj_factor"]),
            "limitation": "corporate_action_registry_unavailable",
        }
        for row in sample_rows
    ]
    jump = _quality_result(
        rule_id="adjustment.factor_jump",
        table_name=table_name,
        severity="warning",
        checked_rows=checked_rows,
        failed_rows=failed_rows,
        samples=samples,
    )
    return [positive, jump]


def check_freshness(db: Session, contract: QualityCheckContract) -> list[QualityRuleResult]:
    open_dates = _open_dates(db, contract)
    if not open_dates:
        return []
    expected = _expected_latest_primary_date(db, contract, open_dates)
    if expected is None:
        return []
    if contract.scope == "a_share_cross_section":
        models: list[tuple[str, type[Any], Any]] = [
            ("stock_daily_bars", StockDailyBar, StockDailyBar.trade_date),
            ("stock_adjust_factors", StockAdjustFactor, StockAdjustFactor.trade_date),
            ("stock_limit_prices", StockLimitPrice, StockLimitPrice.trade_date),
        ]
        if "stock_daily_basic" in contract.required_datasets:
            models.append(("stock_daily_basic", StockDailyBasic, StockDailyBasic.trade_date))
    else:
        models = [
            ("fund_daily_bars", FundDailyBar, FundDailyBar.trade_date),
            ("fund_adjust_factors", FundAdjustFactor, FundAdjustFactor.trade_date),
        ]
    results = [_freshness_result(db, contract, table_name, model, date_column, expected) for table_name, model, date_column in models]
    if contract.benchmark:
        results.append(
            _freshness_result(
                db,
                contract,
                "index_daily_bars",
                IndexDailyBar,
                IndexDailyBar.trade_date,
                open_dates[-1],
            )
        )
    return results


def check_benchmark_overlap(db: Session, contract: QualityCheckContract) -> QualityRuleResult:
    open_dates = _open_dates(db, contract)
    if not contract.benchmark:
        return QualityRuleResult.blocked(
            "benchmark.overlap",
            "index_daily_bars",
            checked_rows=len(open_dates),
            failed_rows=max(1, len(open_dates)),
            sample_issues=[{"issue": "benchmark_required"}],
        )
    benchmark_dates = set(
        db.scalars(
            select(IndexDailyBar.trade_date).where(
                IndexDailyBar.ts_code == contract.benchmark,
                IndexDailyBar.trade_date >= contract.start_date,
                IndexDailyBar.trade_date <= contract.end_date,
            )
        ).all()
    )
    missing = sorted(set(open_dates) - benchmark_dates)
    return _quality_result(
        rule_id="benchmark.overlap",
        table_name="index_daily_bars",
        severity="blocker",
        checked_rows=len(open_dates),
        failed_rows=len(missing),
        samples=[{"benchmark": contract.benchmark, "tradeDate": item.isoformat()} for item in missing],
    )


def check_universe_provenance(contract: QualityCheckContract) -> QualityRuleResult:
    if contract.universe_type == "explicit_snapshot":
        issues: list[dict[str, Any]] = []
        if not contract.universe_source:
            issues.append({"issue": "universe_source_required"})
        if not contract.universe_as_of_date:
            issues.append({"issue": "universe_as_of_date_required"})
        elif contract.scope == "a_share_cross_section" and contract.universe_as_of_date > contract.start_date:
            issues.append(
                {
                    "issue": "universe_snapshot_uses_future_membership",
                    "universeAsOfDate": contract.universe_as_of_date.isoformat(),
                    "researchStartDate": contract.start_date.isoformat(),
                }
            )
        return _quality_result(
            rule_id="universe.provenance",
            table_name="stock_listings" if contract.scope == "a_share_cross_section" else "funds",
            severity="blocker",
            checked_rows=len(contract.universe),
            failed_rows=len(issues),
            samples=issues,
        )
    if contract.universe_type == "industry_membership":
        issue = "industry_membership_source_required" if not contract.universe_source else "historical_membership_not_verified_in_quality_slice"
        return QualityRuleResult.blocked(
            "universe.provenance",
            "industry_members",
            checked_rows=len(contract.universe),
            failed_rows=1,
            sample_issues=[{"issue": issue}],
        )
    if contract.scope == "a_share_cross_section" and contract.universe_type == "static_current":
        return QualityRuleResult.blocked(
            "universe.survivorship_risk",
            "stock_listings",
            checked_rows=len(contract.universe),
            failed_rows=len(contract.universe),
            sample_issues=[
                {
                    "universeHash": contract.universe_hash,
                    "limitation": "static_current_universe_cannot_claim_unbiased_research_readiness",
                }
            ],
        )
    if contract.universe_type == "static_current":
        return QualityRuleResult.warning(
            "universe.survivorship_risk",
            "funds",
            checked_rows=len(contract.universe),
            failed_rows=len(contract.universe),
            sample_issues=[
                {
                    "universeHash": contract.universe_hash,
                    "limitation": "static_current_universe_has_survivorship_risk",
                }
            ],
        )
    return QualityRuleResult.passed("universe.provenance", "stock_listings", checked_rows=len(contract.universe))


def _unregistered_code_result(
    db: Session,
    *,
    source_model: type[Any],
    source_date: Any,
    master_model: type[Any],
    table_name: str,
    contract: QualityCheckContract,
) -> QualityRuleResult:
    date_filters = (source_date >= contract.start_date, source_date <= contract.end_date)
    checked_rows, registered_rows = db.execute(
        select(func.count(), func.count(master_model.ts_code))
        .select_from(source_model)
        .outerjoin(master_model, master_model.ts_code == source_model.ts_code)
        .where(*date_filters)
    ).one()
    checked_rows = int(checked_rows or 0)
    failed_rows = checked_rows - int(registered_rows or 0)
    samples = [
        {"tsCode": code, "rows": int(rows)}
        for code, rows in db.execute(
            select(source_model.ts_code, func.count())
            .select_from(source_model)
            .outerjoin(master_model, master_model.ts_code == source_model.ts_code)
            .where(*date_filters, master_model.ts_code.is_(None))
            .group_by(source_model.ts_code)
            .order_by(source_model.ts_code)
            .limit(MAX_SAMPLE_ISSUES)
        ).all()
    ]
    return _quality_result(
        rule_id="domain.unlisted_codes",
        table_name=table_name,
        severity="warning",
        checked_rows=checked_rows,
        failed_rows=failed_rows,
        samples=samples,
    )


def _calendar_primary_coverage(
    db: Session,
    contract: QualityCheckContract,
    open_dates: list[date],
    primary: type[Any],
    master: type[Any],
    table_name: str,
) -> QualityRuleResult:
    master_rows = list(db.scalars(select(master).where(master.ts_code.in_(contract.universe))).all())
    exempt_suspensions: dict[str, set[date]] = {}
    if contract.scope == "a_share_cross_section":
        for code, trade_date, timing in db.execute(
            select(StockSuspendEvent.ts_code, StockSuspendEvent.trade_date, StockSuspendEvent.suspend_timing).where(
                StockSuspendEvent.ts_code.in_(contract.universe),
                StockSuspendEvent.trade_date >= contract.start_date,
                StockSuspendEvent.trade_date <= contract.end_date,
                StockSuspendEvent.suspend_type == "S",
            )
        ).all():
            if _suspension_exempts_daily_bar(timing):
                exempt_suspensions.setdefault(code, set()).add(trade_date)

    open_dates_query = (
        select(TradeCalendar.cal_date.label("trade_date"))
        .where(
            TradeCalendar.exchange == "SSE",
            TradeCalendar.is_open.is_(True),
            TradeCalendar.cal_date >= contract.start_date,
            TradeCalendar.cal_date <= contract.end_date,
        )
        .distinct()
        .subquery()
    )
    count_stmt = (
        select(primary.ts_code, func.count(func.distinct(primary.trade_date)))
        .join(open_dates_query, open_dates_query.c.trade_date == primary.trade_date)
        .join(master, master.ts_code == primary.ts_code)
        .where(primary.ts_code.in_(contract.universe))
        .where(or_(master.list_date.is_(None), primary.trade_date >= master.list_date))
    )
    if hasattr(master, "delist_date"):
        count_stmt = count_stmt.where(or_(master.delist_date.is_(None), primary.trade_date <= master.delist_date))
    actual_counts = dict(db.execute(count_stmt.group_by(primary.ts_code)).all())
    actual_exempt_dates: dict[str, set[date]] = {}
    if contract.scope == "a_share_cross_section":
        for code, trade_date, timing in db.execute(
            select(primary.ts_code, primary.trade_date, StockSuspendEvent.suspend_timing)
            .join(
                StockSuspendEvent,
                and_(
                    StockSuspendEvent.ts_code == primary.ts_code,
                    StockSuspendEvent.trade_date == primary.trade_date,
                    StockSuspendEvent.suspend_type == "S",
                ),
            )
            .where(
                primary.ts_code.in_(contract.universe),
                primary.trade_date >= contract.start_date,
                primary.trade_date <= contract.end_date,
            )
        ).all():
            if _suspension_exempts_daily_bar(timing):
                actual_exempt_dates.setdefault(code, set()).add(trade_date)

    expected_windows: dict[str, tuple[int, int, int, set[date]]] = {}
    eligible_actual_counts: dict[str, int] = {}
    open_date_set = set(open_dates)
    failed_rows = 0
    checked_rows = 0
    for row in master_rows:
        active_start = max(contract.start_date, row.list_date or contract.start_date)
        active_end = contract.end_date
        if hasattr(row, "delist_date") and row.delist_date:
            active_end = min(active_end, row.delist_date)
        left = bisect_left(open_dates, active_start)
        right = bisect_right(open_dates, active_end)
        active_exemptions = {
            trade_date
            for trade_date in exempt_suspensions.get(row.ts_code, set())
            if active_start <= trade_date <= active_end and trade_date in open_date_set
        }
        expected_count = max(0, right - left - len(active_exemptions))
        expected_windows[row.ts_code] = (left, right, expected_count, active_exemptions)
        eligible_actual_counts[row.ts_code] = max(
            0,
            int(actual_counts.get(row.ts_code, 0))
            - len(active_exemptions & actual_exempt_dates.get(row.ts_code, set())),
        )
        checked_rows += expected_count
        failed_rows += max(0, expected_count - eligible_actual_counts[row.ts_code])

    samples: list[dict[str, Any]] = []
    for code in sorted(expected_windows):
        if len(samples) >= MAX_SAMPLE_ISSUES:
            break
        left, right, expected_count, active_exemptions = expected_windows[code]
        if expected_count <= eligible_actual_counts.get(code, 0):
            continue
        actual_dates = set(
            db.scalars(
                select(primary.trade_date).where(
                    primary.ts_code == code,
                    primary.trade_date >= contract.start_date,
                    primary.trade_date <= contract.end_date,
                )
            ).all()
        )
        for trade_date in open_dates[left:right]:
            if trade_date in active_exemptions or trade_date in actual_dates:
                continue
            samples.append({"tsCode": code, "tradeDate": trade_date.isoformat()})
            if len(samples) >= MAX_SAMPLE_ISSUES:
                break

    return _quality_result(
        rule_id="calendar.daily_bar_coverage",
        table_name=table_name,
        severity="blocker",
        checked_rows=checked_rows,
        failed_rows=failed_rows,
        samples=samples,
    )


def _suspension_exempts_daily_bar(timing: str | None) -> bool:
    normalized = str(timing or "").strip().lower()
    if not normalized:
        return True
    return any(
        marker in normalized
        for marker in ("全天", "全日", "盘前", "开盘停牌", "all day", "full day", "pre-open", "before open")
    )


def _closed_day_rows(db: Session, contract: QualityCheckContract, primary: type[Any], table_name: str) -> QualityRuleResult:
    filters = _scope_filters(primary, contract)
    no_open_day = ~exists(
        select(1).where(
            TradeCalendar.exchange == "SSE",
            TradeCalendar.cal_date == primary.trade_date,
            TradeCalendar.is_open.is_(True),
        )
    )
    return _condition_result(
        db,
        rule_id="calendar.closed_day_rows",
        table_name=table_name,
        model=primary,
        filters=filters,
        failure_condition=no_open_day,
        sample_columns=(primary.ts_code, primary.trade_date),
    )


def _outside_listing_rows(
    db: Session,
    contract: QualityCheckContract,
    primary: type[Any],
    master: type[Any],
    table_name: str,
) -> QualityRuleResult:
    filters = _scope_filters(primary, contract)
    condition = master.list_date.is_not(None) & (primary.trade_date < master.list_date)
    if hasattr(master, "delist_date"):
        condition = or_(condition, master.delist_date.is_not(None) & (primary.trade_date > master.delist_date))
    checked_rows = _count_rows(db, primary, filters)
    failed_rows = int(
        db.scalar(
            select(func.count())
            .select_from(primary)
            .join(master, master.ts_code == primary.ts_code)
            .where(*filters, condition)
        )
        or 0
    )
    sample_rows = db.execute(
        select(primary.ts_code, primary.trade_date)
        .join(master, master.ts_code == primary.ts_code)
        .where(*filters, condition)
        .order_by(primary.ts_code, primary.trade_date)
        .limit(MAX_SAMPLE_ISSUES)
    ).all()
    return _quality_result(
        rule_id="calendar.outside_listing",
        table_name=table_name,
        severity="blocker",
        checked_rows=checked_rows,
        failed_rows=failed_rows,
        samples=[{"tsCode": code, "tradeDate": trade_date.isoformat()} for code, trade_date in sample_rows],
    )


def _same_key_coverage(
    db: Session,
    contract: QualityCheckContract,
    *,
    primary: type[Any],
    dependent: type[Any],
    table_name: str,
    rule_id: str,
) -> QualityRuleResult:
    filters = _scope_filters(primary, contract)
    missing = ~exists(
        select(1).where(
            dependent.ts_code == primary.ts_code,
            dependent.trade_date == primary.trade_date,
        )
    )
    return _condition_result(
        db,
        rule_id=rule_id,
        table_name=table_name,
        model=primary,
        filters=filters,
        failure_condition=missing,
        sample_columns=(primary.ts_code, primary.trade_date),
    )


def _financial_coverage(db: Session, contract: QualityCheckContract) -> QualityRuleResult:
    available_by_end = exists(
        select(1).where(
            TradeCalendar.exchange == "SSE",
            TradeCalendar.is_open.is_(True),
            TradeCalendar.cal_date > StockFinancialIndicator.ann_date,
            TradeCalendar.cal_date <= contract.end_date,
        )
    )
    present = set(
        db.scalars(
            select(StockFinancialIndicator.ts_code)
            .where(
                StockFinancialIndicator.ts_code.in_(contract.universe),
                available_by_end,
            )
            .distinct()
        ).all()
    )
    missing = sorted(set(contract.universe) - present)
    return _quality_result(
        rule_id="calendar.financial_coverage",
        table_name="stock_financial_indicators",
        severity="blocker",
        checked_rows=len(contract.universe),
        failed_rows=len(missing),
        samples=[
            {
                "tsCode": code,
                "availableBy": contract.end_date.isoformat(),
                "policy": "next_open_trade_date_after_ann_date",
            }
            for code in missing
        ],
    )


def _ohlcv_sanity(db: Session, contract: QualityCheckContract, model: type[Any], table_name: str) -> QualityRuleResult:
    invalid = or_(
        model.open.is_(None),
        model.high.is_(None),
        model.low.is_(None),
        model.close.is_(None),
        model.open <= 0,
        model.high <= 0,
        model.low <= 0,
        model.close <= 0,
        model.high < model.open,
        model.high < model.close,
        model.high < model.low,
        model.low > model.open,
        model.low > model.close,
        model.low > model.high,
        model.vol < 0,
        model.amount < 0,
    )
    filters = _scope_filters(model, contract)
    return _condition_result(
        db,
        rule_id="value.ohlcv_sanity",
        table_name=table_name,
        model=model,
        filters=filters,
        failure_condition=invalid,
        sample_columns=(model.ts_code, model.trade_date),
    )


def _volume_amount_missing(db: Session, contract: QualityCheckContract, model: type[Any], table_name: str) -> QualityRuleResult:
    return _condition_result(
        db,
        rule_id="value.volume_amount_missing",
        table_name=table_name,
        model=model,
        filters=_scope_filters(model, contract),
        failure_condition=or_(model.vol.is_(None), model.amount.is_(None)),
        sample_columns=(model.ts_code, model.trade_date),
        severity="warning",
    )


def _freshness_result(
    db: Session,
    contract: QualityCheckContract,
    table_name: str,
    model: type[Any],
    date_column: Any,
    expected: date,
) -> QualityRuleResult:
    filters = _scope_filters(model, contract)
    actual = db.scalar(select(func.max(date_column)).select_from(model).where(*filters))
    failed = int(actual is None or actual < expected)
    return _quality_result(
        rule_id="freshness.coverage",
        table_name=table_name,
        severity="blocker",
        checked_rows=1,
        failed_rows=failed,
        samples=(
            [{"expectedLatestDate": expected.isoformat(), "actualLatestDate": actual.isoformat() if actual else None}]
            if failed
            else []
        ),
    )


def _expected_latest_primary_date(db: Session, contract: QualityCheckContract, open_dates: list[date]) -> date | None:
    master = StockListing if contract.scope == "a_share_cross_section" else Fund
    rows = list(db.scalars(select(master).where(master.ts_code.in_(contract.universe))).all())
    exempt: dict[str, set[date]] = {}
    if contract.scope == "a_share_cross_section":
        for code, trade_date, timing in db.execute(
            select(StockSuspendEvent.ts_code, StockSuspendEvent.trade_date, StockSuspendEvent.suspend_timing).where(
                StockSuspendEvent.ts_code.in_(contract.universe),
                StockSuspendEvent.trade_date >= contract.start_date,
                StockSuspendEvent.trade_date <= contract.end_date,
                StockSuspendEvent.suspend_type == "S",
            )
        ).all():
            if _suspension_exempts_daily_bar(timing):
                exempt.setdefault(code, set()).add(trade_date)
    latest: date | None = None
    for row in rows:
        active_start = max(contract.start_date, row.list_date or contract.start_date)
        active_end = contract.end_date
        if hasattr(row, "delist_date") and row.delist_date:
            active_end = min(active_end, row.delist_date)
        index = bisect_right(open_dates, active_end) - 1
        while index >= 0 and open_dates[index] >= active_start and open_dates[index] in exempt.get(row.ts_code, set()):
            index -= 1
        if index >= 0 and open_dates[index] >= active_start and (latest is None or open_dates[index] > latest):
            latest = open_dates[index]
    return latest


def _condition_result(
    db: Session,
    *,
    rule_id: str,
    table_name: str,
    model: type[Any],
    filters: Iterable[Any],
    failure_condition: Any,
    sample_columns: tuple[Any, ...],
    severity: str = "blocker",
) -> QualityRuleResult:
    filters = tuple(filters)
    checked_rows = _count_rows(db, model, filters)
    failed_rows = int(db.scalar(select(func.count()).select_from(model).where(*filters, failure_condition)) or 0)
    rows = db.execute(
        select(*sample_columns)
        .where(*filters, failure_condition)
        .order_by(*sample_columns)
        .limit(MAX_SAMPLE_ISSUES)
    ).all()
    samples = [
        {_camel_key(column.key): _json_value(value) for column, value in zip(sample_columns, row)}
        for row in rows
    ]
    return _quality_result(
        rule_id=rule_id,
        table_name=table_name,
        severity=severity,
        checked_rows=checked_rows,
        failed_rows=failed_rows,
        samples=samples,
    )


def _quality_result(
    *,
    rule_id: str,
    table_name: str,
    severity: str,
    checked_rows: int,
    failed_rows: int,
    samples: list[dict[str, Any]],
) -> QualityRuleResult:
    if not failed_rows:
        return QualityRuleResult.passed(rule_id, table_name, checked_rows=checked_rows)
    if severity == "warning":
        return QualityRuleResult.warning(
            rule_id,
            table_name,
            checked_rows=checked_rows,
            failed_rows=failed_rows,
            sample_issues=samples,
        )
    return QualityRuleResult.blocked(
        rule_id,
        table_name,
        checked_rows=checked_rows,
        failed_rows=failed_rows,
        sample_issues=samples,
    )


def _scope_filters(model: type[Any], contract: QualityCheckContract) -> tuple[Any, ...]:
    filters: list[Any] = []
    if model is TradeCalendar:
        filters.extend(
            [
                TradeCalendar.exchange == "SSE",
                TradeCalendar.cal_date >= contract.start_date,
                TradeCalendar.cal_date <= contract.end_date,
            ]
        )
        return tuple(filters)
    if model is MarketIndex:
        if contract.benchmark:
            filters.append(MarketIndex.ts_code == contract.benchmark)
        else:
            filters.append(MarketIndex.ts_code == "__benchmark_required__")
        return tuple(filters)
    if model is IndexDailyBar:
        if contract.benchmark:
            filters.append(IndexDailyBar.ts_code == contract.benchmark)
        else:
            filters.append(IndexDailyBar.ts_code == "__benchmark_required__")
    elif hasattr(model, "ts_code"):
        filters.append(model.ts_code.in_(contract.universe))

    if hasattr(model, "trade_date"):
        filters.extend([model.trade_date >= contract.start_date, model.trade_date <= contract.end_date])
    elif model is StockFinancialIndicator:
        filters.append(StockFinancialIndicator.ann_date <= contract.end_date)
    return tuple(filters)


def _open_dates(db: Session, contract: QualityCheckContract) -> list[date]:
    return list(
        db.scalars(
            select(TradeCalendar.cal_date)
            .where(
                TradeCalendar.exchange == "SSE",
                TradeCalendar.is_open.is_(True),
                TradeCalendar.cal_date >= contract.start_date,
                TradeCalendar.cal_date <= contract.end_date,
            )
            .distinct()
            .order_by(TradeCalendar.cal_date)
        ).all()
    )


def _count_rows(db: Session, model: type[Any], filters: Iterable[Any]) -> int:
    return int(db.scalar(select(func.count()).select_from(model).where(*tuple(filters))) or 0)


def _reflected_unique_keys(inspector: Any, table_name: str) -> set[tuple[str, ...]]:
    keys: set[tuple[str, ...]] = set()
    primary = inspector.get_pk_constraint(table_name).get("constrained_columns") or []
    if primary:
        keys.add(tuple(primary))
    for constraint in inspector.get_unique_constraints(table_name):
        columns = constraint.get("column_names") or []
        if columns:
            keys.add(tuple(columns))
    for index in inspector.get_indexes(table_name):
        if index.get("unique") and index.get("column_names"):
            keys.add(tuple(index["column_names"]))
    return keys


def _camel_key(value: str) -> str:
    head, *tail = value.split("_")
    return head + "".join(item[:1].upper() + item[1:] for item in tail)


def _json_value(value: Any) -> Any:
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value
