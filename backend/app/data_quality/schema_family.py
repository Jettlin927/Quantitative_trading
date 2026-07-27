from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import inspect
from sqlalchemy.orm import Session

from ..models import (
    Fund,
    FundAdjustFactor,
    FundDailyBar,
    Index as MarketIndex,
    IndexDailyBar,
    IndustryClassification,
    IndustryMember,
    StockAdjustFactor,
    StockDailyBar,
    StockDailyBasic,
    StockFinancialIndicator,
    StockLimitPrice,
    StockListing,
    StockSuspendEvent,
    TradeCalendar,
)
from .contracts import QualityCheckContract, QualityRuleResult


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
    "stock_suspend_events": TableSpec(
        StockSuspendEvent,
        ("ts_code", "trade_date", "suspend_type", "suspend_timing"),
    ),
    "industry_classifications": TableSpec(IndustryClassification, ("index_code",)),
    "industry_members": TableSpec(IndustryMember, ("index_code", "con_code", "in_date")),
    "stock_daily_basic": TableSpec(StockDailyBasic, ("ts_code", "trade_date")),
    "stock_financial_indicators": TableSpec(
        StockFinancialIndicator,
        ("ts_code", "end_date", "ann_date", "source_revision_sha256"),
    ),
    "funds": TableSpec(Fund, ("ts_code",)),
    "fund_daily_bars": TableSpec(FundDailyBar, ("ts_code", "trade_date")),
    "fund_adjust_factors": TableSpec(FundAdjustFactor, ("ts_code", "trade_date")),
    "indices": TableSpec(MarketIndex, ("ts_code",)),
    "index_daily_bars": TableSpec(IndexDailyBar, ("ts_code", "trade_date")),
}


def evaluate_schema_family(db: Session, contract: QualityCheckContract) -> list[QualityRuleResult]:
    """检查研究切片所需表、列类型和自然键，不产生任何数据库写入。"""

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

        results.append(
            _quality_result(
                rule_id="schema.contract",
                table_name=table_name,
                severity="blocker",
                checked_rows=len(spec.model.__table__.columns) + 1,
                failed_rows=len(issues),
                samples=issues,
            )
        )
    return results


def _reflected_unique_keys(inspector: Any, table_name: str) -> set[tuple[str, ...]]:
    keys: set[tuple[str, ...]] = set()
    primary_key = inspector.get_pk_constraint(table_name).get("constrained_columns") or []
    if primary_key:
        keys.add(tuple(primary_key))
    for constraint in inspector.get_unique_constraints(table_name):
        columns = constraint.get("column_names") or []
        if columns:
            keys.add(tuple(columns))
    for index in inspector.get_indexes(table_name):
        if index.get("unique"):
            columns = index.get("column_names") or []
            if columns:
                keys.add(tuple(columns))
    return keys


def _quality_result(
    *,
    rule_id: str,
    table_name: str,
    severity: str,
    checked_rows: int,
    failed_rows: int,
    samples: list[dict[str, Any]],
) -> QualityRuleResult:
    status = "passed" if failed_rows == 0 else "warning" if severity == "warning" else "blocked"
    return QualityRuleResult(
        rule_id=rule_id,
        table_name=table_name,
        severity="info" if status == "passed" else severity,
        status=status,
        checked_rows=checked_rows,
        failed_rows=failed_rows,
        sample_issues=samples,
    )
