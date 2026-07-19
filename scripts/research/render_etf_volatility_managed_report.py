from __future__ import annotations

import argparse
from datetime import datetime
from html import escape
import json
import math
from pathlib import Path
import sys
from typing import Any

import numpy as np
import pandas as pd


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.app.quant_research.artifacts import read_canonical_csv_gz
from backend.app.quant_research.dataset import build_adjusted_price_panel
from backend.app.quant_research.metrics import summarize_performance
from backend.app.quant_research.reporting import (
    deflated_sharpe,
    hac_alpha,
    probability_backtest_overfitting,
    returns_from_initial_nav,
    summarize_nav_window,
    summarize_return_subperiod,
    tail_metrics,
)
from scripts.research.report_evidence import (
    canonical_report_timestamp,
    verify_reproduction_evidence,
)


DEFAULT_RUN_ROOT = (
    REPO_ROOT
    / "outputs"
    / "research-runs"
    / "volatility-managed-2026-07-19-final"
    / "canonical-runs"
)
DEFAULT_GATE_RUN_ROOT = (
    REPO_ROOT
    / "outputs"
    / "research-runs"
    / "low-volatility-gate-2026-07-19-final"
    / "canonical-runs"
)
DEFAULT_OUTPUT_DIR = (
    REPO_ROOT
    / "docs"
    / "research"
    / "strategy-results"
    / "etf-volatility-managed-20260713"
)
DEFAULT_REPRODUCTION_EVIDENCE = (
    REPO_ROOT
    / "docs"
    / "research"
    / "strategy-results"
    / "reproduction-evidence-20260719.json"
)
TRIAL_ORDER = ("T0", "T1", "T2", "T3", "zero_cost", "double_cost")
GATE_RUN_ORDER = ("base_cost", "double_cost")
BASE_COST = (0.00035, 0.00085, 0.001)
INITIAL_CAPITAL = 100_000.0
TRIAL_DISPLAY_NAMES = {
    "T0": "逆方差强力降风险版（T0）",
    "T1": "逆波动温和降风险版（T1）",
    "T2": "三个月方差平滑版（T2）",
    "T3": "10 个百分点调仓缓冲版（T3）",
    "zero_cost": "逆方差强力降风险版（T0）·零成本压力场景",
    "double_cost": "逆方差强力降风险版（T0）·双倍成本压力场景",
}
TRIAL_GLOSSARY = (
    {
        "id": "T0",
        "name": "逆方差强力降风险版",
        "meaning": (
            "基准试验：下月仓位与上月实现方差成反比；波动率翻倍时，"
            "未受仓位上限影响的仓位约降至四分之一。"
        ),
    },
    {
        "id": "T1",
        "name": "逆波动温和降风险版",
        "meaning": (
            "温和变体：下月仓位与上月实现波动率成反比；波动率翻倍时，"
            "未受仓位上限影响的仓位约降至二分之一。"
        ),
    },
    {
        "id": "T2",
        "name": "三个月方差平滑版",
        "meaning": "用最近三个月实现方差的均值决定仓位，减少单月波动噪声。",
    },
    {
        "id": "T3",
        "name": "10 个百分点调仓缓冲版",
        "meaning": "沿用逆方差规则，但新旧目标仓位相差不足 10 个百分点时不调仓。",
    },
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="从六个 canonical 运行生成 ETF 波动率管理研究报告。"
    )
    parser.add_argument("--run-root", type=Path, default=DEFAULT_RUN_ROOT)
    parser.add_argument("--gate-run-root", type=Path, default=DEFAULT_GATE_RUN_ROOT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument(
        "--reproduction-evidence",
        type=Path,
        default=DEFAULT_REPRODUCTION_EVIDENCE,
    )
    args = parser.parse_args(argv)

    runs = load_runs(args.run_root)
    gate_runs = load_gate_runs(args.gate_run_root)
    reproduction_audit = verify_reproduction_evidence(
        args.reproduction_evidence,
        runs,
        gate_runs,
    )
    summary, charts = build_summary(runs, gate_runs, reproduction_audit)
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


def load_gate_runs(run_root: Path) -> dict[str, dict[str, Any]]:
    root = Path(run_root).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"低波动准入 canonical run 根目录不存在：{root}")
    runs: dict[str, dict[str, Any]] = {}
    for path in sorted(item for item in root.iterdir() if item.is_dir()):
        config_path = path / "config.json"
        manifest_path = path / "manifest.json"
        metrics_path = path / "metrics.json"
        if not all(item.is_file() for item in (config_path, manifest_path, metrics_path)):
            continue
        config = json.loads(config_path.read_text(encoding="utf-8"))
        if config.get("strategyId") != "etf_low_volatility_gate":
            continue
        label = classify_gate_run(config)
        if label in runs:
            raise ValueError(f"低波动准入 canonical run 标签重复：{label}")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("runId") != path.name:
            raise ValueError(f"低波动准入 run 目录与 manifest 身份不一致：{path}")
        runs[label] = {
            "path": path,
            "config": config,
            "manifest": manifest,
            "metrics": json.loads(metrics_path.read_text(encoding="utf-8")),
        }
    missing = sorted(set(GATE_RUN_ORDER) - set(runs))
    extra = sorted(set(runs) - set(GATE_RUN_ORDER))
    if missing or extra:
        raise ValueError(f"低波动准入 canonical run 集合无效：missing={missing}, extra={extra}")
    identities = {
        (
            item["manifest"]["codeCommit"],
            item["manifest"]["dataSnapshot"]["snapshotId"],
            item["manifest"]["qualityRun"]["qualityRunId"],
        )
        for item in runs.values()
    }
    if len(identities) != 1:
        raise ValueError("低波动准入两个运行没有绑定同一代码、数据快照和质量运行")
    return runs


def classify_gate_run(config: dict[str, Any]) -> str:
    cost = tuple(
        float(config["costModel"][field])
        for field in ("buyRate", "sellRate", "slippageRate")
    )
    if cost == BASE_COST:
        label = "base_cost"
        filename = "etf_low_volatility_gate.json"
    elif cost == tuple(value * 2 for value in BASE_COST):
        label = "double_cost"
        filename = "etf_low_volatility_gate_double_cost.json"
    else:
        raise ValueError(f"低波动准入未登记的成本场景：{cost}")
    _assert_preregistered_config(config, filename, "低波动准入")
    return label


def classify_run(config: dict[str, Any]) -> str:
    features = config["featureParameters"]
    targets = config["targetWeightParameters"]
    cost = tuple(
        float(config["costModel"][field])
        for field in ("buyRate", "sellRate", "slippageRate")
    )
    if cost not in {
        BASE_COST,
        (0.0, 0.0, 0.0),
        tuple(value * 2 for value in BASE_COST),
    }:
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
        base_label = labels[identity]
    except KeyError as exc:
        raise ValueError(f"未登记的试验配置：{identity}") from exc
    if cost == (0.0, 0.0, 0.0):
        label = "zero_cost"
        if base_label != "T0":
            raise ValueError("零成本运行配置偏离事前登记")
    elif cost == tuple(value * 2 for value in BASE_COST):
        label = "double_cost"
        if base_label != "T0":
            raise ValueError("双倍成本运行配置偏离事前登记")
    else:
        label = base_label
    filenames = {
        "T0": "etf_volatility_managed_baseline.json",
        "T1": "etf_volatility_managed_inverse_volatility.json",
        "T2": "etf_volatility_managed_smoothed_variance.json",
        "T3": "etf_volatility_managed_rebalance_band.json",
    }
    expected = json.loads(
        (REPO_ROOT / "configs" / "research" / filenames[base_label]).read_text(
            encoding="utf-8"
        )
    )
    if label == "zero_cost":
        expected["costModel"] = {
            "buyRate": "0",
            "sellRate": "0",
            "slippageRate": "0",
        }
    elif label == "double_cost":
        expected["costModel"] = {
            "buyRate": "0.0007",
            "sellRate": "0.0017",
            "slippageRate": "0.002",
        }
    _assert_preregistered_payload(config, expected, f"{label} 运行")
    return label


def _assert_preregistered_config(
    config: dict[str, Any], filename: str, label: str
) -> None:
    expected = json.loads(
        (REPO_ROOT / "configs" / "research" / filename).read_text(encoding="utf-8")
    )
    _assert_preregistered_payload(config, expected, label)


def _assert_preregistered_payload(
    config: dict[str, Any], expected: dict[str, Any], label: str
) -> None:
    normalized = json.loads(json.dumps(config))
    normalized["qualityRunId"] = "__REQUIRED_BY_CLI__"
    if normalized != expected:
        raise ValueError(f"{label}配置偏离事前登记")


def build_summary(
    runs: dict[str, dict[str, Any]],
    gate_runs: dict[str, dict[str, Any]],
    reproduction_audit: dict[str, Any],
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
        initial_strategy_nav=1.0,
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
    hac = hac_alpha(returns["T0"], returns["passive"])
    dsr = deflated_sharpe(returns, ("T0", "T1", "T2", "T3"))
    pbo = probability_backtest_overfitting(
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
            f"{TRIAL_DISPLAY_NAMES['T0']} / 被动 <= 90%",
            t0_metrics["annualizedVolatility"] / passive_metrics["annualizedVolatility"],
            0.9,
            "le",
        ),
        _gate(
            "最大回撤",
            f"|{TRIAL_DISPLAY_NAMES['T0']} 最大回撤| / |被动最大回撤| <= 85%",
            abs(t0_metrics["maxDrawdown"]) / abs(passive_metrics["maxDrawdown"]),
            0.85,
            "le",
        ),
        _gate(
            "Sharpe 改善",
            f"{TRIAL_DISPLAY_NAMES['T0']} - 被动 >= 0.10",
            t0_metrics["sharpe"] - passive_metrics["sharpe"],
            0.1,
            "ge",
        ),
        _gate(
            "CAGR 保留",
            f"{TRIAL_DISPLAY_NAMES['T0']} - 被动 >= -2 个百分点",
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
        "initialCapital": INITIAL_CAPITAL,
        "researchDate": "2026-07-13",
        "reportGeneratedAt": canonical_report_timestamp(runs, gate_runs),
        "reproductionAudit": reproduction_audit,
        "conclusion": {
            "oneLine": (
                "基准逆方差强力降风险版明显降低波动并提高净 CAGR，但最大回撤和 Sharpe 改善未达到事前门槛，"
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
        "trialGlossary": [dict(item) for item in TRIAL_GLOSSARY],
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
            "marketReference": "000300.SH 只用于行情环境参考，不冒充总收益基准",
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
            "offlineReproduction": (
                f"本报告 {reproduction_audit['runCount']} 个运行均在镜像 "
                f"{reproduction_audit['imageDigest']} 的 --network none 容器中连续"
                f"{reproduction_audit['matchesPerRun']}次匹配 result fingerprint；"
                f"审计输入为 {reproduction_audit['evidenceFile']}。"
            ),
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
                "canonical runner 与报告都以同一冻结 ETF 的因果复权价格作为窗口主基准；"
                "000300.SH 只用于行情环境划分。"
            ),
        },
        "multipleTesting": {
            "registeredTrials": 4,
            "winnerByNetSharpe": dsr["winner"],
            "winnerDisplayName": TRIAL_DISPLAY_NAMES[dsr["winner"]],
            "deflatedSharpeRatio": dsr,
            "pbo": pbo,
            "interpretation": (
                "DSR 未达到常用 95% 置信水平，PBO 较高；预登记未把它们设为硬阈值，"
                "但足以否定把同一 OOS 上的优胜变体直接升级为研究通过。"
            ),
        },
        "supportingEvidence": [
            "30/30 数据质量规则通过，canonical 快照、执行账本和离线复现完整。",
            f"逆方差强力降风险版年化波动 {t0_metrics['annualizedVolatility']:.2%}，低于被动 {passive_metrics['annualizedVolatility']:.2%}。",
            f"逆方差强力降风险版净 CAGR {t0_metrics['annualizedReturn']:.2%}，高于被动 {passive_metrics['annualizedReturn']:.2%}。",
            f"双倍成本 Sharpe {double_metrics['sharpe']:.3f}，仍高于被动 {passive_metrics['sharpe']:.3f}。",
        ],
        "opposingEvidence": [
            f"逆方差强力降风险版最大回撤 {t0_metrics['maxDrawdown']:.2%}，远未达到相对被动缩减 15% 的门槛。",
            f"逆方差强力降风险版 Sharpe 改善 {t0_metrics['sharpe'] - passive_metrics['sharpe']:.3f}，低于 0.10 门槛。",
            f"HAC 年化 alpha {hac['annualizedAlpha']:.2%}，95% 区间 {hac['ci95Low']:.2%}..{hac['ci95High']:.2%}，不能排除 0。",
            f"PBO {pbo['probability']:.1%}，四个变体中的样本内赢家经常在对应样本外落到后半区。",
            f"{largest_year['year']} 单年主动对数财富贡献超过整段净主动对数财富，稳定性门禁失败。",
        ],
        "missingEvidence": [
            "未绑定可投资现金收益率，现金收益按 0。",
            "未绑定目标资金规模、ETF 申赎/买卖冲击和 ADV 参与率，容量为 not_available。",
            "流动性环境阈值未在看 OOS 前预登记，本轮不做事后分组。",
            "只验证一只中国宽基 ETF；三个变体与基准逆方差版共用 OOS，没有新的独立验证集。",
        ],
        "optimizationDirections": [
            {
                "priority": 1,
                "direction": "把逆波动温和降风险版（T1）仅作为待证伪的新研究假设，在新时间段或另一只事前指定宽基 ETF 上独立验证。",
                "evidence": (
                    f"逆波动温和降风险版 CAGR {runs['T1']['metrics']['annualizedReturn']:.2%}、最大回撤 {runs['T1']['metrics']['maxDrawdown']:.2%}，"
                    f"成本约为基准逆方差版的 {runs['T1']['metrics']['cumulativeTransactionCostRate'] / t0_metrics['cumulativeTransactionCostRate']:.1%}；"
                    "但 Sharpe 低于基准逆方差版，当前不足以形成研究候选。"
                ),
            },
            {
                "priority": 2,
                "direction": "若目标优先压回撤，下一轮只测试一个更低风险预算或更低最大暴露，不与预测器改动同时搜索。",
                "evidence": "基准逆方差版中位目标权重约 93%，46% 的月份触及 100% 上限；当前回撤改善不足。",
            },
            {
                "priority": 3,
                "direction": "补充 point-in-time 现金收益、明确资金规模和 ETF 冲击/ADV 约束后再谈可部署性。",
                "evidence": "当前毛到净累计财富拖累约 4.98pp，但容量和闲置现金收益都未建模。",
            },
            {
                "priority": 4,
                "direction": "拒绝三个月方差平滑版（T2）和 10 个百分点调仓缓冲版（T3）作为当前优化方向。",
                "evidence": "三个月方差平滑版回撤更差；调仓缓冲版成本仅小幅下降却损失收益，二者都没有改善核心门槛。",
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
    followup, followup_charts = _build_low_volatility_gate_followup(
        gate_runs,
        passive,
        manifest,
        returns,
        reproduction_audit,
    )
    summary["lowVolatilityGateFollowup"] = followup
    charts.update(followup_charts)
    return summary, charts


def _build_low_volatility_gate_followup(
    runs: dict[str, dict[str, Any]],
    passive: pd.DataFrame,
    original_manifest: dict[str, Any],
    original_returns: pd.DataFrame,
    reproduction_audit: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, pd.Series]]:
    base = runs["base_cost"]
    double = runs["double_cost"]
    manifest = base["manifest"]
    if (
        manifest["dataSnapshot"]["snapshotId"]
        != original_manifest["dataSnapshot"]["snapshotId"]
        or manifest["qualityRun"]["qualityRunId"]
        != original_manifest["qualityRun"]["qualityRunId"]
    ):
        raise ValueError("低波动准入复现没有复用原报告的数据快照与质量运行")

    config = base["config"]
    start = pd.Timestamp(config["startDate"])
    end = pd.Timestamp(config["endDate"])
    nav = _read_frame(
        base["path"] / "nav.csv.gz",
        dates=("trade_date", "executed_signal_date"),
        numeric=(
            "nav",
            "cash_weight",
            "gross_exposure",
            "one_way_turnover",
            "transaction_cost_rate",
        ),
    )
    nav = nav[nav["trade_date"].between(start, end)].copy()
    double_nav = _read_frame(
        double["path"] / "nav.csv.gz",
        dates=("trade_date", "executed_signal_date"),
        numeric=("nav",),
    )
    double_nav = double_nav[double_nav["trade_date"].between(start, end)].copy()
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
    returns = _aligned_returns({"T0": nav}, passive)
    double_returns = _aligned_returns({"T0": double_nav}, passive)
    passive_metrics = summarize_performance(
        passive[["trade_date", "nav"]],
        include_extended=True,
        initial_strategy_nav=1.0,
    )
    static_half = passive[["trade_date"]].copy()
    static_half["nav"] = 0.5 + 0.5 * passive["nav"]
    static_half_returns = returns_from_initial_nav(static_half["nav"])
    static_metrics = base["metrics"]["staticHalfBenchmarkMetrics"]
    base_metrics = base["metrics"]
    double_metrics = double["metrics"]

    yearly = _year_rows(returns, nav, executions)
    regimes, regime_threshold = _regime_rows(
        base["path"], returns, nav, executions, config
    )
    stress = _stress_rows(returns, nav, executions)
    walk_forward = _walk_forward_rows(base, nav, passive)
    total_active_log = math.log1p(base_metrics["totalReturn"]) - math.log1p(
        passive_metrics["totalReturn"]
    )
    largest_year = max(yearly, key=lambda row: row["activeLogWealth"])
    stability_pass = largest_year["activeLogWealth"] <= total_active_log
    trial_returns = original_returns[
        ["trade_date", "T0", "T1", "T2", "T3"]
    ].merge(
        returns[["trade_date", "T0"]].rename(columns={"T0": "low_vol_gate"}),
        on="trade_date",
        how="inner",
        validate="one_to_one",
    )
    trial_candidates = ("T0", "T1", "T2", "T3", "low_vol_gate")
    dsr = deflated_sharpe(trial_returns, trial_candidates)
    pbo = probability_backtest_overfitting(trial_returns, trial_candidates)
    gates = [
        _gate(
            "相对满仓被动回撤",
            "|低波动准入最大回撤| / |被动最大回撤| <= 85%",
            base_metrics["maxDrawdownRatioVsPassive"],
            0.85,
            "le",
        ),
        _gate(
            "相对半仓基准回撤",
            "|低波动准入最大回撤| / |50% ETF + 50% 现金最大回撤| <= 100%",
            base_metrics["maxDrawdownRatioVsStaticHalf"],
            1.0,
            "le",
        ),
        _gate(
            "相对半仓基准 Sharpe",
            "低波动准入 - 半仓基准 >= 0.05",
            base_metrics["sharpeImprovementVsStaticHalf"],
            0.05,
            "ge",
        ),
        _gate(
            "相对半仓基准 CAGR",
            "低波动准入 - 半仓基准 >= -1 个百分点",
            base_metrics["annualizedReturn"] - static_metrics["annualizedReturn"],
            -0.01,
            "ge",
        ),
        _gate(
            "双倍成本",
            "双倍成本 Sharpe - 半仓基准 >= 0",
            double_metrics["sharpe"] - static_metrics["sharpe"],
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
    status = "有条件候选" if all(row["passed"] for row in gates) else "不通过"

    drawdown = nav[["trade_date", "nav"]].copy()
    drawdown["peak_nav"] = drawdown["nav"].cummax()
    drawdown["drawdown"] = drawdown["nav"] / drawdown["peak_nav"] - 1
    trough = drawdown.loc[drawdown["drawdown"].idxmin()]
    before_trough = drawdown[drawdown["trade_date"].le(trough["trade_date"])]
    peak = before_trough.loc[before_trough["nav"].idxmax()]
    recovered = drawdown[
        drawdown["trade_date"].gt(trough["trade_date"])
        & drawdown["nav"].ge(peak["nav"])
    ]
    recovery_date = (
        None if recovered.empty else recovered.iloc[0]["trade_date"].date().isoformat()
    )

    comparison = [
        _followup_comparison_row(
            "100% 被动持有 ETF",
            passive_metrics,
            returns["passive"],
            passive_metrics,
            average_exposure=1.0,
        ),
        _followup_comparison_row(
            "50% ETF + 50% 现金持有基准",
            static_metrics,
            static_half_returns,
            passive_metrics,
            average_exposure=0.5,
        ),
        _followup_comparison_row(
            "沪深300 ETF 低波动准入策略",
            base_metrics,
            returns["T0"],
            passive_metrics,
            average_exposure=base_metrics["averageTargetWeight"],
        ),
        _followup_comparison_row(
            "低波动准入策略·双倍成本",
            double_metrics,
            double_returns["T0"],
            passive_metrics,
            average_exposure=double_metrics["averageTargetWeight"],
        ),
    ]
    followup = {
        "status": status,
        "initialCapital": INITIAL_CAPITAL,
        "reproductionAudit": reproduction_audit,
        "researchClassification": (
            "事后探索；同一 OOS 结论上限为有条件候选"
            if status == "有条件候选"
            else "事后探索；同一 OOS 门禁失败，只保留为待证伪的新研究假设"
        ),
        "strategyName": "沪深300 ETF 低波动准入策略",
        "rule": (
            "月末 ETF 实现方差不高于 2012-06..2017-12 校准期中位数时，"
            "下月 100% 持有 ETF；高于门槛时 100% 现金；下一开市日开盘执行。"
        ),
        "oneLine": (
            f"低波动准入没有改善回撤：基础成本最大回撤 {base_metrics['maxDrawdown']:.2%}，"
            f"比 100% 被动持有的 {passive_metrics['maxDrawdown']:.2%} 和 50% ETF / 50% 现金的 "
            f"{static_metrics['maxDrawdown']:.2%} 都更差。"
        ),
        "interpretation": (
            "该门槛确实在 2018 年高波动下跌中减少损失，但在 2022 年低波慢跌中继续持仓，"
            "2023 年更是全年 100% 暴露；它筛选的是上一月波动，不是下一月下跌方向。"
        ),
        "admissionVarianceThreshold": base_metrics["admissionVarianceThreshold"],
        "riskOnMonths": base_metrics["riskOnMonthCount"],
        "riskOffMonths": base_metrics["riskOffMonthCount"],
        "riskOnRate": base_metrics["riskOnRate"],
        "cumulativeTransactionCostRate": base_metrics[
            "cumulativeTransactionCostRate"
        ],
        "doubleCostRate": double_metrics["cumulativeTransactionCostRate"],
        "gates": gates,
        "comparison": comparison,
        "yearly": yearly,
        "regimes": regimes,
        "regimeVarianceThreshold": regime_threshold,
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
        },
        "multipleTesting": {
            "trialCount": len(trial_candidates),
            "trialDefinition": (
                "原始报告的四个波动率管理变体，加上同一 OOS 上追加的低波动准入规则；"
                "零成本和双倍成本只作为压力场景，不重复计作策略试验。"
            ),
            "winnerByNetSharpe": dsr["winner"],
            "winnerDisplayName": (
                "沪深300 ETF 低波动准入策略"
                if dsr["winner"] == "low_vol_gate"
                else next(
                    item["name"]
                    for item in TRIAL_GLOSSARY
                    if item["id"] == dsr["winner"]
                )
            ),
            "deflatedSharpeRatio": dsr,
            "pbo": pbo,
            "interpretation": (
                "低波动准入是在同一段 OOS 上看过原始四个变体后追加的第五个研究假设；"
                "DSR/PBO 只能作为过拟合反证，不能把这段 OOS 再当成独立确认集。"
            ),
        },
        "supportingEvidence": [
            "30/30 数据质量规则通过，低波动准入与原始报告复用同一 canonical 快照，"
            f"并由 {reproduction_audit['evidenceFile']} 证明在同一镜像的断网容器中连续"
            f"{reproduction_audit['matchesPerRun']}次复现匹配。",
            f"基础成本 CAGR {base_metrics['annualizedReturn']:.2%}，高于 50% ETF / 50% 现金基准的 {static_metrics['annualizedReturn']:.2%}。",
            "2018 年高波动下跌阶段减少了绝对损失，说明波动门槛在部分冲击环境中确实降低暴露。",
        ],
        "opposingEvidence": [
            f"最大回撤 {base_metrics['maxDrawdown']:.2%}，同时差于被动 {passive_metrics['maxDrawdown']:.2%} 和半仓基准 {static_metrics['maxDrawdown']:.2%}。",
            f"相对半仓基准 Sharpe 改善 {base_metrics['sharpeImprovementVsStaticHalf']:.3f}，未达到 0.05 门槛。",
            f"五个同 OOS 研究假设的 PBO 为 {pbo['probability']:.1%}；追加规则没有独立样本外确认。",
        ],
        "missingEvidence": [
            "缺少另一只事前指定宽基 ETF 或后续新时间段的独立样本外验证。",
            "现金收益仍按 0，未纳入 point-in-time 现金利率与税费细分。",
            "未绑定目标资金规模、ADV 参与率和市场冲击模型，容量仍为 not_available。",
        ],
        "drawdownEpisode": {
            "peakDate": peak["trade_date"].date().isoformat(),
            "troughDate": trough["trade_date"].date().isoformat(),
            "recoveryDate": recovery_date,
            "maxDrawdown": float(trough["drawdown"]),
            "peakCapital": float(peak["nav"] * INITIAL_CAPITAL),
            "troughCapital": float(trough["nav"] * INITIAL_CAPITAL),
            "drawdownLoss": float((peak["nav"] - trough["nav"]) * INITIAL_CAPITAL),
        },
        "reproduction": {
            label: {
                "name": (
                    "基础成本" if label == "base_cost" else "双倍成本压力场景"
                ),
                "runId": item["manifest"]["runId"],
                "codeCommit": item["manifest"]["codeCommit"],
                "dataSnapshotId": item["manifest"]["dataSnapshot"]["snapshotId"],
                "reproducibilityKey": item["manifest"]["reproducibilityKey"],
                "resultFingerprint": item["manifest"]["resultFingerprint"],
            }
            for label, item in runs.items()
        },
    }
    charts = {
        "gate_nav": nav.set_index("trade_date")["nav"],
        "gate_static_half_nav": static_half.set_index("trade_date")["nav"],
        "gate_passive_nav": passive.set_index("trade_date")["nav"],
        "gate_drawdown": drawdown.set_index("trade_date")["drawdown"],
        "gate_static_half_drawdown": (
            static_half.set_index("trade_date")["nav"]
            / static_half.set_index("trade_date")["nav"].cummax()
            - 1
        ),
        "gate_passive_drawdown": (
            passive.set_index("trade_date")["nav"]
            / passive.set_index("trade_date")["nav"].cummax()
            - 1
        ),
        "gate_exposure": nav.set_index("trade_date")["gross_exposure"],
        "gate_turnover": nav.set_index("trade_date")["one_way_turnover"].cumsum(),
        "gate_cost": nav.set_index("trade_date")["transaction_cost_rate"].cumsum(),
    }
    return followup, charts


def _followup_comparison_row(
    label: str,
    metrics: dict[str, Any],
    returns: pd.Series,
    passive_metrics: dict[str, Any],
    *,
    average_exposure: float,
) -> dict[str, Any]:
    tail = tail_metrics(returns)
    return {
        "label": label,
        "initialCapital": INITIAL_CAPITAL,
        "finalCapital": INITIAL_CAPITAL * (1 + metrics["totalReturn"]),
        "profitAndLoss": INITIAL_CAPITAL * metrics["totalReturn"],
        "totalReturn": metrics["totalReturn"],
        "relativeWealth": (
            (1 + metrics["totalReturn"]) / (1 + passive_metrics["totalReturn"]) - 1
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
        "cost": metrics.get("cumulativeTransactionCostRate"),
        "averageExposure": average_exposure,
    }


def _comparison_rows(
    runs: dict[str, dict[str, Any]],
    passive_metrics: dict[str, Any],
    returns: pd.DataFrame,
) -> list[dict[str, Any]]:
    labels = {
        "passive": "被动 ETF",
        **TRIAL_DISPLAY_NAMES,
    }
    rows: list[dict[str, Any]] = []
    for label in ("passive", *TRIAL_ORDER):
        metrics = passive_metrics if label == "passive" else runs[label]["metrics"]
        tail = tail_metrics(returns["passive" if label == "passive" else label])
        rows.append(
            {
                "label": labels[label],
                "initialCapital": INITIAL_CAPITAL,
                "finalCapital": INITIAL_CAPITAL * (1 + metrics["totalReturn"]),
                "profitAndLoss": INITIAL_CAPITAL * metrics["totalReturn"],
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
        persisted = canonical.loc[window.window_id]
        if not math.isclose(
            strategy_metrics["totalReturn"],
            float(persisted["total_return"]),
            rel_tol=0,
            abs_tol=1e-12,
        ):
            raise ValueError(f"walk-forward 策略指标未与 canonical 工件闭合：{window.window_id}")
        if not math.isclose(
            strategy_metrics["benchmarkTotalReturn"],
            float(persisted["benchmark_total_return"]),
            rel_tol=0,
            abs_tol=1e-12,
        ):
            raise ValueError(f"walk-forward 基准指标未与 canonical 工件闭合：{window.window_id}")
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
        **{label: TRIAL_DISPLAY_NAMES[label] for label in ("T0", "T1", "T2", "T3")},
    }
    return [
        {"label": labels[label], **tail_metrics(returns[label])}
        for label in labels
    ]


def _selected_return_metrics(group: pd.DataFrame) -> dict[str, Any]:
    if group.empty:
        return {
            "strategyReturn": None,
            "passiveReturn": None,
            "activeReturn": None,
            "annualizedVolatility": None,
            "maxDrawdown": None,
        }
    period = summarize_return_subperiod(group["T0"], group["passive"])
    strategy_return = float(period["totalReturn"])
    passive_return = float(period["benchmarkTotalReturn"])
    return {
        "strategyReturn": strategy_return,
        "passiveReturn": passive_return,
        "activeReturn": strategy_return - passive_return,
        "annualizedVolatility": period["annualizedVolatility"],
        "maxDrawdown": period["maxDrawdown"],
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
    prices["adj_open"] = pd.to_numeric(prices["adj_open"], errors="raise")
    prices["adj_close"] = pd.to_numeric(prices["adj_close"], errors="raise")
    prices = prices[prices["trade_date"].between(start, end)].sort_values("trade_date")
    if prices.empty:
        raise ValueError("被动 ETF OOS 净值为空")
    result = prices[["trade_date", "adj_open", "adj_close"]].copy()
    result["nav"] = result["adj_close"] / result["adj_open"].iloc[0]
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
    returns["passive"] = returns_from_initial_nav(frame["passive_nav"])
    for label in navs:
        returns[label] = returns_from_initial_nav(frame[f"{label}_nav"])
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


def _comparison_table(rows: list[dict[str, Any]]) -> str:
    return _html_table(
        rows,
        (
            ("label", "方案", "text"),
            ("initialCapital", "初始本金", "money"),
            ("finalCapital", "期末资产", "money"),
            ("profitAndLoss", "累计盈亏", "money"),
            ("totalReturn", "累计收益", "pct"),
            ("relativeWealth", "相对被动财富", "pct"),
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
            ("averageExposure", "平均风险资产暴露", "pct"),
        ),
    )


def _render_low_volatility_gate_followup(
    followup: dict[str, Any],
    charts: dict[str, pd.Series],
) -> str:
    status_class = "fail" if followup["status"] == "不通过" else "pass"
    reproduction_audit = followup["reproductionAudit"]
    strategy_row = next(
        row
        for row in followup["comparison"]
        if row["label"] == "沪深300 ETF 低波动准入策略"
    )
    gate_chart = _line_svg(
        {
            "低波动准入策略": charts["gate_nav"] * followup["initialCapital"],
            "50% ETF + 50% 现金": charts["gate_static_half_nav"]
            * followup["initialCapital"],
            "100% 被动持有 ETF": charts["gate_passive_nav"]
            * followup["initialCapital"],
        },
        {
            "低波动准入策略": "#0f6a53",
            "50% ETF + 50% 现金": "#a26a16",
            "100% 被动持有 ETF": "#65706b",
        },
    )
    gate_drawdown_chart = _line_svg(
        {
            "低波动准入策略": charts["gate_drawdown"],
            "50% ETF + 50% 现金": charts["gate_static_half_drawdown"],
            "100% 被动持有 ETF": charts["gate_passive_drawdown"],
        },
        {
            "低波动准入策略": "#a7372d",
            "50% ETF + 50% 现金": "#a26a16",
            "100% 被动持有 ETF": "#65706b",
        },
    )
    exposure_chart = _line_svg(
        {
            "ETF 风险资产": charts["gate_exposure"],
            "现金": 1 - charts["gate_exposure"],
        },
        {"ETF 风险资产": "#0f6a53", "现金": "#a26a16"},
    )
    turnover_chart = _line_svg(
        {"累计单边换手": charts["gate_turnover"]},
        {"累计单边换手": "#315c8a"},
    )
    cost_chart = _line_svg(
        {"累计成本率": charts["gate_cost"]},
        {"累计成本率": "#a26a16"},
    )
    gates = _html_table(
        followup["gates"],
        (
            ("name", "验证门禁", "text"),
            ("rule", "事前规则", "text"),
            ("actual", "实际值", "object"),
            ("passed", "结果", "bool"),
        ),
    )
    yearly = _html_table(
        followup["yearly"],
        (
            ("year", "年份", "int"),
            ("observations", "交易日", "int"),
            ("strategyReturn", "低波动准入策略", "pct"),
            ("passiveReturn", "被动持有 ETF", "pct"),
            ("activeReturn", "相对被动", "pct"),
            ("maxDrawdown", "策略回撤", "pct"),
            ("cost", "成本率", "pct"),
            ("averageExposure", "平均风险资产暴露", "pct"),
        ),
    )
    regimes = _html_table(
        followup["regimes"],
        (
            ("direction", "行情方向", "text"),
            ("volatility", "波动环境", "text"),
            ("months", "月数", "int"),
            ("strategyReturn", "低波动准入策略", "pct"),
            ("passiveReturn", "被动持有 ETF", "pct"),
            ("activeReturn", "相对被动", "pct"),
            ("maxDrawdown", "策略回撤", "pct"),
            ("cost", "成本率", "pct"),
            ("averageExposure", "平均风险资产暴露", "pct"),
        ),
    )
    stress = _html_table(
        followup["stressWindows"],
        (
            ("name", "压力阶段", "text"),
            ("startDate", "开始", "text"),
            ("endDate", "结束", "text"),
            ("strategyReturn", "低波动准入策略", "pct"),
            ("passiveReturn", "被动持有 ETF", "pct"),
            ("activeReturn", "相对被动", "pct"),
            ("maxDrawdown", "策略回撤", "pct"),
            ("averageExposure", "平均风险资产暴露", "pct"),
        ),
    )
    walk_forward = _html_table(
        followup["walkForward"]["windows"],
        (
            ("windowId", "窗口", "text"),
            ("testStart", "开始", "text"),
            ("testEnd", "结束", "text"),
            ("strategyReturn", "低波动准入策略", "pct"),
            ("passiveReturn", "被动持有 ETF", "pct"),
            ("strategySharpe", "策略 Sharpe", "num"),
            ("passiveSharpe", "被动 Sharpe", "num"),
            ("strategyMaxDrawdown", "策略回撤", "pct"),
        ),
    )
    reproduction = _html_table(
        list(followup["reproduction"].values()),
        (
            ("name", "运行场景", "text"),
            ("runId", "运行 ID", "code"),
            ("reproducibilityKey", "复现键", "code"),
            ("resultFingerprint", "结果指纹", "code"),
        ),
    )
    episode = followup["drawdownEpisode"]
    recovery = episode["recoveryDate"] or "截至样本结束仍未修复"
    return f"""
  <section class="hero">
    <div class="eyebrow">本次追加验证 / 统一基准本金 {_fmt(followup["initialCapital"], "money")}</div>
    <h1>{escape(followup["strategyName"])}</h1>
    <div class="status {status_class}">{escape(followup["status"])}</div>
    <div class="lead">{escape(followup["oneLine"])}</div>
    <p><b>具体规则：</b>{escape(followup["rule"])}</p>
    <p><b>为什么结果与直觉不同：</b>{escape(followup["interpretation"])}</p>
    <div class="kpis">
      <div class="kpi"><span>基准本金</span><b>{_fmt(followup["initialCapital"], "money")}</b></div>
      <div class="kpi"><span>策略期末资产</span><b>{_fmt(strategy_row["finalCapital"], "money")}</b></div>
      <div class="kpi"><span>累计盈亏</span><b>{_fmt(strategy_row["profitAndLoss"], "money")}</b></div>
      <div class="kpi"><span>最大回撤</span><b>{_fmt(strategy_row["maxDrawdown"], "pct")}</b></div>
    </div>
  </section>

  <div class="grid">
    <section class="panel"><h2>策略规则与结论边界</h2><p>低波动不是“不会下跌”的同义词。这个规则只观察上一自然月的已实现方差，再决定下月持有 ETF 还是现金；它不能提前知道下月方向。门槛固定来自校准期，没有在样本外反复挑选。</p><ul><li>低波动月份：下一月持有 100% 沪深300 ETF。</li><li>高波动月份：下一月持有 100% 现金，现金收益按 0。</li><li>研究分类：{escape(followup["researchClassification"])}。</li><li>样本外风险开启 {followup["riskOnMonths"]} 个月、风险关闭 {followup["riskOffMonths"]} 个月；风险开启比例 {_fmt(followup["riskOnRate"], "pct")}。</li></ul></section>
    <section class="panel"><h2>统一按 100,000 元起步的总体对比</h2>{_comparison_table(followup["comparison"])}<p class="note">金额由同一条净值路径按 100,000 元同比例换算；收益率、波动率和回撤比例不会因本金改变。现金收益按 0，已计入预登记交易成本。</p></section>
    <section class="panel half"><h2>账户资产曲线（初始本金 100,000 元）</h2><div class="legend"><span><i class="dot" style="background:#0f6a53"></i>低波动准入策略</span><span><i class="dot" style="background:#a26a16"></i>50% ETF + 50% 现金</span><span><i class="dot" style="background:#65706b"></i>100% 被动持有 ETF</span></div>{gate_chart}</section>
    <section class="panel half"><h2>回撤曲线</h2><div class="legend"><span><i class="dot" style="background:#a7372d"></i>低波动准入策略</span><span><i class="dot" style="background:#a26a16"></i>50% ETF + 50% 现金</span><span><i class="dot" style="background:#65706b"></i>100% 被动持有 ETF</span></div>{gate_drawdown_chart}</section>
    <section class="panel half"><h2>风险资产与现金仓位</h2>{exposure_chart}</section>
    <section class="panel half"><h2>最深回撤发生了什么</h2><ul><li>回撤前高点：{escape(episode["peakDate"])}，账户资产 {_fmt(episode["peakCapital"], "money")}。</li><li>谷底：{escape(episode["troughDate"])}，账户资产 {_fmt(episode["troughCapital"], "money")}。</li><li>高点到谷底损失：{_fmt(-episode["drawdownLoss"], "money")}，即 {_fmt(episode["maxDrawdown"], "pct")}。</li><li>修复日期：{escape(recovery)}。</li></ul><p>核心失效机制是低波慢跌：2022 年策略亏损比被动更多，2023 年策略又保持全年 100% 风险资产暴露；月频滞后还可能在冲击后离场、错过随后的反弹。</p></section>
    <section class="panel half"><h2>累计单边换手</h2>{turnover_chart}</section>
    <section class="panel half"><h2>累计成本率</h2>{cost_chart}</section>
    <section class="panel"><h2>事前验证门禁</h2>{gates}<p class="note">门禁以最大回撤、同风险量级基准、双倍成本和环境稳定性为主；只要关键门禁失败，就不能因为某些年份表现好而判为有效。</p></section>
    <section class="panel"><h2>逐年表现</h2>{yearly}</section>
    <section class="panel"><h2>不同方向与波动环境</h2><p class="note">行情方向由沪深300指数当月收益划分；波动环境门槛固定为校准期月实现方差中位数 {followup["regimeVarianceThreshold"]:.8f}。</p>{regimes}</section>
    <section class="panel"><h2>压力阶段</h2>{stress}</section>
    <section class="panel"><h2>滚动样本外窗口</h2>{walk_forward}</section>
    <section class="panel"><h2>多重试验与过拟合</h2><ul><li>本次统一计算 {followup["multipleTesting"]["trialCount"]} 个研究假设：{escape(followup["multipleTesting"]["trialDefinition"])}</li><li>DSR：{_fmt(followup["multipleTesting"]["deflatedSharpeRatio"]["probability"], "pct")}；净 Sharpe 冠军为 {escape(followup["multipleTesting"]["winnerDisplayName"])}。</li><li>PBO：{_fmt(followup["multipleTesting"]["pbo"]["probability"], "pct")}；8 个连续月度块、{followup["multipleTesting"]["pbo"]["combinations"]} 个 CSCV 组合。</li></ul><p>{escape(followup["multipleTesting"]["interpretation"])}</p></section>
    <section class="panel third"><h2>支持证据</h2>{_html_list(followup["supportingEvidence"])}</section>
    <section class="panel third"><h2>反对证据</h2>{_html_list(followup["opposingEvidence"])}</section>
    <section class="panel third"><h2>尚缺证据</h2>{_html_list(followup["missingEvidence"])}</section>
    <section class="panel"><h2>如何继续优化</h2><ol><li>不能在同一段样本外继续搜索波动阈值，否则会把这次失败变成参数拟合。</li><li>若核心目标是压回撤，下一轮只预登记一个“低波慢跌保护”机制，并在新时间段或另一只事先指定的宽基 ETF 上验证。</li><li>月度 0% / 100% 切换累计成本率 {_fmt(followup["cumulativeTransactionCostRate"], "pct")}；可单独验证渐进仓位是否降低换手，但不得同时改门槛和信号。</li><li>任何新版本仍必须与固定比例 ETF / 现金基准比较，不能只和 100% 满仓相比。</li></ol></section>
    <section class="panel"><h2>复现身份</h2>{reproduction}<p class="note">基础成本与双倍成本运行均绑定同一数据快照；审计文件 <code>{escape(reproduction_audit["evidenceFile"])}</code> 已校验镜像 <code>{escape(reproduction_audit["imageDigest"])}</code>、禁用网络和连续 {reproduction_audit["matchesPerRun"]} 轮结果指纹。</p></section>
  </div>
"""


def render_html(summary: dict[str, Any], charts: dict[str, pd.Series]) -> str:
    status_class = "fail" if summary["status"] == "不通过" else "pass"
    followup = summary["lowVolatilityGateFollowup"]
    followup_section = _render_low_volatility_gate_followup(followup, charts)
    baseline_name = TRIAL_DISPLAY_NAMES["T0"]
    candidate_name = TRIAL_DISPLAY_NAMES["T1"]
    trial_glossary = _html_table(
        summary["trialGlossary"],
        (
            ("name", "方案名称", "text"),
            ("id", "内部编号", "code"),
            ("meaning", "具体规则", "text"),
        ),
    )
    nav_chart = _line_svg(
        {
            baseline_name: charts["T0"] * summary["initialCapital"],
            candidate_name: charts["T1"] * summary["initialCapital"],
            "被动持有 ETF": charts["passive"] * summary["initialCapital"],
        },
        {
            baseline_name: "#0f6a53",
            candidate_name: "#a26a16",
            "被动持有 ETF": "#65706b",
        },
    )
    drawdown_chart = _line_svg(
        {
            baseline_name: charts["T0_drawdown"],
            "被动持有 ETF": charts["passive_drawdown"],
        },
        {baseline_name: "#a7372d", "被动持有 ETF": "#65706b"},
    )
    comparison = _comparison_table(summary["comparison"])
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
            ("strategyReturn", "逆方差强力降风险版", "pct"),
            ("passiveReturn", "被动", "pct"),
            ("activeReturn", "主动", "pct"),
            ("maxDrawdown", "逆方差版回撤", "pct"),
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
            ("strategyReturn", "逆方差强力降风险版", "pct"),
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
            ("strategyReturn", "逆方差强力降风险版", "pct"),
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
            ("strategyReturn", "逆方差强力降风险版", "pct"),
            ("passiveReturn", "被动", "pct"),
            ("strategySharpe", "逆方差版 Sharpe", "num"),
            ("passiveSharpe", "被动 Sharpe", "num"),
            ("strategyMaxDrawdown", "逆方差版回撤", "pct"),
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
        f'<article class="action"><b>优先级 {item["priority"]} · {escape(item["direction"])}</b><p>{escape(item["evidence"])}</p></article>'
        for item in summary["optimizationDirections"]
    )
    reproduction = _html_table(
        [
            {
                "name": TRIAL_DISPLAY_NAMES[label],
                "internalId": label,
                **identity,
            }
            for label, identity in summary["reproduction"].items()
        ],
        (
            ("name", "方案名称", "text"),
            ("internalId", "内部运行编号", "code"),
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
  <title>沪深300 ETF 低波动准入策略复验</title>
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
{followup_section}

  <section class="hero appendix-hero">
    <div class="eyebrow">Research protocol / 2026-07-13 / OOS only</div>
    <h1>附录：原始 ETF 波动率管理策略复现</h1>
    <div class="status {status_class}">{escape(summary["status"])}</div>
    <div class="lead">{escape(summary["conclusion"]["oneLine"])}</div>
    <div class="kpis">
      <div class="kpi"><span>统一基准本金</span><b>{_fmt(summary["initialCapital"], "money")}</b></div>
      <div class="kpi"><span>基准逆方差版期末资产</span><b>{_fmt(summary["comparison"][1]["finalCapital"], "money")}</b></div>
      <div class="kpi"><span>基准逆方差版 / 被动年化波动</span><b>{_fmt(summary["gates"][0]["actual"], "pct")}</b></div>
      <div class="kpi"><span>基准逆方差版最大回撤</span><b>{_fmt(summary["comparison"][1]["maxDrawdown"], "pct")}</b></div>
    </div>
  </section>

  <div class="grid">
    <section class="panel"><h2>先看懂报告中的四个试验</h2><p>报告中的 <code>T0</code>–<code>T3</code> 只是连接配置、运行记录和复现身份的内部编号，不是策略名称。正文始终优先使用下面的中文名称。</p>{trial_glossary}</section>
    <section class="panel"><h2>原始策略结论门禁</h2>{gates}<p class="note">状态严格来自事前门槛；研究结论不是投资建议、评级、收益承诺或真实交易授权。</p></section>
    <section class="panel"><h2>原始策略样本外总体指标</h2>{comparison}</section>
    <section class="panel half"><h2>账户资产：强力降风险版 / 温和降风险版 / 被动持有（初始本金 100,000 元）</h2><div class="legend"><span><i class="dot" style="background:#0f6a53"></i>{escape(baseline_name)}</span><span><i class="dot" style="background:#a26a16"></i>{escape(candidate_name)}</span><span><i class="dot" style="background:#65706b"></i>被动持有 ETF</span></div>{nav_chart}</section>
    <section class="panel half"><h2>回撤：逆方差强力降风险版 / 被动持有</h2><div class="legend"><span><i class="dot" style="background:#a7372d"></i>{escape(baseline_name)}</span><span><i class="dot" style="background:#65706b"></i>被动持有 ETF</span></div>{drawdown_chart}</section>
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
        <li>DSR：{_fmt(summary["multipleTesting"]["deflatedSharpeRatio"]["probability"], "pct")}；四试验净 Sharpe 冠军为 {escape(summary["multipleTesting"]["winnerDisplayName"])}。</li>
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
    <section class="panel"><h2>10. 复现身份</h2><p>quality_run={escape(summary["dataEvidence"]["qualityRunId"])} · snapshot={escape(summary["dataEvidence"]["dataSnapshotId"])} · code={escape(summary["reproduction"]["T0"]["codeCommit"])}</p>{reproduction}<p class="note">审计文件 <code>{escape(summary["reproductionAudit"]["evidenceFile"])}</code> 已校验本报告 {summary["reproductionAudit"]["runCount"]} 个运行在镜像 <code>{escape(summary["reproductionAudit"]["imageDigest"])}</code>、禁用网络条件下连续 {summary["reproductionAudit"]["matchesPerRun"]} 轮结果指纹。</p></section>
    <section class="panel"><h2>一手来源与反证</h2><ul>{source_items}</ul><p class="note">研究日期：{escape(summary["researchDate"])}；报告对应的最新 canonical 工件生成时间：{escape(summary["reportGeneratedAt"])}。所有数值来自同一 canonical 快照与运行账本。</p></section>
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
    high_label = f"{high:,.0f}" if abs(high) >= 1_000 else f"{high:.3f}"
    low_label = f"{low:,.0f}" if abs(low) >= 1_000 else f"{low:.3f}"
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
        f'<text x="34" y="18" fill="#65706b" font-size="11">{high_label}</text>'
        f'<text x="34" y="250" fill="#65706b" font-size="11">{low_label}</text>'
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
    if kind == "money":
        numeric = float(value)
        sign = "-" if numeric < 0 else ""
        return f"{sign}¥{abs(numeric):,.0f}"
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
