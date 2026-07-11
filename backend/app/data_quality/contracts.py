from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from hashlib import sha256
import json
from typing import Any, Iterable


MAX_SAMPLE_ISSUES = 20
SUPPORTED_SCOPES = {"a_share_cross_section", "etf_time_series"}
SUPPORTED_DATASETS = {
    "trade_calendars",
    "stock_listings",
    "stock_daily_bars",
    "stock_adjust_factors",
    "stock_limit_prices",
    "stock_suspend_events",
    "stock_daily_basic",
    "stock_financial_indicators",
    "funds",
    "fund_daily_bars",
    "fund_adjust_factors",
    "indices",
    "index_daily_bars",
}
BASE_DATASETS = {
    "a_share_cross_section": {
        "trade_calendars",
        "stock_listings",
        "stock_daily_bars",
        "stock_adjust_factors",
        "stock_limit_prices",
        "stock_suspend_events",
        "indices",
        "index_daily_bars",
    },
    "etf_time_series": {
        "trade_calendars",
        "funds",
        "fund_daily_bars",
        "fund_adjust_factors",
        "indices",
        "index_daily_bars",
    },
}
UNIVERSE_TYPES = {"explicit_snapshot", "static_current", "industry_membership"}


@dataclass(frozen=True)
class QualityCheckContract:
    scope: str
    start_date: date
    end_date: date
    universe: tuple[str, ...]
    required_datasets: tuple[str, ...]
    benchmark: str | None
    universe_type: str
    universe_source: str | None
    universe_as_of_date: date | None
    statement_timeout_ms: int
    universe_hash: str

    @classmethod
    def create(
        cls,
        *,
        scope: str,
        start_date: date,
        end_date: date,
        universe: Iterable[str],
        required_datasets: Iterable[str] = (),
        benchmark: str | None = None,
        universe_type: str = "explicit_snapshot",
        universe_source: str | None = None,
        universe_as_of_date: date | None = None,
        statement_timeout_ms: int = 30_000,
    ) -> "QualityCheckContract":
        normalized_universe = tuple(sorted({str(code).strip().upper() for code in universe if str(code).strip()}))
        normalized_required = tuple(sorted({str(name).strip() for name in required_datasets if str(name).strip()}))
        if scope not in SUPPORTED_SCOPES:
            raise ValueError(f"未知质量范围：{scope}")
        if start_date > end_date:
            raise ValueError("start_date 不能晚于 end_date")
        if not normalized_universe:
            raise ValueError("universe 必须显式提供至少一个代码")
        if len(normalized_universe) > 5000:
            raise ValueError("universe 最多允许 5000 个代码")
        unknown_datasets = sorted(set(normalized_required) - SUPPORTED_DATASETS)
        if unknown_datasets:
            raise ValueError(f"未知数据集：{', '.join(unknown_datasets)}")
        allowed_datasets = set(BASE_DATASETS[scope])
        if scope == "a_share_cross_section":
            allowed_datasets.update({"stock_daily_basic", "stock_financial_indicators"})
        incompatible = sorted(set(normalized_required) - allowed_datasets)
        if incompatible:
            raise ValueError(f"当前 scope 不支持数据集：{', '.join(incompatible)}")
        if universe_type not in UNIVERSE_TYPES:
            raise ValueError(f"未知 universe_type：{universe_type}")
        if not 500 <= int(statement_timeout_ms) <= 60_000:
            raise ValueError("statement_timeout_ms 必须在 500 到 60000 之间")

        normalized_benchmark = benchmark.strip().upper() if benchmark and benchmark.strip() else None
        normalized_source = universe_source.strip() if universe_source and universe_source.strip() else None
        if normalized_source and len(normalized_source) > 200:
            raise ValueError("universe_source 最多 200 个字符")
        universe_payload = json.dumps(
            {
                "type": universe_type,
                "source": normalized_source,
                "asOfDate": universe_as_of_date.isoformat() if universe_as_of_date else None,
                "codes": normalized_universe,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return cls(
            scope=scope,
            start_date=start_date,
            end_date=end_date,
            universe=normalized_universe,
            required_datasets=normalized_required,
            benchmark=normalized_benchmark,
            universe_type=universe_type,
            universe_source=normalized_source,
            universe_as_of_date=universe_as_of_date,
            statement_timeout_ms=int(statement_timeout_ms),
            universe_hash=sha256(universe_payload.encode("utf-8")).hexdigest(),
        )

    @property
    def datasets(self) -> tuple[str, ...]:
        return tuple(sorted(BASE_DATASETS[self.scope] | set(self.required_datasets)))

    def to_config(self) -> dict[str, Any]:
        return {
            "scope": self.scope,
            "startDate": self.start_date.isoformat(),
            "endDate": self.end_date.isoformat(),
            "universe": list(self.universe),
            "universeType": self.universe_type,
            "universeSource": self.universe_source,
            "universeAsOfDate": self.universe_as_of_date.isoformat() if self.universe_as_of_date else None,
            "universeHash": self.universe_hash,
            "requiredDatasets": list(self.required_datasets),
            "effectiveDatasets": list(self.datasets),
            "benchmark": self.benchmark,
            "statementTimeoutMs": self.statement_timeout_ms,
        }


@dataclass
class QualityRuleResult:
    rule_id: str
    table_name: str
    severity: str
    status: str
    checked_rows: int = 0
    failed_rows: int = 0
    sample_issues: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.checked_rows = max(0, int(self.checked_rows or 0))
        self.failed_rows = max(0, int(self.failed_rows or 0))
        self.sample_issues = list(self.sample_issues[:MAX_SAMPLE_ISSUES])
        if self.severity not in {"blocker", "warning", "info"}:
            raise ValueError(f"未知规则级别：{self.severity}")
        if self.status not in {"passed", "warning", "blocked", "failed"}:
            raise ValueError(f"未知规则状态：{self.status}")

    @classmethod
    def passed(cls, rule_id: str, table_name: str, *, checked_rows: int = 0) -> "QualityRuleResult":
        return cls(rule_id=rule_id, table_name=table_name, severity="info", status="passed", checked_rows=checked_rows)

    @classmethod
    def warning(
        cls,
        rule_id: str,
        table_name: str,
        *,
        checked_rows: int = 0,
        failed_rows: int = 0,
        sample_issues: list[dict[str, Any]] | None = None,
    ) -> "QualityRuleResult":
        return cls(
            rule_id=rule_id,
            table_name=table_name,
            severity="warning",
            status="warning",
            checked_rows=checked_rows,
            failed_rows=failed_rows,
            sample_issues=sample_issues or [],
        )

    @classmethod
    def blocked(
        cls,
        rule_id: str,
        table_name: str,
        *,
        checked_rows: int = 0,
        failed_rows: int = 0,
        sample_issues: list[dict[str, Any]] | None = None,
    ) -> "QualityRuleResult":
        return cls(
            rule_id=rule_id,
            table_name=table_name,
            severity="blocker",
            status="blocked",
            checked_rows=checked_rows,
            failed_rows=failed_rows,
            sample_issues=sample_issues or [],
        )

    @classmethod
    def failed(cls, rule_id: str, table_name: str, message: str) -> "QualityRuleResult":
        return cls(
            rule_id=rule_id,
            table_name=table_name,
            severity="blocker",
            status="failed",
            failed_rows=0,
            sample_issues=[{"error": str(message)[:500]}],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "ruleId": self.rule_id,
            "tableName": self.table_name,
            "severity": self.severity,
            "status": self.status,
            "checkedRows": self.checked_rows,
            "failedRows": self.failed_rows,
            "sampleIssues": self.sample_issues,
        }


def summarize_quality_status(results: Iterable[QualityRuleResult]) -> str:
    statuses = {result.status for result in results}
    if "failed" in statuses:
        return "failed"
    if "blocked" in statuses:
        return "blocked"
    if "warning" in statuses:
        return "ready_with_warnings"
    return "ready"


def result_reference(result: QualityRuleResult) -> str:
    return f"{result.rule_id}:{result.table_name}"
