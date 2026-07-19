from __future__ import annotations

import argparse
from html import escape
import json
import math
from pathlib import Path
import sys
from typing import Any

import pandas as pd


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.app.quant_research.metrics import summarize_performance
from backend.app.quant_research.reporting import (
    hac_alpha,
    summarize_nav_window,
    summarize_return_subperiod,
    tail_metrics,
)
from scripts.research.report_evidence import (
    canonical_report_timestamp,
    verify_reproduction_evidence,
)
from scripts.research.render_etf_volatility_managed_report import (
    _aligned_returns,
    _build_passive_nav,
    _fmt,
    _html_table,
    _json_safe,
    _line_svg,
    _read_frame,
)


DEFAULT_RUN_ROOT = (
    REPO_ROOT
    / "outputs"
    / "research-runs"
    / "trend-120d-2026-07-19-final"
    / "canonical-runs"
)
DEFAULT_OUTPUT_DIR = (
    REPO_ROOT
    / "docs"
    / "research"
    / "strategy-results"
    / "etf-trend-120d-long-history-20260713"
)
DEFAULT_REPRODUCTION_EVIDENCE = (
    REPO_ROOT
    / "docs"
    / "research"
    / "strategy-results"
    / "reproduction-evidence-20260719.json"
)
INITIAL_CAPITAL = 100_000.0
SCENARIOS = {
    (0.00035, 0.00085, 0.001): "base_cost",
    (0.0, 0.0, 0.0): "zero_cost",
    (0.0007, 0.0017, 0.002): "double_cost",
}
SCENARIO_NAMES = {
    "base_cost": "120日均线趋势策略（基础成本）",
    "zero_cost": "120日均线趋势策略（零成本归因）",
    "double_cost": "120日均线趋势策略（双倍成本压力）",
    "passive": "沪深300 ETF 被动持有",
    "static": "同平均暴露静态 ETF / 现金组合",
}
STRESS_WINDOWS = (
    ("A股2015至2016急跌", "2015-06-12", "2016-02-29"),
    ("2018全年", "2018-01-01", "2018-12-31"),
    ("COVID冲击", "2020-01-23", "2020-04-30"),
    ("2022回撤", "2022-01-01", "2022-10-31"),
    ("2024年初", "2024-01-01", "2024-02-08"),
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="从三个 canonical 运行生成 120 日均线趋势长历史报告。"
    )
    parser.add_argument("--run-root", type=Path, default=DEFAULT_RUN_ROOT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument(
        "--reproduction-evidence",
        type=Path,
        default=DEFAULT_REPRODUCTION_EVIDENCE,
    )
    args = parser.parse_args(argv)

    runs = load_runs(args.run_root)
    reproduction_audit = verify_reproduction_evidence(
        args.reproduction_evidence,
        runs,
    )
    summary, charts = build_summary(runs, reproduction_audit)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    summary_path = args.output_dir / "summary.json"
    report_path = args.output_dir / "index.html"
    summary_path.write_text(
        json.dumps(_json_safe(summary), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    report_path.write_text(render_html(summary, charts), encoding="utf-8")
    print(
        json.dumps(
            {
                "status": summary["status"],
                "summary": str(summary_path),
                "report": str(report_path),
            },
            ensure_ascii=False,
        )
    )
    return 0


def classify_run(config: dict[str, Any]) -> str:
    if config.get("strategyId") != "etf_trend_120d":
        raise ValueError("运行不是 etf_trend_120d@1")
    if config.get("strategyVersion") != "1":
        raise ValueError("趋势策略版本必须为 1")
    if config.get("featureParameters") != {"movingAverageWindow": 120}:
        raise ValueError("报告只接受固定 120 日均线规则")
    costs = tuple(
        float(config["costModel"][field])
        for field in ("buyRate", "sellRate", "slippageRate")
    )
    try:
        label = SCENARIOS[costs]
    except KeyError as exc:
        raise ValueError(f"未登记的成本场景：{costs}") from exc
    filenames = {
        "base_cost": "etf_trend_120d_long_history.json",
        "zero_cost": "etf_trend_120d_long_history_zero_cost.json",
        "double_cost": "etf_trend_120d_long_history_double_cost.json",
    }
    expected = json.loads(
        (REPO_ROOT / "configs" / "research" / filenames[label]).read_text(
            encoding="utf-8"
        )
    )
    normalized = json.loads(json.dumps(config))
    normalized["qualityRunId"] = "__REQUIRED_BY_CLI__"
    if normalized != expected:
        raise ValueError(f"{SCENARIO_NAMES[label]}配置偏离事前登记")
    return label


def load_runs(run_root: Path) -> dict[str, dict[str, Any]]:
    root = Path(run_root).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"canonical run 根目录不存在：{root}")
    runs: dict[str, dict[str, Any]] = {}
    for path in sorted(item for item in root.iterdir() if item.is_dir()):
        required = (path / "config.json", path / "manifest.json", path / "metrics.json")
        if not all(item.is_file() for item in required):
            continue
        config = json.loads(required[0].read_text(encoding="utf-8"))
        if config.get("strategyId") != "etf_trend_120d":
            continue
        label = classify_run(config)
        if label in runs:
            raise ValueError(f"canonical run 成本场景重复：{label}")
        runs[label] = {
            "path": path,
            "config": config,
            "manifest": json.loads(required[1].read_text(encoding="utf-8")),
            "metrics": json.loads(required[2].read_text(encoding="utf-8")),
        }
    missing = {"base_cost", "zero_cost", "double_cost"} - set(runs)
    if missing:
        raise ValueError(f"缺少 canonical 成本场景：{sorted(missing)}")
    identities = {
        (
            run["config"]["warmupStart"],
            run["config"]["startDate"],
            run["config"]["endDate"],
            run["manifest"]["dataSnapshot"]["snapshotId"],
            run["manifest"]["codeCommit"],
        )
        for run in runs.values()
    }
    if len(identities) != 1:
        raise ValueError("三个成本场景未使用同一日期、快照和代码提交")
    return runs


def build_summary(
    runs: dict[str, dict[str, Any]],
    reproduction_audit: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, str]]:
    base = runs["base_cost"]
    config = base["config"]
    start = pd.Timestamp(config["startDate"])
    end = pd.Timestamp(config["endDate"])
    navs = {
        label: _load_nav(run["path"], start, end)
        for label, run in runs.items()
    }
    passive = _build_passive_nav(base["path"], start, end)
    target_exposure = float(navs["base_cost"]["gross_exposure"].mean())
    static, static_initial_weight = _build_exposure_matched_static(
        passive, target_exposure
    )
    comparison_navs = {**navs, "static": static}
    returns = _aligned_returns(comparison_navs, passive)
    performance = {
        label: summarize_performance(
            frame[["trade_date", "nav"]],
            include_extended=True,
            initial_strategy_nav=1.0,
        )
        for label, frame in comparison_navs.items()
    }
    performance["passive"] = summarize_performance(
        passive[["trade_date", "nav"]],
        include_extended=True,
        initial_strategy_nav=1.0,
    )
    _assert_canonical_metrics(runs, performance)

    targets = _read_frame(
        base["path"] / "targets.csv.gz",
        dates=("signal_date", "available_date"),
        numeric=("target_weight",),
    )
    executions = _read_frame(
        base["path"] / "rebalance_executions.csv.gz",
        dates=("signal_date", "execution_date"),
        numeric=(
            "requested_change",
            "executed_change",
            "blocked_change",
            "transaction_cost_rate",
        ),
    )
    comparison = [
        _comparison_row(
            label,
            performance[label],
            returns[label if label != "passive" else "passive"],
            returns["passive"],
            passive_metrics=performance["passive"],
            nav=(passive if label == "passive" else comparison_navs[label]),
        )
        for label in ("base_cost", "passive", "static", "zero_cost", "double_cost")
    ]
    comparison_by_label = {row["labelKey"]: row for row in comparison}
    yearly = _year_rows(returns, navs["base_cost"], executions, targets, start, end)
    regimes, variance_threshold = _regime_rows(
        base["path"], returns, navs["base_cost"], executions, targets
    )
    stress = _stress_rows(returns, navs["base_cost"], executions, targets)
    walk_forward = _walk_forward_rows(base, navs["base_cost"], passive)
    hac = hac_alpha(returns["base_cost"], returns["passive"])
    drawdown = _drawdown_info(navs["base_cost"], base["metrics"])

    base_row = comparison_by_label["base_cost"]
    passive_row = comparison_by_label["passive"]
    static_row = comparison_by_label["static"]
    double_row = comparison_by_label["double_cost"]
    zero_row = comparison_by_label["zero_cost"]
    total_active_log = math.log1p(base_row["totalReturn"]) - math.log1p(
        passive_row["totalReturn"]
    )
    largest_year = max(yearly, key=lambda row: row["activeLogWealth"])
    complete_years = sum(row["countsForCoverageGate"] for row in yearly)
    period_years = (end - start).days / 365.2425
    gates = _build_gates(
        base_row,
        passive_row,
        static_row,
        double_row,
        period_years,
        len(targets),
        complete_years,
        len(regimes),
        largest_year,
        total_active_log,
    )
    status = "有条件候选" if all(row["passed"] for row in gates) else "不通过"
    risk_on = int(targets["target_weight"].eq(1).sum())
    risk_off = int(targets["target_weight"].eq(0).sum())
    walk_wins = sum(row["strategyReturn"] > row["passiveReturn"] for row in walk_forward)
    metric_years = len(returns) / 252
    target_final_capital = INITIAL_CAPITAL * (1.5**metric_years)

    run_identities = []
    for label in ("base_cost", "zero_cost", "double_cost"):
        manifest = runs[label]["manifest"]
        run_identities.append(
            {
                "scenario": SCENARIO_NAMES[label],
                "runId": manifest["runId"],
                "reproducibilityKey": manifest["reproducibilityKey"],
                "resultFingerprint": manifest["resultFingerprint"],
            }
        )

    summary = {
        "schemaVersion": 1,
        "reportId": "etf-trend-120d-long-history-20260713",
        "title": "沪深300 ETF 120日均线趋势跟踪长历史验证",
        "status": status,
        "researchDate": "2026-07-13",
        "reportGeneratedAt": canonical_report_timestamp(runs),
        "reproductionAudit": reproduction_audit,
        "initialCapital": INITIAL_CAPITAL,
        "period": {
            "rawDataStart": config["warmupStart"],
            "formalStart": config["startDate"],
            "formalEnd": config["endDate"],
            "calendarYears": period_years,
            "openDays": int(len(navs["base_cost"])),
            "returnObservations": int(len(returns)),
            "warmupOpenDays": 120,
            "yearRowsAreSubperiods": True,
        },
        "strategyProfile": {
            "strategyId": "etf_trend_120d",
            "strategyVersion": "1",
            "asset": "510300.SH 华泰柏瑞沪深300 ETF",
            "benchmark": "同一 ETF 因果复权被动持有",
            "rule": "完整月末复权收盘价严格高于最近120个开市日简单均线，则下一开市日目标仓位100%；否则目标仓位0%。",
            "execution": "月末收盘后形成信号，下一开市日开盘执行；月内不调整。",
            "cashReturn": 0.0,
            "leverage": False,
            "shorting": False,
            "trialCount": 1,
            "researchClassification": "同一资产历史已在其他研究中观察；即使通过，结论上限也只是有条件候选。",
        },
        "comparison": comparison,
        "gates": gates,
        "gateSummary": {
            "passed": sum(row["passed"] for row in gates),
            "failed": sum(not row["passed"] for row in gates),
        },
        "execution": {
            "signalDecisions": int(len(targets)),
            "riskOnDecisions": risk_on,
            "riskOffDecisions": risk_off,
            "riskOnRate": risk_on / len(targets),
            "rebalanceRequests": int(len(executions)),
            "filledRequests": int(executions["status"].eq("filled").sum()),
            "blockedRequests": int(executions["status"].eq("blocked").sum()),
            "firstSignalDate": targets["signal_date"].min().date().isoformat(),
            "firstExecutionDate": executions["execution_date"].min().date().isoformat(),
            "lastExecutionDate": executions["execution_date"].max().date().isoformat(),
            "averageExposure": target_exposure,
            "turnover": float(navs["base_cost"]["one_way_turnover"].sum()),
            "cumulativeCostRate": float(
                navs["base_cost"]["transaction_cost_rate"].sum()
            ),
        },
        "costAttribution": {
            "zeroCostFinalCapital": zero_row["finalCapital"],
            "baseCostFinalCapital": base_row["finalCapital"],
            "doubleCostFinalCapital": double_row["finalCapital"],
            "baseCostWealthDragVsZero": zero_row["finalCapital"]
            - base_row["finalCapital"],
            "doubleCostWealthDragVsBase": base_row["finalCapital"]
            - double_row["finalCapital"],
        },
        "staticBenchmark": {
            "description": "事后风险归因基准：一次性配置 ETF 与现金后不再调仓；求解初始 ETF 权重，使全期按市值计算的平均 ETF 暴露与趋势策略相同。它不是可事前知道的交易规则。",
            "initialEtfWeight": static_initial_weight,
            "averageEtfExposure": target_exposure,
        },
        "yearly": yearly,
        "regimeDefinition": {
            "direction": "000300.SH 月收益 > +2% / < -2% / 其余",
            "volatilityReference": "2013-01..2017-12 月实现方差中位数",
            "varianceThreshold": variance_threshold,
        },
        "regimes": regimes,
        "stressWindows": stress,
        "walkForward": {
            "mode": "anchored 504日训练 / 252日测试 / 252日步长",
            "windowCount": len(walk_forward),
            "returnWinCount": walk_wins,
            "rows": walk_forward,
        },
        "drawdown": drawdown,
        "hacAlpha": hac,
        "overfitting": {
            "trialCount": 1,
            "dsr": "not_applicable：固定单一规则，没有从参数网格挑冠军。",
            "pbo": "not_applicable：固定单一规则，没有候选策略排名。",
        },
        "targetGap": {
            "targetCagr": 0.5,
            "actualCagr": base_row["cagr"],
            "gapPercentagePoints": 0.5 - base_row["cagr"],
            "metricYears": metric_years,
            "targetFinalCapital": target_final_capital,
            "actualFinalCapital": base_row["finalCapital"],
        },
        "evidence": {
            "supports": [
                "规则在2018年和2022年熊市阶段减少了同期损失，证明它偶尔能提供方向保护。",
                "正式区间约13.6年、163次月末决策，三个成本场景均使用同一冻结快照并通过断网复现。",
                "年化波动和日度ES95低于100%被动持有，但这没有转化为更好的长期财富或最大回撤。",
            ],
            "against": [
                "基础成本CAGR约0.05%，远低于50%目标和被动持有约8.32%。",
                "最大回撤约-52.82%，比被动持有约-45.45%以及同平均暴露静态组合都差。",
                "零成本CAGR也只有约0.50%，说明主要问题是信号错过上涨和反复切换，不只是费用。",
            ],
            "missing": [
                "现金收益固定为0，未加入时点正确的货币基金或无风险利率。",
                "没有订单金额、ADV参与率和冲击模型，因此不能给出容量或可部署性结论。",
                "没有在全新资产或未来未见时期做独立确认；同一ETF历史已被其他研究观察。",
            ],
        },
        "nextStep": {
            "stop": "停止在同一ETF历史上搜索60/120/200日窗口，也不使用杠杆挽救负的主动价值。",
            "candidate": "若继续趋势方向，下一项应另行预登记A股横截面相对强度：用point-in-time股票宇宙选择强趋势标的，而不是只在一只宽基ETF上开关仓位。",
            "boundary": "横截面趋势需要历史成员、退市、复权、停牌、涨跌停、开盘成交、成本和容量全部闭合；50%年化仍只能是验收目标，不能成为承诺。",
        },
        "quality": {
            "qualityRunId": base["manifest"]["qualityRun"]["qualityRunId"],
            "status": base["manifest"]["qualityRun"]["status"],
            "passedRules": base["manifest"]["qualityRun"]["summary"]["passedCount"],
            "failedRules": base["manifest"]["qualityRun"]["summary"]["failedCount"],
            "snapshotId": base["manifest"]["dataSnapshot"]["snapshotId"],
            "rowCounts": base["manifest"]["dataSnapshot"]["rowCounts"],
            "codeCommit": base["manifest"]["codeCommit"],
            "schemaRevision": base["manifest"]["environment"]["schemaRevision"],
        },
        "runIdentities": run_identities,
        "reproduction": {
            "baseRepeatedMatches": reproduction_audit["matchesPerRun"],
            "zeroCostMatches": reproduction_audit["matchesPerRun"],
            "doubleCostMatches": reproduction_audit["matchesPerRun"],
            "networkDisabled": reproduction_audit["networkDisabled"],
            "allMatched": reproduction_audit["allMatched"],
        },
    }

    capital_series = {
        "120日趋势": navs["base_cost"].set_index("trade_date")["nav"]
        * INITIAL_CAPITAL,
        "ETF被动": passive.set_index("trade_date")["nav"] * INITIAL_CAPITAL,
        "同暴露静态": static.set_index("trade_date")["nav"] * INITIAL_CAPITAL,
    }
    drawdown_series = {
        label: values / values.cummax() - 1 for label, values in capital_series.items()
    }
    indexed_nav = navs["base_cost"].set_index("trade_date")
    charts = {
        "capital": _line_svg(
            capital_series,
            {"120日趋势": "#d9f99d", "ETF被动": "#62d6ff", "同暴露静态": "#ffb454"},
        ),
        "drawdown": _line_svg(
            drawdown_series,
            {"120日趋势": "#ff756d", "ETF被动": "#62d6ff", "同暴露静态": "#ffb454"},
        ),
        "turnover": _line_svg(
            {"累计单边换手": indexed_nav["one_way_turnover"].cumsum()},
            {"累计单边换手": "#d9f99d"},
        ),
        "cost": _line_svg(
            {"累计成本率": indexed_nav["transaction_cost_rate"].cumsum()},
            {"累计成本率": "#ffb454"},
        ),
        "exposure": _line_svg(
            {"ETF仓位": indexed_nav["gross_exposure"]},
            {"ETF仓位": "#62d6ff"},
        ),
    }
    return summary, charts


def _load_nav(path: Path, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    nav = _read_frame(
        path / "nav.csv.gz",
        dates=("trade_date", "executed_signal_date"),
        numeric=(
            "nav",
            "cash_weight",
            "gross_exposure",
            "one_way_turnover",
            "transaction_cost_rate",
        ),
    )
    return nav[nav["trade_date"].between(start, end)].copy()


def _build_exposure_matched_static(
    passive: pd.DataFrame, target_exposure: float
) -> tuple[pd.DataFrame, float]:
    passive_nav = passive["nav"].astype(float)
    low, high = 0.0, 1.0
    for _ in range(80):
        weight = (low + high) / 2
        static_nav = 1 - weight + weight * passive_nav
        average_exposure = float((weight * passive_nav / static_nav).mean())
        if average_exposure < target_exposure:
            low = weight
        else:
            high = weight
    weight = (low + high) / 2
    result = passive[["trade_date"]].copy()
    result["nav"] = 1 - weight + weight * passive_nav
    result["gross_exposure"] = weight * passive_nav / result["nav"]
    result["cash_weight"] = 1 - result["gross_exposure"]
    result["one_way_turnover"] = 0.0
    result["transaction_cost_rate"] = 0.0
    return result, weight


def _assert_canonical_metrics(
    runs: dict[str, dict[str, Any]], performance: dict[str, dict[str, Any]]
) -> None:
    keys = (
        "totalReturn",
        "annualizedReturn",
        "annualizedVolatility",
        "sharpe",
        "sortino",
        "maxDrawdown",
        "maxDrawdownDuration",
        "calmar",
    )
    for label, run in runs.items():
        for key in keys:
            if not math.isclose(
                float(run["metrics"][key]),
                float(performance[label][key]),
                rel_tol=0,
                abs_tol=1e-12,
            ):
                raise ValueError(f"{label} 报告指标未与 canonical 工件闭合：{key}")


def _comparison_row(
    label: str,
    metrics: dict[str, Any],
    returns: pd.Series,
    passive_returns: pd.Series,
    *,
    passive_metrics: dict[str, Any],
    nav: pd.DataFrame,
) -> dict[str, Any]:
    tail = tail_metrics(returns)
    active = returns - passive_returns
    active_volatility = float(active.std(ddof=1))
    benchmark_variance = float(passive_returns.var(ddof=1))
    beta = (
        1.0
        if label == "passive"
        else float(returns.cov(passive_returns) / benchmark_variance)
    )
    tracking_error = float(active_volatility * math.sqrt(252))
    information_ratio = (
        None
        if active_volatility == 0
        else float(active.mean() / active_volatility * math.sqrt(252))
    )
    total_return = float(metrics["totalReturn"])
    cost = (
        float(nav["transaction_cost_rate"].sum())
        if "transaction_cost_rate" in nav
        else 0.0
    )
    turnover = (
        float(nav["one_way_turnover"].sum()) if "one_way_turnover" in nav else 0.0
    )
    exposure = (
        float(nav["gross_exposure"].mean())
        if "gross_exposure" in nav
        else 1.0
    )
    return {
        "labelKey": label,
        "label": SCENARIO_NAMES[label],
        "initialCapital": INITIAL_CAPITAL,
        "finalCapital": INITIAL_CAPITAL * (1 + total_return),
        "profitAndLoss": INITIAL_CAPITAL * total_return,
        "totalReturn": total_return,
        "relativeWealth": (1 + total_return) / (1 + passive_metrics["totalReturn"])
        - 1,
        "cagr": float(metrics["annualizedReturn"]),
        "volatility": float(metrics["annualizedVolatility"]),
        "sharpe": float(metrics["sharpe"]),
        "downsideVolatility": float(metrics["downsideVolatility"]),
        "sortino": float(metrics["sortino"]),
        "maxDrawdown": float(metrics["maxDrawdown"]),
        "maxDrawdownDuration": int(metrics["maxDrawdownDuration"]),
        "calmar": float(metrics["calmar"]),
        "var95": tail["var95"],
        "es95": tail["es95"],
        "skew": tail["skew"],
        "excessKurtosis": tail["excessKurtosis"],
        "beta": beta,
        "trackingError": tracking_error,
        "informationRatio": information_ratio,
        "informationRatioDisplay": (
            "not_applicable" if information_ratio is None else f"{information_ratio:.3f}"
        ),
        "averageExposure": exposure,
        "turnover": turnover,
        "cost": cost,
    }


def _period_metrics(group: pd.DataFrame) -> dict[str, Any]:
    if group.empty:
        return {
            "strategyReturn": None,
            "passiveReturn": None,
            "staticReturn": None,
            "activeReturn": None,
            "annualizedVolatility": None,
            "strategyMaxDrawdown": None,
        }
    period = summarize_return_subperiod(group["base_cost"], group["passive"])
    strategy_return = float(period["totalReturn"])
    passive_return = float(period["benchmarkTotalReturn"])
    return {
        "strategyReturn": strategy_return,
        "passiveReturn": passive_return,
        "staticReturn": float((1 + group["static"]).prod() - 1),
        "activeReturn": strategy_return - passive_return,
        "annualizedVolatility": period["annualizedVolatility"],
        "strategyMaxDrawdown": period["maxDrawdown"],
    }


def _ledger_metrics(
    dates: pd.Series,
    nav: pd.DataFrame,
    executions: pd.DataFrame,
    targets: pd.DataFrame,
) -> dict[str, Any]:
    selected_dates = set(pd.to_datetime(dates))
    selected_months = set(pd.to_datetime(dates).dt.to_period("M"))
    nav_slice = nav[nav["trade_date"].isin(selected_dates)]
    execution_slice = executions[executions["execution_date"].isin(selected_dates)]
    target_slice = targets[
        targets["signal_date"].dt.to_period("M").isin(selected_months)
    ]
    return {
        "decisionCount": int(len(target_slice)),
        "requestCount": int(len(execution_slice)),
        "turnover": float(nav_slice["one_way_turnover"].sum()),
        "cost": float(nav_slice["transaction_cost_rate"].sum()),
        "blockedRate": (
            float(execution_slice["status"].eq("blocked").mean())
            if not execution_slice.empty
            else None
        ),
        "averageExposure": float(nav_slice["gross_exposure"].mean()),
    }


def _year_rows(
    returns: pd.DataFrame,
    nav: pd.DataFrame,
    executions: pd.DataFrame,
    targets: pd.DataFrame,
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> list[dict[str, Any]]:
    rows = []
    for year, group in returns.groupby(returns["trade_date"].dt.year, sort=True):
        boundary_year = year in (start.year, end.year)
        if boundary_year:
            coverage = "部分年度（总周期边界）"
        elif len(group) >= 240:
            coverage = "完整年度（计入门禁）"
        else:
            coverage = "完整自然年但不足240个共同交易日"
        metrics = _period_metrics(group)
        rows.append(
            {
                "year": int(year),
                "coverage": coverage,
                "countsForCoverageGate": bool(not boundary_year and len(group) >= 240),
                "observations": int(len(group)),
                **metrics,
                **_ledger_metrics(group["trade_date"], nav, executions, targets),
                "activeLogWealth": math.log1p(metrics["strategyReturn"])
                - math.log1p(metrics["passiveReturn"]),
            }
        )
    return rows


def _regime_rows(
    run_path: Path,
    returns: pd.DataFrame,
    nav: pd.DataFrame,
    executions: pd.DataFrame,
    targets: pd.DataFrame,
) -> tuple[list[dict[str, Any]], float]:
    benchmark = _read_frame(
        run_path / "inputs" / "index_daily_bars.csv.gz",
        dates=("trade_date",),
        numeric=("close",),
    ).sort_values("trade_date")
    benchmark["daily_return"] = benchmark["close"].pct_change(fill_method=None)
    benchmark["month"] = benchmark["trade_date"].dt.to_period("M")
    monthly_rows = []
    previous_close: float | None = None
    for month, group in benchmark.groupby("month", sort=True):
        daily = group["daily_return"].dropna()
        last_close = float(group["close"].iloc[-1])
        monthly_rows.append(
            {
                "month": month,
                "marketReturn": (
                    None if previous_close is None else last_close / previous_close - 1
                ),
                "realizedVariance": float(((daily - daily.mean()) ** 2).sum()),
            }
        )
        previous_close = last_close
    months = pd.DataFrame(monthly_rows)
    reference = months[months["month"].between("2013-01", "2017-12")]
    threshold = float(reference["realizedVariance"].median())
    months["direction"] = months["marketReturn"].map(
        lambda value: (
            "上涨"
            if pd.notna(value) and value > 0.02
            else "下跌"
            if pd.notna(value) and value < -0.02
            else "震荡"
        )
    )
    months["volatility"] = months["realizedVariance"].map(
        lambda value: "高波动" if value > threshold else "低波动"
    )
    joined = returns.copy()
    joined["month"] = joined["trade_date"].dt.to_period("M")
    joined = joined.merge(
        months[["month", "direction", "volatility"]],
        on="month",
        how="left",
        validate="many_to_one",
    )
    rows = []
    for (direction, volatility), group in joined.groupby(
        ["direction", "volatility"], sort=False
    ):
        rows.append(
            {
                "direction": direction,
                "volatility": volatility,
                "months": int(group["month"].nunique()),
                "observations": int(len(group)),
                **_period_metrics(group),
                **_ledger_metrics(group["trade_date"], nav, executions, targets),
            }
        )
    order = {"上涨": 0, "震荡": 1, "下跌": 2, "低波动": 0, "高波动": 1}
    rows.sort(key=lambda row: (order[row["direction"]], order[row["volatility"]]))
    return rows, threshold


def _stress_rows(
    returns: pd.DataFrame,
    nav: pd.DataFrame,
    executions: pd.DataFrame,
    targets: pd.DataFrame,
) -> list[dict[str, Any]]:
    rows = []
    for name, start, end in STRESS_WINDOWS:
        group = returns[returns["trade_date"].between(start, end)]
        rows.append(
            {
                "name": name,
                "startDate": start,
                "endDate": end,
                "observations": int(len(group)),
                **_period_metrics(group),
                **_ledger_metrics(group["trade_date"], nav, executions, targets),
            }
        )
    return rows


def _walk_forward_rows(
    run: dict[str, Any], strategy_nav: pd.DataFrame, passive_nav: pd.DataFrame
) -> list[dict[str, Any]]:
    windows = _read_frame(
        run["path"] / "walk_forward_windows.csv.gz",
        dates=("train_start", "train_end", "test_start", "test_end"),
        numeric=("train_periods", "test_periods"),
    )
    persisted = _read_frame(
        run["path"] / "walk_forward_metrics.csv.gz",
        dates=("start_date", "end_date"),
        numeric=("total_return", "sharpe", "max_drawdown"),
    ).set_index("window_id")
    rows = []
    for window in windows.itertuples(index=False):
        strategy_metrics = summarize_nav_window(
            strategy_nav,
            start=window.test_start,
            end=window.test_end,
            benchmark_nav=passive_nav,
            include_extended=True,
        )
        passive_metrics = summarize_nav_window(
            passive_nav,
            start=window.test_start,
            end=window.test_end,
            include_extended=True,
        )
        canonical = persisted.loc[window.window_id]
        if not math.isclose(
            strategy_metrics["totalReturn"],
            float(canonical["total_return"]),
            rel_tol=0,
            abs_tol=1e-12,
        ):
            raise ValueError(f"walk-forward 未与 canonical 工件闭合：{window.window_id}")
        if not math.isclose(
            strategy_metrics["benchmarkTotalReturn"],
            float(canonical["benchmark_total_return"]),
            rel_tol=0,
            abs_tol=1e-12,
        ):
            raise ValueError(f"walk-forward 基准未与 canonical 工件闭合：{window.window_id}")
        rows.append(
            {
                "window": window.window_id,
                "trainRange": f"{window.train_start.date()}..{window.train_end.date()}",
                "testRange": f"{window.test_start.date()}..{window.test_end.date()}",
                "observations": int(strategy_metrics["observations"]),
                "strategyReturn": float(strategy_metrics["totalReturn"]),
                "passiveReturn": float(passive_metrics["totalReturn"]),
                "strategySharpe": float(strategy_metrics["sharpe"]),
                "passiveSharpe": float(passive_metrics["sharpe"]),
                "strategyMaxDrawdown": float(strategy_metrics["maxDrawdown"]),
                "passiveMaxDrawdown": float(passive_metrics["maxDrawdown"]),
            }
        )
    return rows


def _drawdown_info(nav: pd.DataFrame, canonical_metrics: dict[str, Any]) -> dict[str, Any]:
    frame = nav[["trade_date", "nav"]].copy()
    frame["peak"] = frame["nav"].cummax()
    frame["drawdown"] = frame["nav"] / frame["peak"] - 1
    trough = frame.loc[frame["drawdown"].idxmin()]
    history = frame[frame["trade_date"].le(trough["trade_date"])]
    peak = history.loc[history["nav"].idxmax()]
    recovered = frame[
        frame["trade_date"].gt(trough["trade_date"])
        & frame["nav"].ge(peak["nav"])
    ]
    recovery = None if recovered.empty else recovered.iloc[0]["trade_date"].date().isoformat()
    return {
        "peakDate": peak["trade_date"].date().isoformat(),
        "peakCapital": float(peak["nav"] * INITIAL_CAPITAL),
        "troughDate": trough["trade_date"].date().isoformat(),
        "troughCapital": float(trough["nav"] * INITIAL_CAPITAL),
        "lossAmount": float((trough["nav"] - peak["nav"]) * INITIAL_CAPITAL),
        "maxDrawdown": float(trough["drawdown"]),
        "recoveryDate": recovery,
        "recoveredBySampleEnd": recovery is not None,
        "longestDurationOpenDays": int(canonical_metrics["maxDrawdownDuration"]),
    }


def _build_gates(
    base: dict[str, Any],
    passive: dict[str, Any],
    static: dict[str, Any],
    double: dict[str, Any],
    years: float,
    decisions: int,
    complete_years: int,
    regime_cells: int,
    largest_year: dict[str, Any],
    total_active_log: float,
) -> list[dict[str, Any]]:
    rows = []

    def add(name: str, rule: str, actual: str, passed: bool) -> None:
        rows.append({"name": name, "rule": rule, "actual": actual, "passed": passed})

    add("50%年化目标", "基础成本净CAGR ≥ 50%", _fmt(base["cagr"], "pct"), base["cagr"] >= 0.5)
    add(
        "相对收益",
        "基础成本净CAGR ≥ ETF被动持有",
        f"{base['cagr']:.2%} vs {passive['cagr']:.2%}",
        base["cagr"] >= passive["cagr"],
    )
    drawdown_ratio = abs(base["maxDrawdown"]) / abs(passive["maxDrawdown"])
    add(
        "最大回撤",
        "|策略回撤| / |被动回撤| ≤ 75%",
        f"{drawdown_ratio:.2%}",
        drawdown_ratio <= 0.75,
    )
    sharpe_delta = base["sharpe"] - passive["sharpe"]
    add("Sharpe改善", "策略 - 被动 ≥ 0.10", f"{sharpe_delta:.3f}", sharpe_delta >= 0.1)
    calmar_target = passive["calmar"] * 1.25
    add(
        "Calmar改善",
        "策略Calmar ≥ 被动的1.25倍",
        f"{base['calmar']:.3f} vs {calmar_target:.3f}",
        base["calmar"] >= calmar_target,
    )
    static_sharpe_delta = base["sharpe"] - static["sharpe"]
    add(
        "超过机械降仓",
        "Sharpe高于同暴露静态组合0.05，且回撤不差",
        f"Sharpe差 {static_sharpe_delta:.3f}；回撤 {base['maxDrawdown']:.2%} vs {static['maxDrawdown']:.2%}",
        static_sharpe_delta >= 0.05
        and abs(base["maxDrawdown"]) <= abs(static["maxDrawdown"]),
    )
    double_sharpe_delta = double["sharpe"] - passive["sharpe"]
    add(
        "双倍成本压力",
        "累计收益为正，且Sharpe不低于被动",
        f"累计 {double['totalReturn']:.2%}；Sharpe差 {double_sharpe_delta:.3f}",
        double["totalReturn"] > 0 and double_sharpe_delta >= 0,
    )
    add(
        "长期覆盖",
        "≥10年、≥120次决策、≥8个完整年、6个环境格",
        f"{years:.1f}年 / {decisions}次 / {complete_years}年 / {regime_cells}格",
        years >= 10 and decisions >= 120 and complete_years >= 8 and regime_cells == 6,
    )
    add(
        "单年不主导",
        "最大单年主动对数财富贡献 ≤ 整段净主动对数财富",
        f"{largest_year['year']}年 {largest_year['activeLogWealth']:.3f}；整段 {total_active_log:.3f}",
        largest_year["activeLogWealth"] <= total_active_log,
    )
    return rows


def render_html(summary: dict[str, Any], charts: dict[str, str]) -> str:
    comparison = {row["labelKey"]: row for row in summary["comparison"]}
    base = comparison["base_cost"]
    passive = comparison["passive"]
    static = comparison["static"]
    zero = comparison["zero_cost"]
    double = comparison["double_cost"]
    period = summary["period"]
    execution = summary["execution"]
    drawdown = summary["drawdown"]

    comparison_table = _html_table(
        summary["comparison"],
        (
            ("label", "方案", "text"),
            ("initialCapital", "初始本金", "money"),
            ("finalCapital", "期末资产", "money"),
            ("profitAndLoss", "累计盈亏", "money"),
            ("totalReturn", "累计收益", "pct"),
            ("cagr", "CAGR", "pct"),
            ("volatility", "年化波动", "pct"),
            ("sharpe", "Sharpe", "num"),
            ("sortino", "Sortino", "num"),
            ("maxDrawdown", "最大回撤", "pct"),
            ("maxDrawdownDuration", "最长回撤开市日", "int"),
            ("calmar", "Calmar", "num"),
            ("es95", "日度ES95", "pct"),
            ("beta", "Beta", "num"),
            ("trackingError", "跟踪误差", "pct"),
            ("informationRatioDisplay", "信息比率", "text"),
            ("averageExposure", "平均ETF暴露", "pct"),
            ("turnover", "累计单边换手", "pct"),
            ("cost", "累计成本率", "pct"),
        ),
    )
    gates = _html_table(
        summary["gates"],
        (
            ("name", "事前门禁", "text"),
            ("rule", "要求", "text"),
            ("actual", "实际", "text"),
            ("passed", "结果", "bool"),
        ),
    )
    yearly = _html_table(
        summary["yearly"],
        (
            ("year", "自然年", "int"),
            ("coverage", "年度性质", "text"),
            ("observations", "共同交易日", "int"),
            ("decisionCount", "月末决策", "int"),
            ("strategyReturn", "策略净收益", "pct"),
            ("passiveReturn", "ETF被动", "pct"),
            ("activeReturn", "主动差", "pct"),
            ("strategyMaxDrawdown", "策略回撤", "pct"),
            ("averageExposure", "平均暴露", "pct"),
            ("turnover", "单边换手", "pct"),
            ("cost", "成本率", "pct"),
        ),
    )
    regimes = _html_table(
        summary["regimes"],
        (
            ("direction", "行情方向", "text"),
            ("volatility", "波动环境", "text"),
            ("months", "月份", "int"),
            ("observations", "交易日", "int"),
            ("decisionCount", "月末决策", "int"),
            ("strategyReturn", "策略净收益", "pct"),
            ("passiveReturn", "ETF被动", "pct"),
            ("activeReturn", "主动差", "pct"),
            ("strategyMaxDrawdown", "策略条件回撤", "pct"),
            ("averageExposure", "平均暴露", "pct"),
            ("turnover", "单边换手", "pct"),
            ("cost", "成本率", "pct"),
        ),
    )
    stress = _html_table(
        summary["stressWindows"],
        (
            ("name", "事前压力阶段", "text"),
            ("startDate", "开始", "text"),
            ("endDate", "结束", "text"),
            ("observations", "交易日", "int"),
            ("decisionCount", "月末决策", "int"),
            ("strategyReturn", "策略净收益", "pct"),
            ("passiveReturn", "ETF被动", "pct"),
            ("activeReturn", "主动差", "pct"),
            ("strategyMaxDrawdown", "策略回撤", "pct"),
            ("averageExposure", "平均暴露", "pct"),
        ),
    )
    walk_forward = _html_table(
        summary["walkForward"]["rows"],
        (
            ("trainRange", "训练区间", "text"),
            ("testRange", "测试区间", "text"),
            ("observations", "测试交易日", "int"),
            ("strategyReturn", "策略净收益", "pct"),
            ("passiveReturn", "ETF被动", "pct"),
            ("strategySharpe", "策略Sharpe", "num"),
            ("passiveSharpe", "被动Sharpe", "num"),
            ("strategyMaxDrawdown", "策略回撤", "pct"),
            ("passiveMaxDrawdown", "被动回撤", "pct"),
        ),
    )
    identities = _html_table(
        summary["runIdentities"],
        (
            ("scenario", "成本场景", "text"),
            ("runId", "运行ID", "code"),
            ("reproducibilityKey", "可复现键", "code"),
            ("resultFingerprint", "结果指纹", "code"),
        ),
    )

    supports = "".join(f"<li>{escape(item)}</li>" for item in summary["evidence"]["supports"])
    against = "".join(f"<li>{escape(item)}</li>" for item in summary["evidence"]["against"])
    missing = "".join(f"<li>{escape(item)}</li>" for item in summary["evidence"]["missing"])
    recovery = drawdown["recoveryDate"] or "截至样本末仍未恢复"
    failed_gates = summary["gateSummary"]["failed"]
    target_final = summary["targetGap"]["targetFinalCapital"]
    regime_threshold = summary["regimeDefinition"]["varianceThreshold"]

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{escape(summary['title'])}</title>
<style>
:root{{--ink:#e7eee9;--muted:#91a39a;--panel:#17201b;--panel2:#111914;--line:#34473d;--lime:#d9f99d;--cyan:#62d6ff;--amber:#ffb454;--red:#ff756d;--bg:#0c120f}}
*{{box-sizing:border-box}}html{{scroll-behavior:smooth}}body{{margin:0;color:var(--ink);background-color:var(--bg);background-image:linear-gradient(rgba(98,214,255,.035) 1px,transparent 1px),linear-gradient(90deg,rgba(98,214,255,.035) 1px,transparent 1px);background-size:28px 28px;font-family:"Avenir Next","PingFang SC","Noto Sans CJK SC",sans-serif;line-height:1.58}}
body:before{{content:"";position:fixed;inset:0;pointer-events:none;background:repeating-linear-gradient(0deg,rgba(255,255,255,.012),rgba(255,255,255,.012) 1px,transparent 1px,transparent 4px)}}
.shell{{width:min(1480px,calc(100% - 36px));margin:0 auto;padding:34px 0 80px}}.mono,code,.eyebrow,.metric span,.tag,th{{font-family:"IBM Plex Mono","SFMono-Regular",Consolas,monospace}}
.hero{{position:relative;overflow:hidden;border:1px solid var(--line);background:linear-gradient(145deg,rgba(23,32,27,.98),rgba(11,18,14,.98));padding:34px;box-shadow:0 24px 80px rgba(0,0,0,.34)}}.hero:after{{content:"";position:absolute;right:-80px;top:-140px;width:340px;height:340px;border:42px solid rgba(217,249,157,.065);transform:rotate(18deg)}}
.eyebrow{{color:var(--lime);font-size:12px;letter-spacing:.2em;text-transform:uppercase}}h1{{max-width:880px;margin:16px 0 8px;font-family:"DIN Condensed","Avenir Next Condensed","PingFang SC",sans-serif;font-size:clamp(38px,6vw,82px);line-height:.98;letter-spacing:-.04em}}.lead{{max-width:1000px;margin:20px 0 0;color:#c4d0ca;font-size:18px}}
.status{{display:inline-flex;align-items:center;gap:10px;margin-top:22px;padding:8px 12px;border:1px solid rgba(255,117,109,.6);background:rgba(255,117,109,.1);color:#ffd0cc;font-weight:800}}.status:before{{content:"";width:9px;height:9px;background:var(--red);box-shadow:0 0 18px var(--red)}}
.timeline{{display:grid;grid-template-columns:1fr 2fr 1fr;margin-top:30px;border-top:1px solid var(--line);border-bottom:1px solid var(--line)}}.time{{padding:16px 18px;border-right:1px solid var(--line)}}.time:last-child{{border:0}}.time b{{display:block;color:var(--lime);font-size:15px}}.time small{{color:var(--muted)}}
.metric-grid{{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:1px;margin:22px 0;background:var(--line);border:1px solid var(--line)}}.metric{{min-height:112px;padding:18px;background:var(--panel)}}.metric span{{display:block;color:var(--muted);font-size:11px;letter-spacing:.08em;text-transform:uppercase}}.metric b{{display:block;margin-top:10px;font-size:clamp(24px,3vw,42px);line-height:1;color:var(--ink)}}.metric.bad b{{color:var(--red)}}.metric.good b{{color:var(--lime)}}
.section{{margin-top:22px;padding:26px;border:1px solid var(--line);background:rgba(23,32,27,.96)}}h2{{margin:0 0 8px;font-family:"DIN Condensed","Avenir Next Condensed","PingFang SC",sans-serif;font-size:32px;letter-spacing:-.02em}}h3{{margin:0 0 10px;font-size:18px}}.note{{margin:0 0 18px;color:var(--muted)}}.callout{{border-left:5px solid var(--red);background:rgba(255,117,109,.08);padding:16px 18px;margin:18px 0}}.callout strong{{color:#ffd0cc}}
.rule-grid,.evidence-grid,.chart-grid{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px;margin-top:18px}}.rule,.evidence,.chart{{border:1px solid var(--line);background:var(--panel2);padding:18px}}.rule .tag{{display:inline-block;color:var(--lime);font-size:11px;letter-spacing:.1em}}.rule b{{display:block;margin:10px 0 6px;font-size:19px}}.evidence.support{{border-top:3px solid var(--lime)}}.evidence.against{{border-top:3px solid var(--red)}}.evidence.missing{{border-top:3px solid var(--amber)}}ul{{padding-left:20px;margin:10px 0}}li+li{{margin-top:8px}}
.chart-grid{{grid-template-columns:2fr 1fr}}.chart.wide{{grid-column:1/-1}}.legend{{display:flex;flex-wrap:wrap;gap:16px;color:var(--muted);font-size:13px;margin-bottom:8px}}.dot{{display:inline-block;width:9px;height:9px;margin-right:6px}}svg{{width:100%;height:auto;background:#0d1511;border:1px solid #25372e}}
.table-wrap{{overflow:auto;border:1px solid var(--line)}}table{{width:100%;border-collapse:collapse;min-width:920px;background:var(--panel2)}}th{{position:sticky;top:0;z-index:1;padding:11px 12px;text-align:left;color:#aebfb6;background:#1c2922;border-bottom:1px solid var(--line);font-size:11px;letter-spacing:.04em;white-space:nowrap}}td{{padding:10px 12px;border-bottom:1px solid #25352d;vertical-align:top;white-space:nowrap}}tr:hover td{{background:rgba(98,214,255,.04)}}td.ok{{color:var(--lime);font-weight:800}}td.bad{{color:var(--red);font-weight:800}}code{{color:#bcecff;font-size:12px;word-break:break-all;white-space:normal}}
.split{{display:grid;grid-template-columns:1fr 1fr;gap:14px}}.statline{{display:grid;grid-template-columns:1fr auto;gap:12px;padding:10px 0;border-bottom:1px solid var(--line)}}.statline span{{color:var(--muted)}}.statline b{{text-align:right}}.footer{{margin-top:28px;color:var(--muted);font-size:13px;text-align:center}}
@media(max-width:960px){{.metric-grid{{grid-template-columns:repeat(2,1fr)}}.timeline,.rule-grid,.evidence-grid,.chart-grid,.split{{grid-template-columns:1fr}}.chart.wide{{grid-column:auto}}.time{{border-right:0;border-bottom:1px solid var(--line)}}.hero,.section{{padding:20px}}}}
@media print{{body{{background:#fff;color:#111}}.hero,.section,.metric,.rule,.evidence,.chart,table{{background:#fff;color:#111;box-shadow:none}}.shell{{width:100%;padding:0}}}}
</style>
</head>
<body><main class="shell">
<header class="hero">
  <div class="eyebrow">RESEARCH AUDIT / FIXED RULE / LONG HISTORY</div>
  <h1>沪深300 ETF<br>120日均线趋势跟踪</h1>
  <div class="status">强制结论：{escape(summary['status'])}</div>
  <p class="lead">这不是一年回测。完整正式回测从 <b>{period['formalStart']}</b> 一直覆盖到 <b>{period['formalEnd']}</b>，约 <b>{period['calendarYears']:.1f} 年</b>、{period['openDays']} 个开市日。自然年表只是把这条完整历史切成子区间检查。</p>
  <div class="timeline">
    <div class="time"><small>原始数据与热身</small><b>{period['rawDataStart']} 起</b><small>先积累固定120个开市日</small></div>
    <div class="time"><small>完整正式回测周期</small><b>{period['formalStart']} → {period['formalEnd']}</b><small>所有总体收益、风险和门禁均使用这整段</small></div>
    <div class="time"><small>年度与压力表</small><b>仅为子区间诊断</b><small>绝不代表总回测只有一年</small></div>
  </div>
</header>

<div class="metric-grid">
  <div class="metric bad"><span>基础成本 CAGR</span><b>{base['cagr']:.2%}</b></div>
  <div class="metric bad"><span>¥100,000 期末资产</span><b>{_fmt(base['finalCapital'],'money')}</b></div>
  <div class="metric bad"><span>策略最大回撤</span><b>{base['maxDrawdown']:.2%}</b></div>
  <div class="metric"><span>ETF被动 CAGR</span><b>{passive['cagr']:.2%}</b></div>
  <div class="metric"><span>50% CAGR目标对应期末</span><b>{_fmt(target_final,'money')}</b></div>
</div>

<section class="section">
  <h2>先看懂策略到底做什么</h2>
  <p class="note">120个开市日大约是半年。它不预测底部，只在月末确认价格是否仍处在半年均线之上。</p>
  <div class="rule-grid">
    <div class="rule"><span class="tag">观察</span><b>只看月末</b><p>用当日及此前119个开市日的因果复权收盘价计算简单均线。</p></div>
    <div class="rule"><span class="tag">判断</span><b>价格高于均线才持有</b><p>严格高于均线：下月100% ETF；否则：下月100%现金。</p></div>
    <div class="rule"><span class="tag">执行</span><b>下一开市日开盘成交</b><p>不使用月末收盘价偷跑；不加杠杆、不做空，现金收益固定为0。</p></div>
  </div>
  <div class="callout"><strong>一句话结论：</strong>这条规则在2018和2022年有保护，但长期错过上涨、遭遇反复切换，基础成本后13.6年只把10万元变成 {_fmt(base['finalCapital'],'money')}；最大回撤还比一直持有ETF更深。</div>
</section>

<section class="section">
  <h2>三种成本场景先解释清楚</h2>
  <div class="rule-grid">
    <div class="rule"><span class="tag">正式口径</span><b>基础成本</b><p>买入0.035%、卖出0.085%、滑点0.10%。所有结论以此为准。</p></div>
    <div class="rule"><span class="tag">归因口径</span><b>零成本</b><p>把全部交易费用与滑点设为0，只检查信号本身是否有价值。</p></div>
    <div class="rule"><span class="tag">压力口径</span><b>双倍成本</b><p>买卖费率与滑点全部翻倍，检查纸面优势是否经得起成本恶化。</p></div>
  </div>
</section>

<section class="section">
  <h2>事前门禁：{failed_gates} 项失败</h2>
  <p class="note">50%净CAGR是用户目标，也是第一条硬门禁。规则、成本、区间和阈值均在查看结果前固定。</p>
  <div class="table-wrap">{gates}</div>
</section>

<section class="section">
  <h2>十万元账户的长期结果</h2>
  <p class="note">同平均暴露静态组合只用于事后风险归因，不是可事前知道的交易规则：它在起点配置一次ETF与现金，之后不调仓；求解出的全期平均ETF暴露与趋势策略同为 {execution['averageExposure']:.2%}。</p>
  <div class="table-wrap">{comparison_table}</div>
  <div class="chart-grid">
    <div class="chart wide"><h3>账户资产曲线</h3><div class="legend"><span><i class="dot" style="background:#d9f99d"></i>120日趋势</span><span><i class="dot" style="background:#62d6ff"></i>ETF被动</span><span><i class="dot" style="background:#ffb454"></i>同暴露静态</span></div>{charts['capital']}</div>
    <div class="chart wide"><h3>回撤曲线</h3><div class="legend"><span><i class="dot" style="background:#ff756d"></i>120日趋势</span><span><i class="dot" style="background:#62d6ff"></i>ETF被动</span><span><i class="dot" style="background:#ffb454"></i>同暴露静态</span></div>{charts['drawdown']}</div>
  </div>
</section>

<section class="section">
  <h2>为什么趋势规则没有赚到钱</h2>
  <div class="evidence-grid">
    <div class="evidence support"><h3>它确实保护过</h3><ul><li>2018：策略 {_fmt(next(row for row in summary['yearly'] if row['year']==2018)['strategyReturn'],'pct')}；ETF被动 {_fmt(next(row for row in summary['yearly'] if row['year']==2018)['passiveReturn'],'pct')}。</li><li>2022预设压力期主动差为 {_fmt(next(row for row in summary['stressWindows'] if row['name']=='2022回撤')['activeReturn'],'pct')}。</li></ul></div>
    <div class="evidence against"><h3>但错过反弹更致命</h3><ul><li>2019：策略 {_fmt(next(row for row in summary['yearly'] if row['year']==2019)['strategyReturn'],'pct')}；ETF被动 {_fmt(next(row for row in summary['yearly'] if row['year']==2019)['passiveReturn'],'pct')}。</li><li>2024：策略 {_fmt(next(row for row in summary['yearly'] if row['year']==2024)['strategyReturn'],'pct')}；ETF被动 {_fmt(next(row for row in summary['yearly'] if row['year']==2024)['passiveReturn'],'pct')}。</li></ul></div>
    <div class="evidence missing"><h3>成本不是主因</h3><ul><li>零成本CAGR也只有 {zero['cagr']:.2%}。</li><li>基础成本累计成本率 {base['cost']:.2%}，财富拖累约 {_fmt(summary['costAttribution']['baseCostWealthDragVsZero'],'money')}。</li><li>双倍成本期末仅 {_fmt(double['finalCapital'],'money')}。</li></ul></div>
  </div>
</section>

<section class="section">
  <h2>最大回撤发生了什么</h2>
  <div class="split">
    <div>
      <div class="statline"><span>前高日期 / 资产</span><b>{drawdown['peakDate']} / {_fmt(drawdown['peakCapital'],'money')}</b></div>
      <div class="statline"><span>谷底日期 / 资产</span><b>{drawdown['troughDate']} / {_fmt(drawdown['troughCapital'],'money')}</b></div>
      <div class="statline"><span>高点到谷底损失</span><b>{_fmt(drawdown['lossAmount'],'money')}</b></div>
      <div class="statline"><span>恢复日期</span><b>{escape(recovery)}</b></div>
    </div>
    <div>
      <div class="statline"><span>策略最大回撤</span><b>{base['maxDrawdown']:.2%}</b></div>
      <div class="statline"><span>ETF被动最大回撤</span><b>{passive['maxDrawdown']:.2%}</b></div>
      <div class="statline"><span>同暴露静态回撤</span><b>{static['maxDrawdown']:.2%}</b></div>
      <div class="statline"><span>最长未创新高</span><b>{drawdown['longestDurationOpenDays']} 个开市日</b></div>
    </div>
  </div>
</section>

<section class="section">
  <h2>逐年稳定性：每一行都只是子区间</h2>
  <p class="note">再次强调：总回测是 {period['formalStart']} 至 {period['formalEnd']}。下表只是按自然年切片；2012和2026明确是边界部分年度，绝不冒充完整总周期。</p>
  <div class="table-wrap">{yearly}</div>
</section>

<section class="section">
  <h2>方向 × 波动率环境</h2>
  <p class="note">方向按沪深300指数月收益±2%划分；波动门槛只取固定参考期2013至2017年月实现方差中位数 {regime_threshold:.8f}。条件回撤把同类月份串联，仅用于环境比较。</p>
  <div class="table-wrap">{regimes}</div>
</section>

<section class="section">
  <h2>事前固定的压力阶段</h2>
  <p class="note">窗口在读取收益指标前写入预登记，未按策略结果挑选。</p>
  <div class="table-wrap">{stress}</div>
</section>

<section class="section">
  <h2>交易、换手与成本路径</h2>
  <div class="metric-grid">
    <div class="metric"><span>月末决策</span><b>{execution['signalDecisions']}</b></div>
    <div class="metric"><span>风险开启 / 关闭</span><b>{execution['riskOnDecisions']} / {execution['riskOffDecisions']}</b></div>
    <div class="metric"><span>成交请求 / 阻塞</span><b>{execution['filledRequests']} / {execution['blockedRequests']}</b></div>
    <div class="metric"><span>累计单边换手</span><b>{execution['turnover']:.1f}×</b></div>
    <div class="metric"><span>累计成本率</span><b>{execution['cumulativeCostRate']:.2%}</b></div>
  </div>
  <div class="chart-grid">
    <div class="chart"><h3>累计单边换手</h3>{charts['turnover']}</div>
    <div class="chart"><h3>累计交易成本率</h3>{charts['cost']}</div>
    <div class="chart wide"><h3>ETF仓位：只在0%和100%之间切换</h3>{charts['exposure']}</div>
  </div>
</section>

<section class="section">
  <h2>Walk-forward测试窗口</h2>
  <p class="note">固定规则没有从训练段估计参数；这里仍按504日训练、252日测试、252日步长生成11个测试窗口。策略累计收益只在 {summary['walkForward']['returnWinCount']} / {summary['walkForward']['windowCount']} 个窗口超过ETF被动。</p>
  <div class="table-wrap">{walk_forward}</div>
</section>

<section class="section">
  <h2>统计、尾部与过拟合边界</h2>
  <div class="split">
    <div>
      <div class="statline"><span>HAC年化Alpha</span><b>{summary['hacAlpha']['annualizedAlpha']:.2%}</b></div>
      <div class="statline"><span>Alpha t值</span><b>{summary['hacAlpha']['alphaTStatistic']:.2f}</b></div>
      <div class="statline"><span>95%区间</span><b>{summary['hacAlpha']['ci95Low']:.2%} .. {summary['hacAlpha']['ci95High']:.2%}</b></div>
      <div class="statline"><span>回归Beta</span><b>{summary['hacAlpha']['beta']:.3f}</b></div>
    </div>
    <div>
      <div class="statline"><span>试验数量</span><b>1个固定规则</b></div>
      <div class="statline"><span>DSR</span><b>not_applicable</b></div>
      <div class="statline"><span>PBO</span><b>not_applicable</b></div>
      <div class="statline"><span>原因</span><b>未从参数网格挑冠军</b></div>
    </div>
  </div>
</section>

<section class="section">
  <h2>支持、反对与尚缺证据</h2>
  <div class="evidence-grid">
    <div class="evidence support"><h3>支持证据</h3><ul>{supports}</ul></div>
    <div class="evidence against"><h3>反对证据</h3><ul>{against}</ul></div>
    <div class="evidence missing"><h3>尚缺证据</h3><ul>{missing}</ul></div>
  </div>
</section>

<section class="section">
  <h2>指标怎么读</h2>
  <div class="rule-grid">
    <div class="rule"><span class="tag">复合收益</span><b>CAGR</b><p>期末净值相对期初净值的年均复合增长率；它回答长期财富每年等效增长多少。</p></div>
    <div class="rule"><span class="tag">峰谷风险</span><b>最大回撤 / Calmar</b><p>最大回撤是最坏峰谷跌幅；Calmar = CAGR ÷ 最大回撤绝对值，衡量每单位最坏回撤换来多少增长。</p></div>
    <div class="rule"><span class="tag">日度风险收益</span><b>Sharpe / Sortino</b><p>Sharpe用全部波动归一化平均收益；Sortino只用下行波动，二者都不能代替回撤和尾部检查。</p></div>
    <div class="rule"><span class="tag">尾部</span><b>日度ES95</b><p>进入最差5%日收益后，平均亏损有多大；比只给一个分位点的VaR更能描述坏日子。</p></div>
    <div class="rule"><span class="tag">主动能力</span><b>信息比率 / Alpha</b><p>信息比率衡量每单位主动波动的主动收益；Alpha用HAC稳健标准误检查扣除市场暴露后的剩余收益。</p></div>
    <div class="rule"><span class="tag">执行摩擦</span><b>换手 / 成本</b><p>单边换手累计记录仓位变化规模；成本率汇总佣金、税费代理与滑点，零成本场景只用于归因。</p></div>
  </div>
</section>

<section class="section">
  <h2>优化方向不是继续调均线</h2>
  <div class="callout"><strong>停止：</strong>{escape(summary['nextStep']['stop'])}</div>
  <p><b>下一项最小研究：</b>{escape(summary['nextStep']['candidate'])}</p>
  <p class="note">{escape(summary['nextStep']['boundary'])}</p>
</section>

<section class="section">
  <h2>数据质量与复现身份</h2>
  <p class="note">质量运行 {escape(summary['quality']['qualityRunId'])}：{summary['quality']['passedRules']} 条通过、{summary['quality']['failedRules']} 条失败；三个运行共享快照 <code>{escape(summary['quality']['snapshotId'])}</code>。审计文件 <code>{escape(summary['reproductionAudit']['evidenceFile'])}</code> 已校验三个运行在镜像 <code>{escape(summary['reproductionAudit']['imageDigest'])}</code>、禁用网络条件下连续 {summary['reproductionAudit']['matchesPerRun']} 轮结果指纹。</p>
  <div class="table-wrap">{identities}</div>
  <div class="callout"><strong>研究边界：</strong>离线模拟、无真实券商、无下单、无收益承诺。代码提交 <code>{escape(summary['quality']['codeCommit'])}</code>；生产schema revision <code>{escape(summary['quality']['schemaRevision'])}</code>。</div>
</section>

<footer class="footer">策略评价规范 1.0 · 初始本金统一为 ¥100,000 · 报告生成 {escape(summary['reportGeneratedAt'])}</footer>
</main></body></html>"""


if __name__ == "__main__":
    raise SystemExit(main())
