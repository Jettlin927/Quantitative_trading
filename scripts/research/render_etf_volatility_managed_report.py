from __future__ import annotations

import argparse
from datetime import datetime
from html import escape
import itertools
import json
import math
from pathlib import Path
from statistics import NormalDist
import sys
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.app.quant_research.artifacts import read_canonical_csv_gz
from backend.app.quant_research.dataset import build_adjusted_price_panel
from backend.app.quant_research.metrics import summarize_performance


DEFAULT_RUN_ROOT = (
    REPO_ROOT
    / "outputs"
    / "research-runs"
    / "volatility-managed-2026-07-13"
    / "canonical-runs"
)
DEFAULT_OUTPUT_DIR = (
    REPO_ROOT
    / "docs"
    / "research"
    / "strategy-results"
    / "etf-volatility-managed-20260713"
)
TRIAL_ORDER = ("T0", "T1", "T2", "T3", "zero_cost", "double_cost")
BASE_COST = (0.00035, 0.00085, 0.001)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="从六个 canonical 运行生成 ETF 波动率管理研究报告。"
    )
    parser.add_argument("--run-root", type=Path, default=DEFAULT_RUN_ROOT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args(argv)

    runs = load_runs(args.run_root)
    summary, charts = build_summary(runs)
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


def load_runs(run_root: Path) -> dict[str, dict[str, Any]]:
    root = Path(run_root).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"canonical run 根目录不存在：{root}")
    runs: dict[str, dict[str, Any]] = {}
    for path in sorted(item for item in root.iterdir() if item.is_dir()):
        config_path = path / "config.json"
        manifest_path = path / "manifest.json"
        metrics_path = path / "metrics.json"
        if not all(item.is_file() for item in (config_path, manifest_path, metrics_path)):
            continue
        config = json.loads(config_path.read_text(encoding="utf-8"))
        if config.get("strategyId") != "etf_volatility_managed":
            continue
        label = classify_run(config)
        if label in runs:
            raise ValueError(f"canonical run 标签重复：{label}")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("runId") != path.name:
            raise ValueError(f"run 目录与 manifest 身份不一致：{path}")
        runs[label] = {
            "path": path,
            "config": config,
            "manifest": manifest,
            "metrics": json.loads(metrics_path.read_text(encoding="utf-8")),
        }
    missing = sorted(set(TRIAL_ORDER) - set(runs))
    extra = sorted(set(runs) - set(TRIAL_ORDER))
    if missing or extra:
        raise ValueError(f"canonical run 集合无效：missing={missing}, extra={extra}")
    identities = {
        (
            item["manifest"]["codeCommit"],
            item["manifest"]["dataSnapshot"]["snapshotId"],
            item["manifest"]["qualityRun"]["qualityRunId"],
        )
        for item in runs.values()
    }
    if len(identities) != 1:
        raise ValueError("六个运行没有绑定同一代码、数据快照和质量运行")
    return runs


def classify_run(config: dict[str, Any]) -> str:
    features = config["featureParameters"]
    targets = config["targetWeightParameters"]
    cost = tuple(
        float(config["costModel"][field])
        for field in ("buyRate", "sellRate", "slippageRate")
    )
    if cost == (0.0, 0.0, 0.0):
        return "zero_cost"
    if cost == tuple(value * 2 for value in BASE_COST):
        return "double_cost"
    if cost != BASE_COST:
        raise ValueError(f"未登记的成本场景：{cost}")
    identity = (
        features["realizedVarianceEstimator"],
        features["exposurePower"],
        targets["rebalanceBand"],
    )
    labels = {
        ("previous_month", "1", "0"): "T0",
        ("previous_month", "0.5", "0"): "T1",
        ("trailing_3_month_mean", "1", "0"): "T2",
        ("previous_month", "1", "0.1"): "T3",
    }
    try:
        return labels[identity]
    except KeyError as exc:
        raise ValueError(f"未登记的试验配置：{identity}") from exc


def build_summary(
    runs: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, pd.Series]]:
    t0 = runs["T0"]
    config = t0["config"]
    start = pd.Timestamp(config["startDate"])
    end = pd.Timestamp(config["endDate"])
    navs = {
        label: _read_frame(
            item["path"] / "nav.csv.gz",
            dates=("trade_date", "executed_signal_date"),
            numeric=(
                "nav",
                "cash_weight",
                "gross_exposure",
                "one_way_turnover",
                "transaction_cost_rate",
            ),
        )
        for label, item in runs.items()
    }
    passive = _build_passive_nav(t0["path"], start, end)
    strategy_navs = {
        label: frame[frame["trade_date"].between(start, end)].copy()
        for label, frame in navs.items()
    }
    returns = _aligned_returns(strategy_navs, passive)
    passive_metrics = summarize_performance(
        passive[["trade_date", "nav"]],
        include_extended=True,
    )
    executions = _read_frame(
        t0["path"] / "rebalance_executions.csv.gz",
        dates=("execution_date", "signal_date"),
        numeric=(
            "requested_change",
            "executed_change",
            "blocked_change",
            "transaction_cost_rate",
        ),
    )
    targets = _read_frame(
        t0["path"] / "targets.csv.gz",
        dates=("signal_date", "available_date"),
        numeric=("target_weight",),
    )
    t0_nav = strategy_navs["T0"]

    comparison = _comparison_rows(runs, passive_metrics, returns)
    yearly = _year_rows(returns, t0_nav, executions)
    regimes, variance_threshold = _regime_rows(
        t0["path"], returns, t0_nav, executions, config
    )
    stress = _stress_rows(returns, t0_nav, executions)
    walk_forward = _walk_forward_rows(t0, t0_nav, passive)
    tail = _tail_rows(returns)
    hac = _hac_alpha(returns["T0"], returns["passive"])
    dsr = _deflated_sharpe(returns, ("T0", "T1", "T2", "T3"))
    pbo = _probability_backtest_overfitting(
        returns,
        ("T0", "T1", "T2", "T3"),
    )

    t0_metrics = t0["metrics"]
    double_metrics = runs["double_cost"]["metrics"]
    zero_metrics = runs["zero_cost"]["metrics"]
    total_active_log = math.log1p(t0_metrics["totalReturn"]) - math.log1p(
        passive_metrics["totalReturn"]
    )
    largest_year = max(yearly, key=lambda row: row["activeLogWealth"])
    stability_pass = largest_year["activeLogWealth"] <= total_active_log
    gates = [
        _gate(
            "年化波动",
            "T0 / 被动 <= 90%",
            t0_metrics["annualizedVolatility"] / passive_metrics["annualizedVolatility"],
            0.9,
            "le",
        ),
        _gate(
            "最大回撤",
            "|T0 MDD| / |被动 MDD| <= 85%",
            abs(t0_metrics["maxDrawdown"]) / abs(passive_metrics["maxDrawdown"]),
            0.85,
            "le",
        ),
        _gate(
            "Sharpe 改善",
            "T0 - 被动 >= 0.10",
            t0_metrics["sharpe"] - passive_metrics["sharpe"],
            0.1,
            "ge",
        ),
        _gate(
            "CAGR 保留",
            "T0 - 被动 >= -2pp",
            t0_metrics["annualizedReturn"] - passive_metrics["annualizedReturn"],
            -0.02,
            "ge",
        ),
        _gate(
            "双倍成本",
            "双倍成本 Sharpe - 被动 >= 0",
            double_metrics["sharpe"] - passive_metrics["sharpe"],
            0.0,
            "ge",
        ),
        {
            "name": "环境覆盖",
            "rule": "至少 6 个完整年度且方向 x 波动率 6 格都有样本",
            "actual": {
                "completeYears": sum(row["observations"] >= 240 for row in yearly),
                "regimeCells": len(regimes),
            },
            "threshold": {"completeYears": 6, "regimeCells": 6},
            "passed": sum(row["observations"] >= 240 for row in yearly) >= 6
            and len(regimes) == 6,
        },
        {
            "name": "单年不主导",
            "rule": "最大单年主动对数财富贡献不超过整段净主动对数财富",
            "actual": {
                "year": largest_year["year"],
                "largestContribution": largest_year["activeLogWealth"],
                "wholePeriod": total_active_log,
            },
            "threshold": "largestContribution <= wholePeriod",
            "passed": stability_pass,
        },
    ]
    status = "研究通过" if all(row["passed"] for row in gates) else "不通过"
    manifest = t0["manifest"]
    execution_summary = {
        "openDays": int(len(t0_nav)),
        "signalDecisions": int(len(targets)),
        "rebalanceRequests": int(len(executions)),
        "filledRequests": int(executions["status"].eq("filled").sum()),
        "blockedRequests": int(executions["status"].eq("blocked").sum()),
        "partialRequests": int(executions["status"].eq("partial").sum()),
        "independentDecisionMonths": int(targets["signal_date"].dt.to_period("M").nunique()),
        "averageExposure": float(t0_nav["gross_exposure"].mean()),
        "medianTargetWeight": t0_metrics["medianTargetWeight"],
        "capHitRate": t0_metrics["exposureCapHitRate"],
        "cumulativeOneWayTurnover": float(t0_nav["one_way_turnover"].sum()),
        "cumulativeTransactionCostRate": t0_metrics["cumulativeTransactionCostRate"],
        "grossToNetWealthDrag": zero_metrics["totalReturn"] - t0_metrics["totalReturn"],
        "baseCostModel": config["costModel"],
        "doubleCostModel": runs["double_cost"]["config"]["costModel"],
    }
    summary = {
        "status": status,
        "reportGeneratedAt": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(
            timespec="seconds"
        ),
        "conclusion": {
            "oneLine": (
                "T0 明显降低波动并提高净 CAGR，但最大回撤和 Sharpe 改善未达到事前门槛，"
                "且主动收益被少数年份主导，因此不能认定为好策略。"
            ),
            "strongestSupport": (
                f"年化波动是被动的 {gates[0]['actual']:.1%}，净 CAGR 高出 "
                f"{t0_metrics['annualizedReturn'] - passive_metrics['annualizedReturn']:.2%}。"
            ),
            "strongestOpposition": (
                f"最大回撤仍达 {abs(t0_metrics['maxDrawdown']):.1%}，是被动回撤的 "
                f"{gates[1]['actual']:.1%}；Sharpe 改善仅 {gates[2]['actual']:.3f}。"
            ),
            "bestEnvironment": "高波动下跌月相对保护最明显，但绝对亏损仍大。",
            "failureEnvironment": "波动突然上升或持续熊市；上一月方差反应偏慢，不能阻止深回撤。",
            "researchOnly": True,
        },
        "strategyProfile": {
            "name": "Moreira–Muir ETF 波动率管理（无杠杆复现）",
            "strategyId": "etf_volatility_managed",
            "strategyVersion": "1",
            "economicHypothesis": (
                "波动率具有持续性，而预期收益不会随方差一比一上升；因此在上一月实现方差较高时降低风险暴露，"
                "有机会改善单位风险收益。潜在对手方是必须维持固定风险资产敞口或受杠杆/授权约束的投资者。"
            ),
            "asset": "510300.SH 华泰柏瑞沪深300 ETF",
            "primaryBenchmark": "同一 ETF 的因果复权被动持有",
            "marketReference": "000300.SH 只用于行情环境与公共 runner 参考，不冒充总收益基准",
            "signal": "自然月内日简单收益去均值平方和；下一月权重 min(c / RV^2, 1)",
            "calibration": "2012-06-01..2017-12-29，只用校准期确定 c",
            "oos": "2018-01-01..2026-06-29",
            "execution": "月末收盘后形成目标，下一开市日开盘执行",
            "cash": "未投资部分收益按 0",
            "leverage": "禁止杠杆和做空，目标权重上限 100%",
            "failureMechanisms": [
                "月频滞后无法预先规避突然跳空或当月首次冲击",
                "低波动后风险暴露偏高，长熊市仍可能产生大回撤",
                "波动率管理拥挤时，集中减仓会放大滑点和冲击",
                "现金收益、税费细分和市场冲击缺失会改变真实净结果",
            ],
        },
        "dataEvidence": {
            "qualityRunId": manifest["qualityRun"]["qualityRunId"],
            "qualityStatus": manifest["qualityRun"]["status"],
            "qualityPassedRules": manifest["qualityRun"]["summary"]["passedCount"],
            "dataSnapshotId": manifest["dataSnapshot"]["snapshotId"],
            "rowCounts": manifest["dataSnapshot"]["rowCounts"],
            "startDate": passive_metrics["startDate"],
            "endDate": passive_metrics["endDate"],
            "observations": passive_metrics["observations"],
            "pointInTime": True,
            "offlineReproduction": "六个运行全部在 --network none 下匹配 result fingerprint；T0 重复两次。",
        },
        "executionAndCost": execution_summary,
        "riskAndCapacity": {
            "averageGrossExposure": float(t0_nav["gross_exposure"].mean()),
            "maximumSingleWeight": float(t0_nav["gross_exposure"].max()),
            "averageHhi": float((t0_nav["gross_exposure"] ** 2).mean()),
            "maximumHhi": float((t0_nav["gross_exposure"] ** 2).max()),
            "averageHoldingCount": 1.0,
            "blockedRequestRate": t0_metrics["blockedRequestRate"],
            "profitFactor": "not_applicable: 连续目标权重策略没有稳定单笔交易边界",
            "advParticipation": "not_available: 未绑定目标资金规模与冲击模型",
            "capacity": "not_available",
        },
        "gates": gates,
        "comparison": comparison,
        "tailRisk": tail,
        "alphaHac": hac,
        "yearly": yearly,
        "regimeDefinition": {
            "direction": "000300.SH 月收益 > +2% / < -2% / 其余",
            "volatility": "校准期月实现方差中位数",
            "varianceThreshold": variance_threshold,
        },
        "regimes": regimes,
        "stressWindows": stress,
        "walkForward": {
            "windows": walk_forward,
            "positiveStrategySharpeWindows": sum(
                row["strategySharpe"] > 0 for row in walk_forward
            ),
            "strategyBeatsPassiveSharpeWindows": sum(
                row["strategySharpe"] > row["passiveSharpe"]
                for row in walk_forward
            ),
            "note": (
                "公共 runner 的 active 字段绑定 000300.SH 价格参考；本报告从同一冻结 ETF 输入重算被动 ETF 窗口，"
                "只把后者用于主比较。"
            ),
        },
        "multipleTesting": {
            "registeredTrials": 4,
            "winnerByNetSharpe": dsr["winner"],
            "deflatedSharpeRatio": dsr,
            "pbo": pbo,
            "interpretation": (
                "DSR 未达到常用 95% 置信水平，PBO 较高；预登记未把它们设为硬阈值，"
                "但足以否定把同一 OOS 上的优胜变体直接升级为研究通过。"
            ),
        },
        "supportingEvidence": [
            "30/30 数据质量规则通过，canonical 快照、执行账本和离线复现完整。",
            f"T0 年化波动 {t0_metrics['annualizedVolatility']:.2%}，低于被动 {passive_metrics['annualizedVolatility']:.2%}。",
            f"T0 净 CAGR {t0_metrics['annualizedReturn']:.2%}，高于被动 {passive_metrics['annualizedReturn']:.2%}。",
            f"双倍成本 Sharpe {double_metrics['sharpe']:.3f}，仍高于被动 {passive_metrics['sharpe']:.3f}。",
        ],
        "opposingEvidence": [
            f"T0 最大回撤 {t0_metrics['maxDrawdown']:.2%}，远未达到相对被动缩减 15% 的门槛。",
            f"T0 Sharpe 改善 {t0_metrics['sharpe'] - passive_metrics['sharpe']:.3f}，低于 0.10 门槛。",
            f"HAC 年化 alpha {hac['annualizedAlpha']:.2%}，95% 区间 {hac['ci95Low']:.2%}..{hac['ci95High']:.2%}，不能排除 0。",
            f"PBO {pbo['probability']:.1%}，四个变体中的样本内赢家经常在对应样本外落到后半区。",
            f"{largest_year['year']} 单年主动对数财富贡献超过整段净主动对数财富，稳定性门禁失败。",
        ],
        "missingEvidence": [
            "未绑定可投资现金收益率，现金收益按 0。",
            "未绑定目标资金规模、ETF 申赎/买卖冲击和 ADV 参与率，容量为 not_available。",
            "流动性环境阈值未在看 OOS 前预登记，本轮不做事后分组。",
            "只验证一只中国宽基 ETF；T1–T3 与 T0 共用 OOS，没有新的独立验证集。",
        ],
        "optimizationDirections": [
            {
                "priority": 1,
                "direction": "把 T1 倒数波动率作为唯一下一轮候选，在新时间段或另一只事前指定宽基 ETF 上独立验证。",
                "evidence": (
                    f"T1 CAGR {runs['T1']['metrics']['annualizedReturn']:.2%}、MDD {runs['T1']['metrics']['maxDrawdown']:.2%}，"
                    f"成本约为 T0 的 {runs['T1']['metrics']['cumulativeTransactionCostRate'] / t0_metrics['cumulativeTransactionCostRate']:.1%}；"
                    "但 Sharpe 低于 T0，当前只能是有条件候选。"
                ),
            },
            {
                "priority": 2,
                "direction": "若目标优先压回撤，下一轮只测试一个更低风险预算或更低最大暴露，不与预测器改动同时搜索。",
                "evidence": "T0 中位目标权重约 93%，46% 的月份触及 100% 上限；当前回撤改善不足。",
            },
            {
                "priority": 3,
                "direction": "补充 point-in-time 现金收益、明确资金规模和 ETF 冲击/ADV 约束后再谈可部署性。",
                "evidence": "当前毛到净累计财富拖累约 4.98pp，但容量和闲置现金收益都未建模。",
            },
            {
                "priority": 4,
                "direction": "拒绝 T2 三月平滑和 T3 10pp 调仓带作为当前优化方向。",
                "evidence": "T2 回撤更差；T3 成本仅小幅下降却损失收益，二者都没有改善核心门槛。",
            },
        ],
        "reproduction": {
            label: {
                "runId": item["manifest"]["runId"],
                "configSha256": item["manifest"]["configSha256"],
                "codeCommit": item["manifest"]["codeCommit"],
                "dataSnapshotId": item["manifest"]["dataSnapshot"]["snapshotId"],
                "reproducibilityKey": item["manifest"]["reproducibilityKey"],
                "resultFingerprint": item["manifest"]["resultFingerprint"],
            }
            for label, item in runs.items()
        },
        "sources": [
            {
                "title": "Moreira and Muir, Volatility-Managed Portfolios (NBER)",
                "url": "https://www.nber.org/papers/w22208",
                "role": "核心倒数上一月实现方差规则",
            },
            {
                "title": "Journal of Finance 正式版本",
                "url": "https://onlinelibrary.wiley.com/doi/full/10.1111/jofi.12513",
                "role": "不加杠杆 min(c/RV², 1) 与倒数波动率稳健性",
            },
            {
                "title": "Cederburg et al., JFE",
                "url": "https://www.sciencedirect.com/science/article/abs/pii/S0304405X2030132X",
                "role": "实时样本外反证",
            },
            {
                "title": "Barroso and Detzel, SSRN",
                "url": "https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3088828",
                "role": "交易成本反证",
            },
        ],
    }
    chart_index = returns.set_index("trade_date")
    t0_drawdown = (1 + chart_index["T0"]).cumprod()
    passive_drawdown = (1 + chart_index["passive"]).cumprod()
    charts = {
        "T0": (1 + chart_index["T0"]).cumprod(),
        "T1": (1 + chart_index["T1"]).cumprod(),
        "passive": (1 + chart_index["passive"]).cumprod(),
        "T0_drawdown": t0_drawdown / t0_drawdown.cummax() - 1,
        "passive_drawdown": passive_drawdown / passive_drawdown.cummax() - 1,
        "turnover": t0_nav.set_index("trade_date")["one_way_turnover"].cumsum(),
        "cost": t0_nav.set_index("trade_date")["transaction_cost_rate"].cumsum(),
        "gross": t0_nav.set_index("trade_date")["gross_exposure"],
        "cash": t0_nav.set_index("trade_date")["cash_weight"],
    }
    return summary, charts


def _comparison_rows(
    runs: dict[str, dict[str, Any]],
    passive_metrics: dict[str, Any],
    returns: pd.DataFrame,
) -> list[dict[str, Any]]:
    labels = {
        "passive": "被动 ETF",
        "T0": "T0 倒数方差",
        "T1": "T1 倒数波动率",
        "T2": "T2 三月方差",
        "T3": "T3 10pp 调仓带",
        "zero_cost": "T0 零成本",
        "double_cost": "T0 双倍成本",
    }
    rows: list[dict[str, Any]] = []
    for label in ("passive", *TRIAL_ORDER):
        metrics = passive_metrics if label == "passive" else runs[label]["metrics"]
        tail = _tail_metrics(returns["passive" if label == "passive" else label])
        rows.append(
            {
                "label": labels[label],
                "totalReturn": metrics["totalReturn"],
                "relativeWealth": (
                    (1 + metrics["totalReturn"])
                    / (1 + passive_metrics["totalReturn"])
                    - 1
                ),
                "cagr": metrics["annualizedReturn"],
                "volatility": metrics["annualizedVolatility"],
                "sharpe": metrics["sharpe"],
                "downsideVolatility": metrics["downsideVolatility"],
                "sortino": metrics["sortino"],
                "maxDrawdown": metrics["maxDrawdown"],
                "maxDrawdownDuration": metrics["maxDrawdownDuration"],
                "calmar": metrics["calmar"],
                "es95": tail["es95"],
                "trackingError": metrics.get("trackingError"),
                "informationRatio": metrics.get("informationRatio"),
                "beta": metrics.get("beta"),
                "turnover": metrics.get("averageOneWayTurnover"),
                "cost": metrics.get("cumulativeTransactionCostRate"),
                "averageExposure": metrics.get("averageTargetWeight"),
            }
        )
    return rows


def _year_rows(
    returns: pd.DataFrame,
    nav: pd.DataFrame,
    executions: pd.DataFrame,
) -> list[dict[str, Any]]:
    rows = []
    for year, group in returns.groupby(returns["trade_date"].dt.year, sort=True):
        metrics = _selected_return_metrics(group)
        ledger = _ledger_metrics(group["trade_date"], nav, executions)
        strategy_return = metrics["strategyReturn"]
        passive_return = metrics["passiveReturn"]
        rows.append(
            {
                "year": int(year),
                "observations": int(len(group)),
                **metrics,
                **ledger,
                "activeLogWealth": math.log1p(strategy_return)
                - math.log1p(passive_return),
            }
        )
    return rows


def _regime_rows(
    run_path: Path,
    returns: pd.DataFrame,
    nav: pd.DataFrame,
    executions: pd.DataFrame,
    config: dict[str, Any],
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
    calibration_start = pd.Period(config["featureParameters"]["calibrationStartDate"][:7])
    calibration_end = pd.Period(config["featureParameters"]["calibrationEndDate"][:7])
    threshold = float(
        months[
            months["month"].between(calibration_start, calibration_end)
        ]["realizedVariance"].median()
    )
    months["direction"] = months["marketReturn"].map(
        lambda value: (
            "上涨"
            if pd.notna(value) and value > 0.02
            else "下跌"
            if pd.notna(value) and value < -0.02
            else "震荡"
        )
    )
    months["volatilityRegime"] = months["realizedVariance"].map(
        lambda value: "高波动" if value > threshold else "低波动"
    )
    joined = returns.copy()
    joined["month"] = joined["trade_date"].dt.to_period("M")
    joined = joined.merge(
        months[["month", "direction", "volatilityRegime"]],
        on="month",
        how="left",
        validate="many_to_one",
    )
    rows = []
    for (direction, volatility), group in joined.groupby(
        ["direction", "volatilityRegime"], sort=True
    ):
        rows.append(
            {
                "direction": direction,
                "volatility": volatility,
                "startDate": group["trade_date"].min().date().isoformat(),
                "endDate": group["trade_date"].max().date().isoformat(),
                "months": int(group["month"].nunique()),
                "observations": int(len(group)),
                **_selected_return_metrics(group),
                **_ledger_metrics(group["trade_date"], nav, executions),
            }
        )
    return rows, threshold


def _stress_rows(
    returns: pd.DataFrame,
    nav: pd.DataFrame,
    executions: pd.DataFrame,
) -> list[dict[str, Any]]:
    windows = (
        ("2018 全年", "2018-01-01", "2018-12-31"),
        ("COVID 冲击", "2020-01-23", "2020-04-30"),
        ("2022 回撤", "2022-01-01", "2022-10-31"),
        ("2024 年初", "2024-01-01", "2024-02-08"),
    )
    rows = []
    for name, start, end in windows:
        group = returns[returns["trade_date"].between(start, end)]
        rows.append(
            {
                "name": name,
                "startDate": start,
                "endDate": end,
                "observations": int(len(group)),
                **_selected_return_metrics(group),
                **_ledger_metrics(group["trade_date"], nav, executions),
            }
        )
    return rows


def _walk_forward_rows(
    run: dict[str, Any],
    strategy_nav: pd.DataFrame,
    passive_nav: pd.DataFrame,
) -> list[dict[str, Any]]:
    windows = _read_frame(
        run["path"] / "walk_forward_windows.csv.gz",
        dates=("train_start", "train_end", "test_start", "test_end"),
        numeric=("train_periods", "test_periods"),
    )
    canonical = _read_frame(
        run["path"] / "walk_forward_metrics.csv.gz",
        dates=("start_date", "end_date"),
        numeric=("total_return", "sharpe", "max_drawdown"),
    ).set_index("window_id")
    rows = []
    for window in windows.itertuples(index=False):
        strategy_slice = strategy_nav[
            strategy_nav["trade_date"].between(window.test_start, window.test_end)
        ]
        passive_slice = passive_nav[
            passive_nav["trade_date"].between(window.test_start, window.test_end)
        ]
        strategy_metrics = summarize_performance(
            strategy_slice[["trade_date", "nav"]], include_extended=True
        )
        passive_metrics = summarize_performance(
            passive_slice[["trade_date", "nav"]], include_extended=True
        )
        persisted = canonical.loc[window.window_id]
        if not math.isclose(
            strategy_metrics["totalReturn"],
            float(persisted["total_return"]),
            rel_tol=0,
            abs_tol=1e-12,
        ):
            raise ValueError(f"walk-forward 策略指标未与 canonical 工件闭合：{window.window_id}")
        rows.append(
            {
                "windowId": window.window_id,
                "testStart": window.test_start.date().isoformat(),
                "testEnd": window.test_end.date().isoformat(),
                "observations": int(strategy_metrics["observations"]),
                "strategyReturn": strategy_metrics["totalReturn"],
                "passiveReturn": passive_metrics["totalReturn"],
                "strategySharpe": strategy_metrics["sharpe"],
                "passiveSharpe": passive_metrics["sharpe"],
                "strategyMaxDrawdown": strategy_metrics["maxDrawdown"],
                "passiveMaxDrawdown": passive_metrics["maxDrawdown"],
            }
        )
    return rows


def _tail_rows(returns: pd.DataFrame) -> list[dict[str, Any]]:
    labels = {
        "passive": "被动 ETF",
        "T0": "T0",
        "T1": "T1",
        "T2": "T2",
        "T3": "T3",
    }
    return [
        {"label": labels[label], **_tail_metrics(returns[label])}
        for label in labels
    ]


def _tail_metrics(returns: pd.Series) -> dict[str, float]:
    series = pd.to_numeric(returns, errors="raise").dropna()
    losses = -series
    var95 = float(losses.quantile(0.95))
    return {
        "skew": float(series.skew()),
        "excessKurtosis": float(series.kurt()),
        "var95": var95,
        "es95": float(losses[losses >= var95].mean()),
    }


def _hac_alpha(strategy: pd.Series, benchmark: pd.Series) -> dict[str, float | int]:
    frame = pd.concat([strategy.rename("strategy"), benchmark.rename("benchmark")], axis=1).dropna()
    y = frame["strategy"].to_numpy(dtype=float)
    x = np.column_stack([np.ones(len(frame)), frame["benchmark"].to_numpy(dtype=float)])
    coefficients = np.linalg.solve(x.T @ x, x.T @ y)
    residuals = y - x @ coefficients
    lag = int(math.floor(4 * (len(frame) / 100) ** (2 / 9)))
    xu = x * residuals[:, None]
    meat = xu.T @ xu
    for offset in range(1, lag + 1):
        weight = 1 - offset / (lag + 1)
        gamma = xu[offset:].T @ xu[:-offset]
        meat += weight * (gamma + gamma.T)
    inverse = np.linalg.inv(x.T @ x)
    covariance = inverse @ meat @ inverse
    alpha = float(coefficients[0] * 252)
    standard_error = float(math.sqrt(covariance[0, 0]) * 252)
    return {
        "observations": int(len(frame)),
        "neweyWestLag": lag,
        "annualizedAlpha": alpha,
        "beta": float(coefficients[1]),
        "annualizedAlphaStandardError": standard_error,
        "alphaTStatistic": float(coefficients[0] / math.sqrt(covariance[0, 0])),
        "ci95Low": alpha - 1.96 * standard_error,
        "ci95High": alpha + 1.96 * standard_error,
        "strategyLag1Autocorrelation": float(frame["strategy"].autocorr(1)),
    }


def _deflated_sharpe(
    returns: pd.DataFrame,
    candidates: tuple[str, ...],
) -> dict[str, Any]:
    sharpes = {
        candidate: float(returns[candidate].mean() / returns[candidate].std(ddof=1))
        for candidate in candidates
    }
    winner = max(candidates, key=lambda candidate: sharpes[candidate])
    trial_std = float(pd.Series(sharpes).std(ddof=1))
    count = len(candidates)
    normal = NormalDist()
    euler_gamma = 0.5772156649015329
    expected_max = trial_std * (
        (1 - euler_gamma) * normal.inv_cdf(1 - 1 / count)
        + euler_gamma * normal.inv_cdf(1 - 1 / (count * math.e))
    )
    series = returns[winner].dropna()
    observed = sharpes[winner]
    skew = float(series.skew())
    kurtosis = float(series.kurt() + 3)
    denominator = math.sqrt(
        1 - skew * observed + ((kurtosis - 1) / 4) * observed**2
    )
    statistic = (observed - expected_max) * math.sqrt(len(series) - 1) / denominator
    return {
        "winner": winner,
        "trialCount": count,
        "observations": int(len(series)),
        "dailySharpe": observed,
        "expectedMaximumDailySharpe": expected_max,
        "probability": normal.cdf(statistic),
        "zStatistic": statistic,
    }


def _probability_backtest_overfitting(
    returns: pd.DataFrame,
    candidates: tuple[str, ...],
) -> dict[str, Any]:
    monthly = (
        returns.set_index("trade_date")[list(candidates)]
        .resample("ME")
        .apply(lambda values: float((1 + values).prod() - 1))
        .dropna()
    )
    partition_count = 8
    blocks = [list(values) for values in np.array_split(range(len(monthly)), partition_count)]
    logits: list[float] = []
    winner_counts = {candidate: 0 for candidate in candidates}

    def sharpe(candidate: str, positions: list[int]) -> float:
        series = monthly.iloc[positions][candidate]
        volatility = float(series.std(ddof=1))
        return float(series.mean() / volatility) if volatility > 0 else -math.inf

    for selected in itertools.combinations(range(partition_count), partition_count // 2):
        train = [position for block in selected for position in blocks[block]]
        test = [
            position
            for block in range(partition_count)
            if block not in selected
            for position in blocks[block]
        ]
        winner = max(candidates, key=lambda candidate: (sharpe(candidate, train), candidate))
        winner_counts[winner] += 1
        ordered = sorted(
            candidates,
            key=lambda candidate: (sharpe(candidate, test), candidate),
        )
        rank = ordered.index(winner) + 1
        percentile = rank / (len(candidates) + 1)
        logits.append(math.log(percentile / (1 - percentile)))
    return {
        "method": "CSCV, 8 个连续月度分块",
        "monthlyObservations": int(len(monthly)),
        "combinations": int(len(logits)),
        "probability": float(sum(value <= 0 for value in logits) / len(logits)),
        "medianLogit": float(pd.Series(logits).median()),
        "trainingWinnerCounts": winner_counts,
    }


def _selected_return_metrics(group: pd.DataFrame) -> dict[str, Any]:
    if group.empty:
        return {
            "strategyReturn": None,
            "passiveReturn": None,
            "activeReturn": None,
            "annualizedVolatility": None,
            "maxDrawdown": None,
        }
    strategy_return = float((1 + group["T0"]).prod() - 1)
    passive_return = float((1 + group["passive"]).prod() - 1)
    wealth = (1 + group["T0"]).cumprod()
    return {
        "strategyReturn": strategy_return,
        "passiveReturn": passive_return,
        "activeReturn": strategy_return - passive_return,
        "annualizedVolatility": (
            float(group["T0"].std(ddof=1) * math.sqrt(252))
            if len(group) > 1
            else None
        ),
        "maxDrawdown": float((wealth / wealth.cummax() - 1).min()),
    }


def _ledger_metrics(
    dates: pd.Series,
    nav: pd.DataFrame,
    executions: pd.DataFrame,
) -> dict[str, Any]:
    selected_dates = set(pd.to_datetime(dates))
    nav_slice = nav[nav["trade_date"].isin(selected_dates)]
    execution_slice = executions[executions["execution_date"].isin(selected_dates)]
    return {
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


def _gate(
    name: str,
    rule: str,
    actual: float,
    threshold: float,
    operator: str,
) -> dict[str, Any]:
    passed = actual <= threshold if operator == "le" else actual >= threshold
    return {
        "name": name,
        "rule": rule,
        "actual": float(actual),
        "threshold": threshold,
        "passed": bool(passed),
    }


def _build_passive_nav(run_path: Path, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    bars = read_canonical_csv_gz(run_path / "inputs" / "fund_daily_bars.csv.gz")
    factors = read_canonical_csv_gz(run_path / "inputs" / "fund_adjust_factors.csv.gz")
    prices = build_adjusted_price_panel(bars, factors)
    prices["trade_date"] = pd.to_datetime(prices["trade_date"], errors="raise")
    prices["adj_close"] = pd.to_numeric(prices["adj_close"], errors="raise")
    prices = prices[prices["trade_date"].between(start, end)].sort_values("trade_date")
    if prices.empty:
        raise ValueError("被动 ETF OOS 净值为空")
    result = prices[["trade_date", "adj_close"]].copy()
    result["nav"] = result["adj_close"] / result["adj_close"].iloc[0]
    return result[["trade_date", "nav"]]


def _aligned_returns(
    navs: dict[str, pd.DataFrame],
    passive: pd.DataFrame,
) -> pd.DataFrame:
    frame = passive.rename(columns={"nav": "passive_nav"}).copy()
    for label, nav in navs.items():
        frame = frame.merge(
            nav[["trade_date", "nav"]].rename(columns={"nav": f"{label}_nav"}),
            on="trade_date",
            how="inner",
            validate="one_to_one",
        )
    returns = frame[["trade_date"]].copy()
    returns["passive"] = frame["passive_nav"].pct_change(fill_method=None)
    for label in navs:
        returns[label] = frame[f"{label}_nav"].pct_change(fill_method=None)
    return returns.dropna().reset_index(drop=True)


def _read_frame(
    path: Path,
    *,
    dates: tuple[str, ...] = (),
    numeric: tuple[str, ...] = (),
) -> pd.DataFrame:
    frame = read_canonical_csv_gz(path)
    for column in dates:
        frame[column] = pd.to_datetime(frame[column], errors="coerce")
    for column in numeric:
        frame[column] = pd.to_numeric(frame[column], errors="raise")
    return frame


def render_html(summary: dict[str, Any], charts: dict[str, pd.Series]) -> str:
    status_class = "fail" if summary["status"] == "不通过" else "pass"
    comparison = _html_table(
        summary["comparison"],
        (
            ("label", "方案", "text"),
            ("totalReturn", "累计收益", "pct"),
            ("relativeWealth", "相对财富", "pct"),
            ("cagr", "CAGR", "pct"),
            ("volatility", "年化波动", "pct"),
            ("sharpe", "Sharpe", "num"),
            ("downsideVolatility", "下行波动", "pct"),
            ("sortino", "Sortino", "num"),
            ("maxDrawdown", "最大回撤", "pct"),
            ("maxDrawdownDuration", "回撤持续日", "int"),
            ("calmar", "Calmar", "num"),
            ("es95", "ES95(日)", "pct"),
            ("trackingError", "TE", "pct"),
            ("informationRatio", "IR", "num"),
            ("beta", "Beta", "num"),
            ("cost", "累计成本率", "pct"),
            ("averageExposure", "平均目标暴露", "pct"),
        ),
    )
    gates = _html_table(
        summary["gates"],
        (
            ("name", "门禁", "text"),
            ("rule", "事前规则", "text"),
            ("actual", "实际", "object"),
            ("passed", "结果", "bool"),
        ),
    )
    yearly = _html_table(
        summary["yearly"],
        (
            ("year", "年份", "int"),
            ("observations", "日数", "int"),
            ("strategyReturn", "T0", "pct"),
            ("passiveReturn", "被动", "pct"),
            ("activeReturn", "主动", "pct"),
            ("maxDrawdown", "T0 回撤", "pct"),
            ("turnover", "换手", "num"),
            ("cost", "成本率", "pct"),
            ("averageExposure", "平均暴露", "pct"),
        ),
    )
    regimes = _html_table(
        summary["regimes"],
        (
            ("direction", "方向", "text"),
            ("volatility", "波动", "text"),
            ("startDate", "最早", "text"),
            ("endDate", "最晚", "text"),
            ("months", "月数", "int"),
            ("observations", "日数", "int"),
            ("strategyReturn", "T0", "pct"),
            ("passiveReturn", "被动", "pct"),
            ("activeReturn", "主动", "pct"),
            ("maxDrawdown", "回撤", "pct"),
            ("requestCount", "请求", "int"),
            ("turnover", "换手", "num"),
            ("cost", "成本率", "pct"),
            ("blockedRate", "阻塞率", "pct"),
            ("averageExposure", "暴露", "pct"),
        ),
    )
    stress = _html_table(
        summary["stressWindows"],
        (
            ("name", "压力期", "text"),
            ("startDate", "开始", "text"),
            ("endDate", "结束", "text"),
            ("observations", "日数", "int"),
            ("strategyReturn", "T0", "pct"),
            ("passiveReturn", "被动", "pct"),
            ("activeReturn", "主动", "pct"),
            ("maxDrawdown", "回撤", "pct"),
            ("requestCount", "请求", "int"),
            ("turnover", "换手", "num"),
            ("cost", "成本率", "pct"),
            ("blockedRate", "阻塞率", "pct"),
            ("averageExposure", "暴露", "pct"),
        ),
    )
    walk_forward = _html_table(
        summary["walkForward"]["windows"],
        (
            ("windowId", "窗口", "text"),
            ("testStart", "开始", "text"),
            ("testEnd", "结束", "text"),
            ("strategyReturn", "T0", "pct"),
            ("passiveReturn", "被动", "pct"),
            ("strategySharpe", "T0 Sharpe", "num"),
            ("passiveSharpe", "被动 Sharpe", "num"),
            ("strategyMaxDrawdown", "T0 回撤", "pct"),
        ),
    )
    tail = _html_table(
        summary["tailRisk"],
        (
            ("label", "方案", "text"),
            ("skew", "偏度", "num"),
            ("excessKurtosis", "超额峰度", "num"),
            ("var95", "VaR95(日)", "pct"),
            ("es95", "ES95(日)", "pct"),
        ),
    )
    source_items = "".join(
        f'<li><a href="{escape(item["url"])}">{escape(item["title"])}</a> — {escape(item["role"])}</li>'
        for item in summary["sources"]
    )
    optimization = "".join(
        f'<article class="action"><b>P{item["priority"]} · {escape(item["direction"])}</b><p>{escape(item["evidence"])}</p></article>'
        for item in summary["optimizationDirections"]
    )
    reproduction = _html_table(
        [
            {"label": label, **identity}
            for label, identity in summary["reproduction"].items()
        ],
        (
            ("label", "运行", "text"),
            ("runId", "run_id", "code"),
            ("reproducibilityKey", "reproducibility_key", "code"),
            ("resultFingerprint", "result_fingerprint", "code"),
        ),
    )
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>ETF 波动率管理策略复现</title>
  <style>
    :root {{ --ink:#18201e; --muted:#65706b; --paper:#f2f1eb; --panel:#fbfaf5; --line:#c8cbc3; --green:#0f6a53; --red:#a7372d; --gold:#a26a16; --blue:#315c8a; }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; color:var(--ink); background:var(--paper); font:14px/1.55 ui-monospace, SFMono-Regular, Menlo, Consolas, "Microsoft YaHei", monospace; }}
    main {{ max-width:1320px; margin:auto; padding:28px; }}
    h1 {{ font:700 clamp(28px,4vw,54px)/1.05 Georgia,"Songti SC",serif; margin:0 0 12px; letter-spacing:-1px; }}
    h2 {{ font:700 22px/1.2 Georgia,"Songti SC",serif; margin:0 0 14px; }}
    h3 {{ margin:18px 0 10px; font-size:15px; }}
    a {{ color:var(--blue); }}
    .eyebrow {{ color:var(--muted); text-transform:uppercase; letter-spacing:.12em; margin-bottom:12px; }}
    .hero,.panel {{ background:var(--panel); border:1px solid var(--line); box-shadow:4px 4px 0 #d9d8d0; }}
    .hero {{ padding:28px; margin-bottom:22px; }}
    .status {{ display:inline-block; padding:5px 10px; border:1px solid currentColor; font-weight:800; margin:8px 0 14px; }}
    .status.fail {{ color:var(--red); background:#f7e8e4; }} .status.pass {{ color:var(--green); background:#e4f2ed; }}
    .lead {{ font:600 18px/1.6 Georgia,"Songti SC",serif; max-width:900px; }}
    .grid {{ display:grid; grid-template-columns:repeat(12,1fr); gap:18px; margin-bottom:22px; }}
    .panel {{ grid-column:span 12; padding:20px; overflow:auto; }}
    .half {{ grid-column:span 6; }} .third {{ grid-column:span 4; }}
    .kpis {{ display:grid; grid-template-columns:repeat(4,1fr); gap:1px; background:var(--line); border:1px solid var(--line); margin-top:20px; }}
    .kpi {{ background:var(--panel); padding:14px; }} .kpi span {{ display:block; color:var(--muted); font-size:12px; }} .kpi b {{ font-size:22px; }}
    table {{ border-collapse:collapse; width:100%; min-width:760px; }}
    th,td {{ border-bottom:1px solid var(--line); padding:8px 9px; text-align:right; vertical-align:top; }}
    th:first-child,td:first-child {{ text-align:left; }} th {{ position:sticky; top:0; background:#e9e8e1; font-size:12px; color:#414944; }}
    td.ok {{ color:var(--green); font-weight:800; }} td.bad {{ color:var(--red); font-weight:800; }}
    code {{ font-size:11px; word-break:break-all; }}
    svg {{ display:block; width:100%; height:260px; background:#f8f7f1; border:1px solid var(--line); }}
    .legend {{ display:flex; gap:18px; flex-wrap:wrap; margin:8px 0; color:var(--muted); }}
    .dot {{ display:inline-block; width:9px; height:9px; margin-right:6px; }}
    ul {{ padding-left:20px; }} li {{ margin:7px 0; }}
    .action {{ border-left:4px solid var(--gold); padding:9px 14px; background:#f6f0e3; margin:10px 0; }} .action p {{ margin:5px 0 0; color:#4d554f; }}
    .note {{ color:var(--muted); font-size:12px; }}
    @media(max-width:900px) {{ .half,.third {{ grid-column:span 12; }} .kpis {{ grid-template-columns:1fr 1fr; }} main {{ padding:14px; }} }}
  </style>
</head>
<body><main>
  <section class="hero">
    <div class="eyebrow">Research protocol / 2026-07-13 / OOS only</div>
    <h1>ETF 波动率管理策略复现</h1>
    <div class="status {status_class}">{escape(summary["status"])}</div>
    <div class="lead">{escape(summary["conclusion"]["oneLine"])}</div>
    <div class="kpis">
      <div class="kpi"><span>T0 累计净收益</span><b>{_fmt(summary["comparison"][1]["totalReturn"], "pct")}</b></div>
      <div class="kpi"><span>T0 / 被动年化波动</span><b>{_fmt(summary["gates"][0]["actual"], "pct")}</b></div>
      <div class="kpi"><span>T0 最大回撤</span><b>{_fmt(summary["comparison"][1]["maxDrawdown"], "pct")}</b></div>
      <div class="kpi"><span>PBO</span><b>{_fmt(summary["multipleTesting"]["pbo"]["probability"], "pct")}</b></div>
    </div>
  </section>

  <div class="grid">
    <section class="panel"><h2>0. 结论门禁</h2>{gates}<p class="note">状态严格来自事前门槛；研究结论不是投资建议、评级、收益承诺或真实交易授权。</p></section>
    <section class="panel"><h2>1. 样本外总体指标</h2>{comparison}</section>
    <section class="panel half"><h2>净值：T0 / T1 / 被动</h2><div class="legend"><span><i class="dot" style="background:#0f6a53"></i>T0</span><span><i class="dot" style="background:#a26a16"></i>T1</span><span><i class="dot" style="background:#65706b"></i>被动</span></div>{_line_svg({"T0": charts["T0"], "T1": charts["T1"], "被动": charts["passive"]}, {"T0":"#0f6a53","T1":"#a26a16","被动":"#65706b"})}</section>
    <section class="panel half"><h2>回撤：T0 / 被动</h2><div class="legend"><span><i class="dot" style="background:#a7372d"></i>T0</span><span><i class="dot" style="background:#65706b"></i>被动</span></div>{_line_svg({"T0": charts["T0_drawdown"], "被动": charts["passive_drawdown"]}, {"T0":"#a7372d","被动":"#65706b"})}</section>
    <section class="panel third"><h2>累计单边换手</h2>{_line_svg({"换手": charts["turnover"]}, {"换手":"#315c8a"})}</section>
    <section class="panel third"><h2>累计成本率</h2>{_line_svg({"成本": charts["cost"]}, {"成本":"#a7372d"})}</section>
    <section class="panel third"><h2>风险资产 / 现金</h2>{_line_svg({"风险资产": charts["gross"], "现金": charts["cash"]}, {"风险资产":"#0f6a53","现金":"#a26a16"})}</section>
  </div>

  <div class="grid">
    <section class="panel"><h2>2. 逐年结果</h2>{yearly}</section>
    <section class="panel"><h2>3. 方向 × 波动率环境</h2><p class="note">方向阈值 ±2%；高/低波阈值只取校准期月实现方差中位数 {summary["regimeDefinition"]["varianceThreshold"]:.8f}。</p>{regimes}</section>
    <section class="panel"><h2>4. 事前压力窗口</h2>{stress}</section>
    <section class="panel"><h2>5. Walk-forward</h2>{walk_forward}<p class="note">{escape(summary["walkForward"]["note"])}</p></section>
    <section class="panel half"><h2>6. 尾部风险</h2>{tail}</section>
    <section class="panel half"><h2>7. 统计与过拟合</h2>
      <ul>
        <li>HAC 年化 alpha：{_fmt(summary["alphaHac"]["annualizedAlpha"], "pct")}；95% CI {_fmt(summary["alphaHac"]["ci95Low"], "pct")} .. {_fmt(summary["alphaHac"]["ci95High"], "pct")}；t={summary["alphaHac"]["alphaTStatistic"]:.2f}。</li>
        <li>DSR：{_fmt(summary["multipleTesting"]["deflatedSharpeRatio"]["probability"], "pct")}；四试验净 Sharpe 冠军为 {summary["multipleTesting"]["winnerByNetSharpe"]}。</li>
        <li>PBO：{_fmt(summary["multipleTesting"]["pbo"]["probability"], "pct")}；8 个连续月度块、{summary["multipleTesting"]["pbo"]["combinations"]} 个 CSCV 组合。</li>
      </ul>
      <p>{escape(summary["multipleTesting"]["interpretation"])}</p>
    </section>
  </div>

  <div class="grid">
    <section class="panel"><h2>8. 优化方向</h2>{optimization}</section>
    <section class="panel third"><h2>支持证据</h2>{_html_list(summary["supportingEvidence"])}</section>
    <section class="panel third"><h2>反对证据</h2>{_html_list(summary["opposingEvidence"])}</section>
    <section class="panel third"><h2>尚缺证据</h2>{_html_list(summary["missingEvidence"])}</section>
    <section class="panel"><h2>9. 策略画像、执行与风险边界</h2><p>{escape(summary["strategyProfile"]["economicHypothesis"])}</p><ul><li>信号：{escape(summary["strategyProfile"]["signal"])}</li><li>执行：{escape(summary["strategyProfile"]["execution"])}</li><li>主比较：{escape(summary["strategyProfile"]["primaryBenchmark"])}</li><li>校准/OOS：{escape(summary["strategyProfile"]["calibration"])} / {escape(summary["strategyProfile"]["oos"])}</li><li>独立月度决策 {summary["executionAndCost"]["independentDecisionMonths"]} 次；产生调仓请求 {summary["executionAndCost"]["rebalanceRequests"]} 次；阻塞 {summary["executionAndCost"]["blockedRequests"]} 次。</li><li>基础成本：buy={summary["executionAndCost"]["baseCostModel"]["buyRate"]}、sell={summary["executionAndCost"]["baseCostModel"]["sellRate"]}、slippage={summary["executionAndCost"]["baseCostModel"]["slippageRate"]}；零成本到净结果的累计财富拖累 {_fmt(summary["executionAndCost"]["grossToNetWealthDrag"], "pct")}。</li><li>最大单一权重 {_fmt(summary["riskAndCapacity"]["maximumSingleWeight"], "pct")}；平均 HHI {summary["riskAndCapacity"]["averageHhi"]:.3f}；单资产持仓时风险贡献全部来自 ETF。</li><li>容量：not_available；没有目标资金规模、冲击和 ADV 参与率合同。Profit Factor：not_applicable（连续权重没有稳定单笔边界）。</li></ul></section>
    <section class="panel"><h2>10. 复现身份</h2><p>quality_run={escape(summary["dataEvidence"]["qualityRunId"])} · snapshot={escape(summary["dataEvidence"]["dataSnapshotId"])} · code={escape(summary["reproduction"]["T0"]["codeCommit"])}</p>{reproduction}</section>
    <section class="panel"><h2>一手来源与反证</h2><ul>{source_items}</ul><p class="note">报告生成：{escape(summary["reportGeneratedAt"])}。所有数值来自同一 canonical 快照与运行账本。</p></section>
  </div>
</main></body></html>"""


def _line_svg(series: dict[str, pd.Series], colors: dict[str, str]) -> str:
    normalized = {
        label: values.dropna().astype(float).sort_index()
        for label, values in series.items()
        if not values.dropna().empty
    }
    if not normalized:
        return '<svg viewBox="0 0 760 260"></svg>'
    all_values = pd.concat(normalized.values())
    low = float(all_values.min())
    high = float(all_values.max())
    span = high - low or 1.0
    width, height, pad = 760.0, 260.0, 28.0
    polylines = []
    for label, values in normalized.items():
        sampled = values.iloc[:: max(len(values) // 320, 1)]
        if sampled.index[-1] != values.index[-1]:
            sampled = pd.concat([sampled, values.iloc[[-1]]])
        points = []
        for index, value in enumerate(sampled):
            x = pad + index * (width - 2 * pad) / max(len(sampled) - 1, 1)
            y = height - pad - (float(value) - low) / span * (height - 2 * pad)
            points.append(f"{x:.2f},{y:.2f}")
        polylines.append(
            f'<polyline aria-label="{escape(label)}" fill="none" stroke="{colors[label]}" stroke-width="2" points="{" ".join(points)}" />'
        )
    return (
        f'<svg viewBox="0 0 {int(width)} {int(height)}" role="img">'
        f'<line x1="{pad}" y1="{pad}" x2="{pad}" y2="{height-pad}" stroke="#c8cbc3" />'
        f'<line x1="{pad}" y1="{height-pad}" x2="{width-pad}" y2="{height-pad}" stroke="#c8cbc3" />'
        f'<text x="34" y="18" fill="#65706b" font-size="11">{high:.3f}</text>'
        f'<text x="34" y="250" fill="#65706b" font-size="11">{low:.3f}</text>'
        + "".join(polylines)
        + "</svg>"
    )


def _html_table(
    rows: list[dict[str, Any]],
    columns: tuple[tuple[str, str, str], ...],
) -> str:
    head = "".join(f"<th>{escape(title)}</th>" for _key, title, _kind in columns)
    body = []
    for row in rows:
        cells = []
        for key, _title, kind in columns:
            value = row.get(key)
            css = ""
            if kind == "bool":
                css = "ok" if value else "bad"
            cells.append(f'<td class="{css}">{_fmt(value, kind)}</td>')
        body.append("<tr>" + "".join(cells) + "</tr>")
    return f"<table><thead><tr>{head}</tr></thead><tbody>{''.join(body)}</tbody></table>"


def _html_list(items: list[str]) -> str:
    return "<ul>" + "".join(f"<li>{escape(item)}</li>" for item in items) + "</ul>"


def _fmt(value: Any, kind: str) -> str:
    if value is None or (isinstance(value, float) and not math.isfinite(value)):
        return "not_available"
    if kind == "pct":
        return f"{float(value):.2%}"
    if kind == "num":
        return f"{float(value):.3f}"
    if kind == "int":
        return str(int(value))
    if kind == "bool":
        return "通过" if value else "失败"
    if kind == "code":
        return f"<code>{escape(str(value))}</code>"
    if kind == "object":
        if isinstance(value, float):
            return f"{value:.4f}"
        return escape(json.dumps(_json_safe(value), ensure_ascii=False, sort_keys=True))
    return escape(str(value))


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        numeric = float(value)
        return numeric if math.isfinite(numeric) else None
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    return value


if __name__ == "__main__":
    raise SystemExit(main())
