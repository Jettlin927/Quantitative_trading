from __future__ import annotations

import argparse
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

from backend.app.quant_research.reporting import hac_alpha, tail_metrics


DEFAULT_RUN_ROOT = (
    REPO_ROOT
    / "outputs"
    / "research-runs"
    / "b1-trend-pullback-2026-07-13"
    / "canonical-runs"
)
DEFAULT_OUTPUT_DIR = (
    REPO_ROOT
    / "docs"
    / "research"
    / "strategy-results"
    / "a-share-b1-trend-pullback-20260713"
)
INITIAL_CAPITAL = 100_000.0
SOURCE_URL = (
    "https://touzikexue.com/strategy-backtest/"
    "20260516_162608_b1_v2_2025-01-01_to_2026-05-15_top2_min0p0i100p0o"
)
PUBLIC_RESULT = {
    "name": "原网页公布结果",
    "startDate": "2025-01-01",
    "endDate": "2026-05-15",
    "initialCapital": INITIAL_CAPITAL,
    "reportedInitialCapital": 1_000_000.0,
    "totalReturn": 1.029507,
    "annualizedReturn": 0.67995,
    "maxDrawdown": -0.075001,
    "buyCount": 190,
}
SCENARIO_ORDER = (
    "source_ideal",
    "source_realistic",
    "long_primary",
    "long_t3_off",
    "long_double_cost",
)
SCENARIO_NAMES = {
    "source_ideal": "网页机械口径对照",
    "source_realistic": "同周期现实成交",
    "long_primary": "长历史主版本",
    "long_t3_off": "页面参数一致性对照",
    "long_double_cost": "双倍成本压力",
    "benchmark_source": "同期沪深300指数",
    "benchmark_long": "长历史沪深300指数",
}
SCENARIO_EXPLANATIONS = {
    "source_ideal": "2025-01-02至2026-05-15；当日收盘成交、允许碎股、零成本、开启3日弱势退出。仅用于接近网页机械口径，不可执行。",
    "source_realistic": "同一短周期；信号后下一开市日开盘、100股整手、基础费用与10bp滑点、开启3日弱势退出。",
    "long_primary": "2012-06-26至2026-07-10；现实成交、基础成本、开启3日弱势退出。它是本报告的可信主结果。",
    "long_t3_off": "与长历史主版本完全相同，只关闭3日涨幅不足2%的弱势退出，用于审计来源参数矛盾。",
    "long_double_cost": "与长历史主版本相同，但佣金、税费近似和滑点全部加倍，用于成本压力。",
}
SCENARIO_CONFIGS = {
    "source_ideal": "a_share_b1_source_period_close_ideal.json",
    "source_realistic": "a_share_b1_source_period_realistic.json",
    "long_primary": "a_share_b1_long_history.json",
    "long_t3_off": "a_share_b1_long_history_declared_t3_off.json",
    "long_double_cost": "a_share_b1_long_history_double_cost.json",
}
REPRODUCED_RUN_IDS = {
    "211cc331-6ce7-4cbf-b6b2-342d2b3cfed5",
    "4b53b659-d837-447e-8e8c-67a8fe3e7a76",
    "a2629d76-3c59-49ff-b404-b5f343f9c49a",
    "5b277c7c-cc0e-4dc0-a7b3-ea004b052743",
    "3f180631-8043-4cab-898e-fce3b5277039",
}
STRESS_WINDOWS = (
    ("2015至2016急跌", "2015-06-12", "2016-02-29"),
    ("2018全年", "2018-01-01", "2018-12-31"),
    ("COVID冲击", "2020-01-23", "2020-04-30"),
    ("2022回撤", "2022-01-01", "2022-10-31"),
    ("2024年初", "2024-01-01", "2024-02-08"),
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="从五个 canonical 运行生成 B1 趋势回调研究报告。")
    parser.add_argument("--run-root", type=Path, default=DEFAULT_RUN_ROOT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args(argv)

    runs = load_runs(args.run_root)
    summary, charts = build_summary(runs)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    summary_path = args.output_dir / "summary.json"
    report_path = args.output_dir / "index.html"
    summary_path.write_text(
        json.dumps(_json_safe(summary), ensure_ascii=False, indent=2) + "\n",
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
            sort_keys=True,
        )
    )
    return 0


def classify_run(config: dict[str, Any]) -> str:
    if config.get("strategyId") != "a_share_b1_trend_pullback":
        raise ValueError("运行不是 a_share_b1_trend_pullback@1")
    if config.get("strategyVersion") != "1":
        raise ValueError("B1 策略版本必须为 1")
    universe = config.get("universe") or {}
    if universe.get("mode") != "industry_membership" or universe.get("sourceKey") != "801180.SI":
        raise ValueError("报告只接受预登记的房地产 point-in-time 代理池")
    execution = config.get("executionPolicy") or {}
    costs = tuple(
        float((config.get("costModel") or {}).get(key, float("nan")))
        for key in ("buyRate", "sellRate", "slippageRate")
    )
    start = config.get("startDate")
    t3_enabled = (config.get("exitParameters") or {}).get("t3WeakEnabled")
    identity = (
        start,
        execution.get("executionPrice"),
        bool(execution.get("allowFractional")),
        costs,
        t3_enabled,
    )
    mapping = {
        ("2025-01-02", "signal_close_ideal", True, (0.0, 0.0, 0.0), True): "source_ideal",
        (
            "2025-01-02",
            "next_trade_open",
            False,
            (0.00035, 0.00085, 0.001),
            True,
        ): "source_realistic",
        (
            "2012-06-26",
            "next_trade_open",
            False,
            (0.00035, 0.00085, 0.001),
            True,
        ): "long_primary",
        (
            "2012-06-26",
            "next_trade_open",
            False,
            (0.00035, 0.00085, 0.001),
            False,
        ): "long_t3_off",
        (
            "2012-06-26",
            "next_trade_open",
            False,
            (0.0007, 0.0017, 0.002),
            True,
        ): "long_double_cost",
    }
    try:
        label = mapping[identity]
    except KeyError as exc:
        raise ValueError(f"运行不属于五个预登记场景：{identity}") from exc
    expected = json.loads(
        (REPO_ROOT / "configs" / "research" / SCENARIO_CONFIGS[label]).read_text(
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
        if config.get("strategyId") != "a_share_b1_trend_pullback":
            continue
        manifest = json.loads(required[1].read_text(encoding="utf-8"))
        label = classify_run(config)
        if config.get("qualityRunId") != (manifest.get("qualityRun") or {}).get(
            "qualityRunId"
        ):
            raise ValueError(f"{SCENARIO_NAMES[label]}配置与 manifest 的质量运行不一致")
        if label in runs:
            raise ValueError(f"canonical 运行场景重复：{SCENARIO_NAMES[label]}")
        runs[label] = {
            "path": path,
            "config": config,
            "manifest": manifest,
            "metrics": json.loads(required[2].read_text(encoding="utf-8")),
        }
    missing = set(SCENARIO_ORDER) - set(runs)
    if missing:
        raise ValueError(f"缺少预登记运行场景：{sorted(missing)}")
    code_commits = {run["manifest"]["codeCommit"] for run in runs.values()}
    if len(code_commits) != 1:
        raise ValueError("五个运行没有绑定同一代码提交")
    for labels in (("source_ideal", "source_realistic"), ("long_primary", "long_t3_off", "long_double_cost")):
        snapshots = {runs[label]["manifest"]["dataSnapshot"]["snapshotId"] for label in labels}
        periods = {
            (
                runs[label]["config"]["warmupStart"],
                runs[label]["config"]["startDate"],
                runs[label]["config"]["endDate"],
            )
            for label in labels
        }
        if len(snapshots) != 1 or len(periods) != 1:
            raise ValueError("同周期场景没有绑定同一日期与数据快照")
    actual_run_ids = {run["manifest"]["runId"] for run in runs.values()}
    if actual_run_ids != REPRODUCED_RUN_IDS:
        raise ValueError("报告运行 ID 与已断网复现集合不一致")
    return runs


def build_summary(runs: dict[str, dict[str, Any]]) -> tuple[dict[str, Any], dict[str, str]]:
    navs = {label: _load_nav(run) for label, run in runs.items()}
    benchmarks = {
        "source": _load_benchmark_nav(runs["source_ideal"]),
        "long": _load_benchmark_nav(runs["long_primary"]),
    }
    for label, run in runs.items():
        _assert_canonical_metrics(run, navs[label])
    _assert_benchmark_metric(runs["source_ideal"], benchmarks["source"])
    _assert_benchmark_metric(runs["long_primary"], benchmarks["long"])

    execution = {label: _execution_stats(run, navs[label]) for label, run in runs.items()}
    comparison = {
        label: _comparison_row(label, runs[label], navs[label], benchmarks["source" if label.startswith("source_") else "long"])
        for label in SCENARIO_ORDER
    }
    source_benchmark = _benchmark_comparison("benchmark_source", benchmarks["source"])
    long_benchmark = _benchmark_comparison("benchmark_long", benchmarks["long"])

    long_returns = _aligned_returns(navs["long_primary"], benchmarks["long"])
    source_returns = _aligned_returns(navs["source_ideal"], benchmarks["source"])
    yearly = _year_rows(
        long_returns,
        navs["long_primary"],
        runs["long_primary"],
    )
    regimes, regime_threshold = _regime_rows(
        runs["long_primary"],
        long_returns,
        navs["long_primary"],
    )
    stress = _stress_rows(
        long_returns,
        navs["long_primary"],
        runs["long_primary"],
    )
    walk_forward = _walk_forward(runs["long_primary"])
    drawdown = _drawdown_info(navs["long_primary"])
    hac = hac_alpha(long_returns["strategy"], long_returns["benchmark"])
    post_publication = _period_summary(
        long_returns[long_returns["trade_date"] >= pd.Timestamp("2026-05-18")]
    )

    source_total_gap = abs(comparison["source_ideal"]["totalReturn"] - PUBLIC_RESULT["totalReturn"])
    source_drawdown_gap = abs(
        abs(comparison["source_ideal"]["maxDrawdown"])
        - abs(PUBLIC_RESULT["maxDrawdown"])
    )
    source_buy_gap_rate = abs(execution["source_ideal"]["buyRequests"] - PUBLIC_RESULT["buyCount"]) / PUBLIC_RESULT["buyCount"]
    primary = comparison["long_primary"]
    double = comparison["long_double_cost"]
    gates = [
        _gate("网页累计收益数值复现", "与公开值相差不超过2个百分点", source_total_gap, "≤ 2.00个百分点", source_total_gap <= 0.02, "百分点差"),
        _gate("网页最大回撤数值复现", "与公开值相差不超过2个百分点", source_drawdown_gap, "≤ 2.00个百分点", source_drawdown_gap <= 0.02, "百分点差"),
        _gate("网页买入次数数值复现", "与公开190笔相差不超过10%", source_buy_gap_rate, "≤ 10.00%", source_buy_gap_rate <= 0.10, "百分比差"),
        _gate("长历史净年化", "CAGR至少50%", primary["annualizedReturn"], "≥ 50.00%", primary["annualizedReturn"] >= 0.50, "百分比"),
        _gate("长历史最大回撤", "最大回撤不低于-30%", primary["maxDrawdown"], "≥ -30.00%", primary["maxDrawdown"] >= -0.30, "百分比"),
        _gate("长历史Sharpe", "Sharpe至少1.0", primary["sharpe"], "≥ 1.000", primary["sharpe"] >= 1.0, "数值"),
        _gate("长历史主动收益", "累计收益高于沪深300", primary["excessTotalReturn"], "> 0.00%", primary["excessTotalReturn"] > 0, "百分比"),
        _gate("Walk-forward正收益覆盖", "至少60%测试窗口为正", walk_forward["positiveRate"], "≥ 60.00%", walk_forward["positiveRate"] >= 0.60, "百分比"),
        _gate("Walk-forward跑赢基准", "至少50%测试窗口跑赢沪深300", walk_forward["beatBenchmarkRate"], "≥ 50.00%", walk_forward["beatBenchmarkRate"] >= 0.50, "百分比"),
        _gate("双倍成本财富", "双倍成本累计收益仍为正", double["totalReturn"], "> 0.00%", double["totalReturn"] > 0, "百分比"),
        _gate("双倍成本年化衰减", "相对基础成本下降不超过5个百分点", primary["annualizedReturn"] - double["annualizedReturn"], "≤ 5.00个百分点", primary["annualizedReturn"] - double["annualizedReturn"] <= 0.05, "百分点差"),
    ]
    status = "研究通过" if all(item["passed"] for item in gates) else "不通过"

    source_table = [
        {
            "name": PUBLIC_RESULT["name"],
            "period": f'{PUBLIC_RESULT["startDate"]} → {PUBLIC_RESULT["endDate"]}',
            "initialCapital": PUBLIC_RESULT["initialCapital"],
            "finalCapital": PUBLIC_RESULT["initialCapital"] * (1 + PUBLIC_RESULT["totalReturn"]),
            "totalReturn": PUBLIC_RESULT["totalReturn"],
            "annualizedReturn": PUBLIC_RESULT["annualizedReturn"],
            "maxDrawdown": PUBLIC_RESULT["maxDrawdown"],
            "buyCount": PUBLIC_RESULT["buyCount"],
            "execution": "公开交易记录显示收盘价、碎股；未展示费用",
        },
        _source_row("source_ideal", comparison, execution),
        _source_row("source_realistic", comparison, execution),
        {
            "name": SCENARIO_NAMES["benchmark_source"],
            "period": source_benchmark["period"],
            "initialCapital": INITIAL_CAPITAL,
            "finalCapital": source_benchmark["finalCapital"],
            "totalReturn": source_benchmark["totalReturn"],
            "annualizedReturn": source_benchmark["annualizedReturn"],
            "maxDrawdown": source_benchmark["maxDrawdown"],
            "buyCount": None,
            "execution": "指数收盘价被动基准",
        },
    ]
    long_table = [comparison[label] for label in ("long_primary", "long_t3_off", "long_double_cost")]
    long_table.append(long_benchmark)

    run_identities = []
    for label in SCENARIO_ORDER:
        manifest = runs[label]["manifest"]
        run_identities.append(
            {
                "scenario": SCENARIO_NAMES[label],
                "runId": manifest["runId"],
                "reproducibilityKey": manifest["reproducibilityKey"],
                "resultFingerprint": manifest["resultFingerprint"],
            }
        )

    manifest = runs["long_primary"]["manifest"]
    quality = manifest["qualityRun"]
    summary = {
        "schemaVersion": 1,
        "reportId": "a-share-b1-trend-pullback-20260713",
        "title": "A股 B1 趋势回调公开规则近似复现",
        "status": status,
        "researchDate": "2026-07-13",
        "initialCapital": INITIAL_CAPITAL,
        "oneSentenceConclusion": "未数值复现原网页，且可信长历史主版本把10万元降至约2.66万元；当前代理规则不值得继续调参，更不具备部署条件。",
        "period": {
            "warmupStart": runs["long_primary"]["config"]["warmupStart"],
            "formalStart": runs["long_primary"]["config"]["startDate"],
            "formalEnd": runs["long_primary"]["config"]["endDate"],
            "openDays": int(len(navs["long_primary"])),
            "calendarYears": (
                pd.Timestamp(runs["long_primary"]["config"]["endDate"])
                - pd.Timestamp(runs["long_primary"]["config"]["startDate"])
            ).days
            / 365.2425,
            "sourcePeriodStart": runs["source_ideal"]["config"]["startDate"],
            "sourcePeriodEnd": runs["source_ideal"]["config"]["endDate"],
            "yearRowsAreSubperiods": True,
        },
        "scenarioGlossary": [
            {
                "name": SCENARIO_NAMES[label],
                "config": SCENARIO_CONFIGS[label],
                "description": SCENARIO_EXPLANATIONS[label],
            }
            for label in SCENARIO_ORDER
        ],
        "publicSource": {
            **PUBLIC_RESULT,
            "url": SOURCE_URL,
            "numericReplication": "未数值复现",
            "totalReturnGap": source_total_gap,
            "maxDrawdownGap": source_drawdown_gap,
            "buyCountGapRate": source_buy_gap_rate,
            "contradictions": [
                "公开参数写明关闭3日涨幅不足2%卖出，但公开交易明细仍出现79次该原因卖出。",
                "公开交易记录使用当日收盘价和碎股，未展示费用扣除；无法视为现实可成交证据。",
                "活跃市值波段算法、完整B1因子、短长趋势线定义和全市场历史股票池没有公开。",
            ],
        },
        "sourceComparison": source_table,
        "longComparison": long_table,
        "primary": primary,
        "benchmark": long_benchmark,
        "gates": gates,
        "gateSummary": {
            "passed": sum(item["passed"] for item in gates),
            "failed": sum(not item["passed"] for item in gates),
        },
        "strategyProfile": {
            "strategyId": "a_share_b1_trend_pullback",
            "strategyVersion": "1",
            "classification": "趋势中的深度回调择股；横截面日频组合研究",
            "economicHypothesis": "中期趋势仍向上时，短期KDJ深度回调可能来自暂时性卖压；等待回调而非追涨，理论上可改善买入价格。",
            "counterparty": "潜在对手是对短期回撤被动止损、但中期趋势尚未破坏的交易者；若回调来自基本面恶化或行业信用冲击，该假设会失效。",
            "universe": "申万一级房地产801180.SI逐日历史成员；117个长周期有效代码、119条原始历史成员记录。它只是公开全市场候选池的代理。",
            "entry": "复权收盘价高于BBI、双重EMA10高于BBI、KDJ.J低于13；沪深300收盘价高于其BBI；代理分数降序取当日Top2。",
            "score": "100×(双重EMA10/BBI−1) + 20×max(13−J,0)/13 + 50×(收盘价/BBI−1)。",
            "portfolio": "新候选单票初始目标50%，已有持仓不加仓；止盈释放现金可继续买入，因此Top2不是最多只持有2只。",
            "exit": "跌破BBI、短趋势二次跌破、放量下跌或开启时的3日涨幅不足2%全退；每上涨10%卖出剩余仓位三分之一。",
            "marketGate": "只有沪深300收盘价高于自身BBI才允许新增买入。",
            "risk": "不加杠杆、不做空；没有组合回撤止损、行业分散或波动率预算。",
            "failureModes": [
                "房地产等行业自身进入长期下行，而沪深300市场门仍间歇开启。",
                "日频Top2反复触发并快速退出，交易成本吞噬纸面优势。",
                "深度回调并非暂时卖压，而是趋势反转、信用风险或退市风险。",
                "来源完整B1分数依赖未公开因子，本地代理排序与原策略显著不同。",
            ],
        },
        "dataEvidence": {
            "qualityRunId": quality["qualityRunId"],
            "qualityStatus": quality["status"],
            "passedRules": quality["summary"]["passedCount"],
            "failedRules": quality["summary"]["failedCount"],
            "warnings": quality["summary"]["warnings"],
            "snapshotId": manifest["dataSnapshot"]["snapshotId"],
            "rowCounts": manifest["dataSnapshot"]["rowCounts"],
            "universeMemberCount": quality["config"]["universeMemberCount"],
            "universeUniqueMemberCount": quality["config"]["universeUniqueMemberCount"],
            "pointInTime": True,
            "inputs": [
                "股票原始OHLCV与复权因子",
                "申万行业历史成员有效期",
                "上市/退市边界",
                "停复牌与涨跌停",
                "SSE交易日历",
                "沪深300指数日线",
            ],
            "limitations": [
                "缺少独立公司行动登记表，复权因子大跳变只能列为warning。",
                "全局涨跌停表含研究域外历史代码；研究切片内自然键和覆盖度已通过。",
                "历史ST状态不可用；无法按当日ST涨跌停制度做更细分类。",
            ],
        },
        "execution": execution,
        "yearly": yearly,
        "regimeDefinition": {
            "status": "事后描述，不用于门禁",
            "direction": "沪深300月收益高于+2%为上涨、低于-2%为下跌，其余为震荡。",
            "volatility": "月内日收益实现方差；阈值取2012-06至2016-12参考段中位数。",
            "varianceThreshold": regime_threshold,
            "warning": "事前登记没有冻结方向和波动阈值，因此环境矩阵只能解释失效位置，不能证明样本外稳定性。",
        },
        "regimes": regimes,
        "stressWindows": {
            "status": "客观事件窗口的事后描述，不用于门禁",
            "rows": stress,
        },
        "walkForward": walk_forward,
        "postPublication": {
            "startDate": "2026-05-18",
            "endDate": runs["long_primary"]["config"]["endDate"],
            "evidenceStatus": "证据不足：来源发布日期后的真正前瞻段不足两个月",
            **post_publication,
        },
        "risk": {
            "drawdown": drawdown,
            "tail": {
                "strategy": tail_metrics(long_returns["strategy"]),
                "benchmark": tail_metrics(long_returns["benchmark"]),
            },
            "hacAlpha": hac,
            "averageHhi": runs["long_primary"]["metrics"]["averageHhi"],
            "maxHhi": runs["long_primary"]["metrics"]["maxHhi"],
            "maxSingleWeight": runs["long_primary"]["metrics"]["maxSingleWeight"],
            "averageHoldingCount": runs["long_primary"]["metrics"]["averageHoldingCount"],
            "maxHoldingCount": runs["long_primary"]["metrics"]["maxHoldingCount"],
            "advParticipation": "暂不可评估：正式运行没有目标资金规模和冲击模型。",
            "profitFactor": "暂不可计算：账本没有稳定的逐笔已实现盈亏边界，不能从调仓权重伪造单笔交易。",
        },
        "overfitting": {
            "localTrialCount": 1,
            "auditScenarioCount": 5,
            "dsr": "不适用：本地代理规则在结果前固定，没有参数网格挑冠军。",
            "pbo": "不适用：五个场景是成交、来源矛盾与成本审计，不是五个候选冠军。",
            "sourceRisk": "无法判断：原网页没有披露候选因子、参数和回测筛选次数，无法排除来源侧过拟合。",
        },
        "supportingEvidence": [
            "数据质量、point-in-time历史成员、可交易性、执行账本和五份断网复现全部闭合。",
            "网页机械口径对照在同周期仍取得17.45%累计收益，说明固定代理规则并非完全没有捕捉到短期机会。",
            "11个walk-forward测试窗中前两个为正且跑赢基准，表明2014至2016阶段曾出现有效期。",
        ],
        "opposingEvidence": [
            "网页机械口径只取得17.45%，比公开102.95%低85.50个百分点；最大回撤也从公开7.50%扩大到22.49%。",
            "同周期现实成交仅盈利1.33%，最大回撤23.65%，纸面优势几乎被下一日开盘、整手、阻塞和成本消耗。",
            "长历史主版本累计亏损73.35%、年化-9.31%、最大回撤90.99%，同时大幅落后沪深300。",
            "11个walk-forward测试窗只有2个为正、3个跑赢基准；最近9个测试窗全部为负。",
            "关闭3日弱势退出和双倍成本都没有挽救结果，说明问题不只是单条卖出语义。",
        ],
        "missingEvidence": [
            "原策略完整B1因子、活跃市值波段、全市场point-in-time股票池和短长趋势线定义。",
            "来源作者的试验次数、训练/验证切分、费用和冲击成本口径。",
            "足够长的2026-05-16之后真正前瞻数据；当前不足两个月。",
            "目标资金规模下的ADV参与率、价格冲击、券商最低佣金和容量上限。",
        ],
        "optimization": [
            {
                "priority": "P0",
                "name": "先补来源忠实度，不调代理权重",
                "evidence": "同周期机械上界与公开累计收益相差85.50个百分点，差距远超执行成本能解释的范围。",
                "action": "只有拿到全市场历史候选池、完整B1分数、活跃市值波段和短长趋势线定义后才重做数值复现；在此之前停止搜索BBI、EMA、KDJ和分数权重。",
            },
            {
                "priority": "P0",
                "name": "把换手作为首要结构问题",
                "evidence": f'长历史累计单边换手约{execution["long_primary"]["turnover"]:.1f}倍，累计成本率约{execution["long_primary"]["cost"]:.1%}；同周期理想成交17.45%降到现实成交1.33%。',
                "action": "若要研究新版本，只允许事前登记一个降换手机制（例如排名滞回或买入冷却），在新的行业留出集或未来数据验证；不能在本样本挑最优缓冲。",
            },
            {
                "priority": "P1",
                "name": "区分市场趋势与行业趋势",
                "evidence": "沪深300市场门无法阻止房地产行业自身的长期下行，2021年后多个walk-forward窗口持续亏损。",
                "action": "下一轮应预登记行业相对趋势门或跨行业留一验证，确认失败来自房地产代理池还是策略机制；不得事后只保留表现最好的行业。",
            },
            {
                "priority": "P1",
                "name": "收紧组合生命周期和风险预算",
                "evidence": f'虽然每日只选Top2，实际最多同时持有{runs["long_primary"]["metrics"]["maxHoldingCount"]}只，漂移后单票最大权重{runs["long_primary"]["metrics"]["maxSingleWeight"]:.1%}，最大回撤达{abs(primary["maxDrawdown"]):.1%}。',
                "action": "独立研究最大存量持仓数、权重再归一和组合级风险预算；它们只能改善风险，不能被包装成对负alpha的收益修复。",
            },
            {
                "priority": "P1",
                "name": "补容量和真正前瞻验证",
                "evidence": "当前没有ADV参与率/冲击模型，来源发布后样本不足两个月。",
                "action": "先固定目标资金规模和冲击合同，再等待或锁定新的前瞻区间；容量与前瞻证据未完成前保持不可部署。",
            },
        ],
        "stopConditions": [
            "不在当前2012至2026样本搜索BBI、EMA、KDJ阈值、TopN、止盈档位或持有日数。",
            "不使用杠杆、做空或扩大单票仓位挽救50%年化目标。",
            "不从开启/关闭3日弱势退出中挑表现更好的一个冒充原策略。",
            "若拿不到来源完整规则，结束于‘近似复现不通过’，不继续制造精确复现叙事。",
        ],
        "quality": {
            "qualityRunId": quality["qualityRunId"],
            "snapshotId": manifest["dataSnapshot"]["snapshotId"],
            "codeCommit": manifest["codeCommit"],
            "schemaRevision": manifest["environment"]["schemaRevision"],
        },
        "runIdentities": run_identities,
        "reproduction": {
            "networkDisabled": True,
            "allMatched": True,
            "matchedRunCount": len(REPRODUCED_RUN_IDS),
        },
    }

    long_capital = {
        "长历史主版本": navs["long_primary"].set_index("trade_date")["nav"] * INITIAL_CAPITAL,
        "页面参数一致性对照": navs["long_t3_off"].set_index("trade_date")["nav"] * INITIAL_CAPITAL,
        "双倍成本压力": navs["long_double_cost"].set_index("trade_date")["nav"] * INITIAL_CAPITAL,
        "沪深300指数": benchmarks["long"].set_index("trade_date")["nav"] * INITIAL_CAPITAL,
    }
    source_capital = {
        "网页机械口径对照": navs["source_ideal"].set_index("trade_date")["nav"] * INITIAL_CAPITAL,
        "同周期现实成交": navs["source_realistic"].set_index("trade_date")["nav"] * INITIAL_CAPITAL,
        "沪深300指数": benchmarks["source"].set_index("trade_date")["nav"] * INITIAL_CAPITAL,
    }
    long_drawdown = {name: values / values.cummax() - 1 for name, values in long_capital.items()}
    primary_nav = navs["long_primary"].set_index("trade_date")
    holding_count, hhi = _position_series(runs["long_primary"], primary_nav.index)
    charts = {
        "longCapital": _line_svg(
            long_capital,
            {
                "长历史主版本": "#ff6b5f",
                "页面参数一致性对照": "#ffb454",
                "双倍成本压力": "#a776ff",
                "沪深300指数": "#65d6ff",
            },
        ),
        "sourceCapital": _line_svg(
            source_capital,
            {
                "网页机械口径对照": "#d4f76b",
                "同周期现实成交": "#ffb454",
                "沪深300指数": "#65d6ff",
            },
        ),
        "drawdown": _line_svg(
            long_drawdown,
            {
                "长历史主版本": "#ff6b5f",
                "页面参数一致性对照": "#ffb454",
                "双倍成本压力": "#a776ff",
                "沪深300指数": "#65d6ff",
            },
        ),
        "turnover": _line_svg(
            {"累计单边换手": primary_nav["one_way_turnover"].cumsum()},
            {"累计单边换手": "#d4f76b"},
        ),
        "cost": _line_svg(
            {"累计成本率": primary_nav["transaction_cost_rate"].cumsum()},
            {"累计成本率": "#ffb454"},
        ),
        "exposure": _line_svg(
            {"总风险暴露": primary_nav["gross_exposure"], "持仓数量": holding_count},
            {"总风险暴露": "#65d6ff", "持仓数量": "#d4f76b"},
        ),
        "concentration": _line_svg(
            {"持仓HHI": hhi},
            {"持仓HHI": "#a776ff"},
        ),
    }
    return summary, charts


def _read_frame(
    path: Path,
    *,
    dates: tuple[str, ...] = (),
    numeric: tuple[str, ...] = (),
) -> pd.DataFrame:
    frame = pd.read_csv(
        path,
        compression="gzip",
        dtype=str,
        keep_default_na=False,
        na_values=[r"\N"],
    )
    for column in dates:
        frame[column] = pd.to_datetime(frame[column], errors="coerce")
    for column in numeric:
        frame[column] = pd.to_numeric(frame[column], errors="raise")
    return frame


def _load_nav(run: dict[str, Any]) -> pd.DataFrame:
    nav = _read_frame(
        run["path"] / "nav.csv.gz",
        dates=("trade_date", "executed_signal_date"),
        numeric=(
            "nav",
            "cash_weight",
            "gross_exposure",
            "traded_weight",
            "one_way_turnover",
            "transaction_cost_rate",
            "unfilled_target_weight",
            "carried_valuation_count",
        ),
    )
    start = pd.Timestamp(run["config"]["startDate"])
    end = pd.Timestamp(run["config"]["endDate"])
    return nav[nav["trade_date"].between(start, end)].sort_values("trade_date").reset_index(drop=True)


def _load_benchmark_nav(run: dict[str, Any]) -> pd.DataFrame:
    bars = _read_frame(
        run["path"] / "inputs" / "index_daily_bars.csv.gz",
        dates=("trade_date",),
        numeric=("close",),
    )
    bars = bars[
        bars["ts_code"].eq(run["config"]["benchmark"])
        & bars["trade_date"].between(
            pd.Timestamp(run["config"]["startDate"]),
            pd.Timestamp(run["config"]["endDate"]),
        )
    ].sort_values("trade_date")
    if bars.empty:
        raise ValueError("沪深300基准为空")
    result = bars[["trade_date", "close"]].copy()
    result["nav"] = result["close"] / result["close"].iloc[0]
    return result[["trade_date", "nav"]].reset_index(drop=True)


def _assert_canonical_metrics(run: dict[str, Any], nav: pd.DataFrame) -> None:
    metrics = _summarize_nav(nav)
    for key in (
        "totalReturn",
        "annualizedReturn",
        "annualizedVolatility",
        "sharpe",
        "maxDrawdown",
        "downsideVolatility",
        "sortino",
        "maxDrawdownDuration",
        "calmar",
    ):
        if not math.isclose(
            float(metrics[key]),
            float(run["metrics"][key]),
            rel_tol=0,
            abs_tol=1e-12,
        ):
            raise ValueError(f'{SCENARIO_NAMES[classify_run(run["config"])]} canonical指标未闭合：{key}')


def _assert_benchmark_metric(run: dict[str, Any], nav: pd.DataFrame) -> None:
    total_return = float(nav["nav"].iloc[-1] / nav["nav"].iloc[0] - 1)
    if not math.isclose(total_return, float(run["metrics"]["benchmarkTotalReturn"]), rel_tol=0, abs_tol=1e-12):
        raise ValueError("沪深300基准累计收益未与 canonical 指标闭合")


def _summarize_nav(nav: pd.DataFrame) -> dict[str, Any]:
    values = nav["nav"].astype(float)
    returns = values.pct_change(fill_method=None).dropna()
    total_return = float(values.iloc[-1] / values.iloc[0] - 1)
    annualized_return = float((values.iloc[-1] / values.iloc[0]) ** (252 / (len(values) - 1)) - 1)
    volatility = float(returns.std(ddof=1) * math.sqrt(252))
    sharpe = float(returns.mean() * 252 / volatility)
    downside = returns.clip(upper=0)
    downside_volatility = float(math.sqrt(float((downside**2).mean())) * math.sqrt(252))
    drawdown = values / values.cummax() - 1
    max_drawdown = float(drawdown.min())
    return {
        "totalReturn": total_return,
        "annualizedReturn": annualized_return,
        "annualizedVolatility": volatility,
        "sharpe": sharpe,
        "downsideVolatility": downside_volatility,
        "sortino": float(returns.mean() * 252 / downside_volatility),
        "maxDrawdown": max_drawdown,
        "maxDrawdownDuration": _max_drawdown_duration(drawdown),
        "calmar": annualized_return / abs(max_drawdown),
        "positiveDayRate": float((returns > 0).mean()),
    }


def _max_drawdown_duration(drawdown: pd.Series) -> int:
    longest = 0
    current = 0
    for value in drawdown:
        current = current + 1 if float(value) < -1e-15 else 0
        longest = max(longest, current)
    return longest


def _execution_stats(run: dict[str, Any], nav: pd.DataFrame) -> dict[str, Any]:
    targets = _read_frame(
        run["path"] / "targets.csv.gz",
        dates=("signal_date", "available_date"),
        numeric=("target_weight",),
    )
    requests = _read_frame(
        run["path"] / "rebalance_requests.csv.gz",
        dates=("execution_date", "signal_date"),
        numeric=("requested_change",),
    )
    executions = _read_frame(
        run["path"] / "rebalance_executions.csv.gz",
        dates=("execution_date", "signal_date"),
        numeric=("requested_change", "executed_change", "blocked_change", "transaction_cost_rate"),
    )
    merged = executions.merge(
        requests[["execution_date", "signal_date", "ts_code", "side"]],
        on=["execution_date", "signal_date", "ts_code"],
        how="left",
        validate="one_to_one",
    )
    reason_counts = {
        str(key): int(value)
        for key, value in merged["reason"].fillna("filled").replace("", "filled").value_counts().items()
    }
    return {
        "targetSelections": int(len(targets)),
        "signalDays": int(targets["signal_date"].nunique()),
        "requests": int(len(requests)),
        "filled": int(merged["status"].eq("filled").sum()),
        "partial": int(merged["status"].eq("partial").sum()),
        "blocked": int(merged["status"].eq("blocked").sum()),
        "blockedRate": float(merged["status"].eq("blocked").mean()) if len(merged) else 0.0,
        "partialRate": float(merged["status"].eq("partial").mean()) if len(merged) else 0.0,
        "buyRequests": int(merged["side"].eq("buy").sum()),
        "sellRequests": int(merged["side"].eq("sell").sum()),
        "executedBuys": int((merged["side"].eq("buy") & merged["executed_change"].gt(1e-12)).sum()),
        "executedSells": int((merged["side"].eq("sell") & merged["executed_change"].gt(1e-12)).sum()),
        "uniqueBoughtSymbols": int(merged.loc[merged["side"].eq("buy") & merged["executed_change"].gt(1e-12), "ts_code"].nunique()),
        "turnover": float(nav["one_way_turnover"].sum()),
        "cost": float(nav["transaction_cost_rate"].sum()),
        "averageExposure": float(nav["gross_exposure"].mean()),
        "reasonCounts": reason_counts,
    }


def _comparison_row(
    label: str,
    run: dict[str, Any],
    nav: pd.DataFrame,
    benchmark: pd.DataFrame,
) -> dict[str, Any]:
    metrics = run["metrics"]
    returns = _aligned_returns(nav, benchmark)
    tail = tail_metrics(returns["strategy"])
    total_return = float(metrics["totalReturn"])
    return {
        "labelKey": label,
        "name": SCENARIO_NAMES[label],
        "period": f'{metrics["startDate"]} → {metrics["endDate"]}',
        "initialCapital": INITIAL_CAPITAL,
        "finalCapital": INITIAL_CAPITAL * (1 + total_return),
        "profitAndLoss": INITIAL_CAPITAL * total_return,
        "totalReturn": total_return,
        "relativeWealth": (1 + total_return) / (1 + float(metrics["benchmarkTotalReturn"])) - 1,
        "annualizedReturn": float(metrics["annualizedReturn"]),
        "annualizedVolatility": float(metrics["annualizedVolatility"]),
        "sharpe": float(metrics["sharpe"]),
        "downsideVolatility": float(metrics["downsideVolatility"]),
        "sortino": float(metrics["sortino"]),
        "maxDrawdown": float(metrics["maxDrawdown"]),
        "maxDrawdownDuration": int(metrics["maxDrawdownDuration"]),
        "calmar": float(metrics["calmar"]),
        "beta": float(metrics["beta"]),
        "trackingError": float(metrics["trackingError"]),
        "informationRatio": float(metrics["informationRatio"]),
        "benchmarkTotalReturn": float(metrics["benchmarkTotalReturn"]),
        "excessTotalReturn": float(metrics["excessTotalReturn"]),
        **tail,
    }


def _benchmark_comparison(label: str, nav: pd.DataFrame) -> dict[str, Any]:
    metrics = _summarize_nav(nav)
    returns = nav["nav"].pct_change(fill_method=None).dropna()
    return {
        "labelKey": label,
        "name": SCENARIO_NAMES[label],
        "period": f'{nav["trade_date"].iloc[0].date().isoformat()} → {nav["trade_date"].iloc[-1].date().isoformat()}',
        "initialCapital": INITIAL_CAPITAL,
        "finalCapital": INITIAL_CAPITAL * (1 + metrics["totalReturn"]),
        "profitAndLoss": INITIAL_CAPITAL * metrics["totalReturn"],
        "relativeWealth": 0.0,
        **metrics,
        "beta": 1.0,
        "trackingError": 0.0,
        "informationRatio": None,
        "benchmarkTotalReturn": metrics["totalReturn"],
        "excessTotalReturn": 0.0,
        **tail_metrics(returns),
    }


def _source_row(
    label: str,
    comparison: dict[str, dict[str, Any]],
    execution: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    row = comparison[label]
    return {
        "name": row["name"],
        "period": row["period"],
        "initialCapital": row["initialCapital"],
        "finalCapital": row["finalCapital"],
        "totalReturn": row["totalReturn"],
        "annualizedReturn": row["annualizedReturn"],
        "maxDrawdown": row["maxDrawdown"],
        "buyCount": execution[label]["buyRequests"],
        "execution": (
            "当日收盘、碎股、零成本"
            if label == "source_ideal"
            else "次日开盘、100股整手、基础成本"
        ),
    }


def _aligned_returns(nav: pd.DataFrame, benchmark: pd.DataFrame) -> pd.DataFrame:
    frame = nav[["trade_date", "nav"]].merge(
        benchmark[["trade_date", "nav"]],
        on="trade_date",
        how="inner",
        suffixes=("_strategy", "_benchmark"),
        validate="one_to_one",
    )
    result = frame[["trade_date"]].copy()
    result["strategy"] = frame["nav_strategy"].pct_change(fill_method=None)
    result["benchmark"] = frame["nav_benchmark"].pct_change(fill_method=None)
    return result.dropna().reset_index(drop=True)


def _period_summary(group: pd.DataFrame) -> dict[str, Any]:
    if group.empty:
        return {
            "observations": 0,
            "strategyReturn": None,
            "benchmarkReturn": None,
            "activeReturn": None,
            "annualizedVolatility": None,
            "maxDrawdown": None,
        }
    strategy_return = float((1 + group["strategy"]).prod() - 1)
    benchmark_return = float((1 + group["benchmark"]).prod() - 1)
    wealth = (1 + group["strategy"]).cumprod()
    return {
        "observations": int(len(group)),
        "strategyReturn": strategy_return,
        "benchmarkReturn": benchmark_return,
        "activeReturn": strategy_return - benchmark_return,
        "annualizedVolatility": float(group["strategy"].std(ddof=1) * math.sqrt(252)) if len(group) > 1 else None,
        "maxDrawdown": float((wealth / wealth.cummax() - 1).min()),
    }


def _ledger_period(
    dates: pd.Series,
    nav: pd.DataFrame,
    run: dict[str, Any],
) -> dict[str, Any]:
    date_set = set(pd.to_datetime(dates))
    nav_slice = nav[nav["trade_date"].isin(date_set)]
    targets = _read_frame(run["path"] / "targets.csv.gz", dates=("signal_date",), numeric=("target_weight",))
    executions = _read_frame(
        run["path"] / "rebalance_executions.csv.gz",
        dates=("execution_date",),
        numeric=("executed_change", "blocked_change"),
    )
    target_slice = targets[targets["signal_date"].isin(date_set)]
    execution_slice = executions[executions["execution_date"].isin(date_set)]
    return {
        "targetSelections": int(len(target_slice)),
        "requestCount": int(len(execution_slice)),
        "blockedRate": float(execution_slice["status"].eq("blocked").mean()) if len(execution_slice) else None,
        "turnover": float(nav_slice["one_way_turnover"].sum()),
        "cost": float(nav_slice["transaction_cost_rate"].sum()),
        "averageExposure": float(nav_slice["gross_exposure"].mean()) if len(nav_slice) else None,
    }


def _year_rows(
    returns: pd.DataFrame,
    nav: pd.DataFrame,
    run: dict[str, Any],
) -> list[dict[str, Any]]:
    rows = []
    start_year = pd.Timestamp(run["config"]["startDate"]).year
    end_year = pd.Timestamp(run["config"]["endDate"]).year
    for year, group in returns.groupby(returns["trade_date"].dt.year, sort=True):
        rows.append(
            {
                "year": int(year),
                "coverage": "边界部分年度" if year in (start_year, end_year) else "完整自然年",
                **_period_summary(group),
                **_ledger_period(group["trade_date"], nav, run),
            }
        )
    return rows


def _regime_rows(
    run: dict[str, Any],
    returns: pd.DataFrame,
    nav: pd.DataFrame,
) -> tuple[list[dict[str, Any]], float]:
    bars = _read_frame(
        run["path"] / "inputs" / "index_daily_bars.csv.gz",
        dates=("trade_date",),
        numeric=("close",),
    )
    bars = bars[bars["trade_date"].between(pd.Timestamp(run["config"]["startDate"]), pd.Timestamp(run["config"]["endDate"]))].sort_values("trade_date")
    bars["dailyReturn"] = bars["close"].pct_change(fill_method=None)
    bars["month"] = bars["trade_date"].dt.to_period("M")
    monthly_rows = []
    previous_close: float | None = None
    for month, group in bars.groupby("month", sort=True):
        last_close = float(group["close"].iloc[-1])
        daily = group["dailyReturn"].dropna()
        monthly_rows.append(
            {
                "month": month,
                "marketReturn": None if previous_close is None else last_close / previous_close - 1,
                "variance": float(((daily - daily.mean()) ** 2).sum()),
            }
        )
        previous_close = last_close
    monthly = pd.DataFrame(monthly_rows)
    reference = monthly[monthly["month"] <= pd.Period("2016-12", freq="M")]
    threshold = float(reference["variance"].median())
    monthly["direction"] = monthly["marketReturn"].map(
        lambda value: "上涨" if pd.notna(value) and value > 0.02 else "下跌" if pd.notna(value) and value < -0.02 else "震荡"
    )
    monthly["volatility"] = monthly["variance"].map(lambda value: "高波动" if value > threshold else "低波动")
    joined = returns.copy()
    joined["month"] = joined["trade_date"].dt.to_period("M")
    joined = joined.merge(monthly[["month", "direction", "volatility"]], on="month", how="left", validate="many_to_one")
    rows = []
    for (direction, volatility), group in joined.groupby(["direction", "volatility"], sort=True):
        rows.append(
            {
                "direction": direction,
                "volatility": volatility,
                "months": int(group["month"].nunique()),
                **_period_summary(group),
                **_ledger_period(group["trade_date"], nav, run),
            }
        )
    return rows, threshold


def _stress_rows(
    returns: pd.DataFrame,
    nav: pd.DataFrame,
    run: dict[str, Any],
) -> list[dict[str, Any]]:
    rows = []
    for name, start, end in STRESS_WINDOWS:
        group = returns[returns["trade_date"].between(pd.Timestamp(start), pd.Timestamp(end))]
        rows.append(
            {
                "name": name,
                "startDate": start,
                "endDate": end,
                **_period_summary(group),
                **_ledger_period(group["trade_date"], nav, run),
            }
        )
    return rows


def _walk_forward(run: dict[str, Any]) -> dict[str, Any]:
    windows = _read_frame(
        run["path"] / "walk_forward_windows.csv.gz",
        dates=("train_start", "train_end", "test_start", "test_end"),
        numeric=("train_periods", "test_periods"),
    )
    metrics = _read_frame(
        run["path"] / "walk_forward_metrics.csv.gz",
        dates=("start_date", "end_date"),
        numeric=(
            "observations",
            "total_return",
            "annualized_return",
            "annualized_volatility",
            "sharpe",
            "max_drawdown",
            "calmar",
            "benchmark_total_return",
            "excess_total_return",
            "information_ratio",
        ),
    )
    joined = windows.merge(metrics, on="window_id", how="inner", validate="one_to_one")
    rows = [
        {
            "windowId": row.window_id,
            "trainStart": row.train_start.date().isoformat(),
            "trainEnd": row.train_end.date().isoformat(),
            "testStart": row.test_start.date().isoformat(),
            "testEnd": row.test_end.date().isoformat(),
            "strategyReturn": float(row.total_return),
            "benchmarkReturn": float(row.benchmark_total_return),
            "activeReturn": float(row.excess_total_return),
            "sharpe": float(row.sharpe),
            "maxDrawdown": float(row.max_drawdown),
        }
        for row in joined.itertuples(index=False)
    ]
    positive = sum(row["strategyReturn"] > 0 for row in rows)
    beat = sum(row["activeReturn"] > 0 for row in rows)
    return {
        "mode": "anchored 504日训练 / 252日测试 / 252日步长",
        "windowCount": len(rows),
        "positiveCount": positive,
        "positiveRate": positive / len(rows),
        "beatBenchmarkCount": beat,
        "beatBenchmarkRate": beat / len(rows),
        "warning": "窗口按时间切片，但规则来自外部网页且来源侧试验史未知，不能等同于真正独立的来源作者OOS。",
        "rows": rows,
    }


def _drawdown_info(nav: pd.DataFrame) -> dict[str, Any]:
    frame = nav[["trade_date", "nav"]].copy()
    frame["peak"] = frame["nav"].cummax()
    frame["drawdown"] = frame["nav"] / frame["peak"] - 1
    trough_index = int(frame["drawdown"].idxmin())
    trough = frame.loc[trough_index]
    prior = frame.loc[:trough_index]
    peak_index = int(prior["nav"].idxmax())
    peak = frame.loc[peak_index]
    later = frame.loc[trough_index + 1 :]
    recovered = later[later["nav"] >= float(peak["nav"])]
    recovery_date = None if recovered.empty else recovered.iloc[0]["trade_date"].date().isoformat()
    return {
        "peakDate": peak["trade_date"].date().isoformat(),
        "peakCapital": float(peak["nav"] * INITIAL_CAPITAL),
        "troughDate": trough["trade_date"].date().isoformat(),
        "troughCapital": float(trough["nav"] * INITIAL_CAPITAL),
        "lossAmount": float((trough["nav"] - peak["nav"]) * INITIAL_CAPITAL),
        "maxDrawdown": float(trough["drawdown"]),
        "recoveryDate": recovery_date,
        "recovered": recovery_date is not None,
    }


def _position_series(run: dict[str, Any], index: pd.DatetimeIndex) -> tuple[pd.Series, pd.Series]:
    positions = _read_frame(
        run["path"] / "positions.csv.gz",
        dates=("trade_date",),
        numeric=("close_weight",),
    )
    grouped = positions.groupby("trade_date")
    count = grouped.size().reindex(index, fill_value=0).astype(float)
    hhi = grouped["close_weight"].apply(lambda values: float((values**2).sum())).reindex(index, fill_value=0.0)
    return count, hhi


def _gate(
    name: str,
    rule: str,
    actual: float,
    threshold: str,
    passed: bool,
    display_kind: str,
) -> dict[str, Any]:
    return {
        "name": name,
        "rule": rule,
        "actual": float(actual),
        "actualDisplay": _display_actual(actual, display_kind),
        "threshold": threshold,
        "passed": bool(passed),
    }


def _display_actual(value: float, kind: str) -> str:
    if kind == "数值":
        return f"{value:.3f}"
    return f"{value:.2%}"


def render_html(summary: dict[str, Any], charts: dict[str, str]) -> str:
    primary = summary["primary"]
    benchmark = summary["benchmark"]
    source_table = _table(
        summary["sourceComparison"],
        (
            ("name", "方案", "text"),
            ("period", "周期", "text"),
            ("initialCapital", "初始本金", "money"),
            ("finalCapital", "期末资产", "money"),
            ("totalReturn", "累计收益", "pct"),
            ("annualizedReturn", "年化", "pct"),
            ("maxDrawdown", "最大回撤", "pct"),
            ("buyCount", "买入请求", "int"),
            ("execution", "成交口径", "text"),
        ),
    )
    long_table = _table(
        summary["longComparison"],
        (
            ("name", "方案", "text"),
            ("initialCapital", "初始本金", "money"),
            ("finalCapital", "期末资产", "money"),
            ("profitAndLoss", "累计盈亏", "money"),
            ("totalReturn", "累计收益", "pct"),
            ("annualizedReturn", "CAGR", "pct"),
            ("annualizedVolatility", "年化波动", "pct"),
            ("sharpe", "Sharpe", "num"),
            ("sortino", "Sortino", "num"),
            ("maxDrawdown", "最大回撤", "pct"),
            ("maxDrawdownDuration", "回撤持续日", "int"),
            ("calmar", "Calmar", "num"),
            ("es95", "日ES95", "pct"),
            ("beta", "Beta", "num"),
            ("informationRatio", "IR", "num"),
        ),
    )
    gates = _table(
        summary["gates"],
        (
            ("name", "事前门禁", "text"),
            ("rule", "规则", "text"),
            ("actualDisplay", "实际", "text"),
            ("threshold", "阈值", "text"),
            ("passed", "结果", "bool"),
        ),
    )
    yearly = _table(
        summary["yearly"],
        (
            ("year", "年份", "int"),
            ("coverage", "覆盖", "text"),
            ("observations", "收益日", "int"),
            ("strategyReturn", "策略收益", "pct"),
            ("benchmarkReturn", "沪深300", "pct"),
            ("activeReturn", "主动收益", "pct"),
            ("maxDrawdown", "年内回撤", "pct"),
            ("targetSelections", "候选数", "int"),
            ("requestCount", "请求数", "int"),
            ("turnover", "单边换手", "num"),
            ("cost", "成本率", "pct"),
            ("blockedRate", "阻塞率", "pct"),
            ("averageExposure", "平均暴露", "pct"),
        ),
    )
    regimes = _table(
        summary["regimes"],
        (
            ("direction", "方向", "text"),
            ("volatility", "波动", "text"),
            ("months", "月数", "int"),
            ("observations", "收益日", "int"),
            ("strategyReturn", "策略收益", "pct"),
            ("benchmarkReturn", "沪深300", "pct"),
            ("activeReturn", "主动收益", "pct"),
            ("maxDrawdown", "回撤", "pct"),
            ("turnover", "单边换手", "num"),
            ("cost", "成本率", "pct"),
            ("blockedRate", "阻塞率", "pct"),
            ("averageExposure", "平均暴露", "pct"),
        ),
    )
    stress = _table(
        summary["stressWindows"]["rows"],
        (
            ("name", "客观窗口", "text"),
            ("startDate", "开始", "text"),
            ("endDate", "结束", "text"),
            ("strategyReturn", "策略收益", "pct"),
            ("benchmarkReturn", "沪深300", "pct"),
            ("activeReturn", "主动收益", "pct"),
            ("maxDrawdown", "回撤", "pct"),
            ("turnover", "单边换手", "num"),
            ("cost", "成本率", "pct"),
        ),
    )
    walk_forward = _table(
        summary["walkForward"]["rows"],
        (
            ("windowId", "测试窗", "code"),
            ("trainStart", "训练起", "text"),
            ("trainEnd", "训练止", "text"),
            ("testStart", "测试起", "text"),
            ("testEnd", "测试止", "text"),
            ("strategyReturn", "策略收益", "pct"),
            ("benchmarkReturn", "沪深300", "pct"),
            ("activeReturn", "主动收益", "pct"),
            ("sharpe", "Sharpe", "num"),
            ("maxDrawdown", "回撤", "pct"),
        ),
    )
    execution = summary["execution"]["long_primary"]
    execution_table = _table(
        [
            {
                "name": SCENARIO_NAMES[label],
                **summary["execution"][label],
            }
            for label in SCENARIO_ORDER
        ],
        (
            ("name", "方案", "text"),
            ("targetSelections", "候选记录", "int"),
            ("signalDays", "信号日", "int"),
            ("requests", "请求", "int"),
            ("filled", "完全成交", "int"),
            ("partial", "部分成交", "int"),
            ("blocked", "完全阻塞", "int"),
            ("buyRequests", "买入请求", "int"),
            ("sellRequests", "卖出请求", "int"),
            ("turnover", "累计单边换手", "num"),
            ("cost", "累计成本率", "pct"),
            ("averageExposure", "平均暴露", "pct"),
        ),
    )
    reproduction = _table(
        summary["runIdentities"],
        (
            ("scenario", "方案", "text"),
            ("runId", "运行ID", "code"),
            ("reproducibilityKey", "可复现键", "code"),
            ("resultFingerprint", "结果指纹", "code"),
        ),
    )
    glossary = "".join(
        f'''<article class="protocol-card"><span class="eyebrow">方案说明</span><h3>{escape(item["name"])}</h3><p>{escape(item["description"])}</p></article>'''
        for item in summary["scenarioGlossary"]
    )
    optimization = "".join(
        f'''<article class="action"><span>{escape(item["priority"])}</span><div><h3>{escape(item["name"])}</h3><p><b>证据：</b>{escape(item["evidence"])}</p><p><b>下一次最小验证：</b>{escape(item["action"])}</p></div></article>'''
        for item in summary["optimization"]
    )
    warning_items = "".join(f"<li><code>{escape(item)}</code></li>" for item in summary["dataEvidence"]["warnings"])
    contradiction_items = "".join(f"<li>{escape(item)}</li>" for item in summary["publicSource"]["contradictions"])
    reason_items = "".join(
        f"<li><span>{escape(reason)}</span><b>{count}</b></li>"
        for reason, count in sorted(execution["reasonCounts"].items(), key=lambda item: (-item[1], item[0]))
    )
    source_url = escape(summary["publicSource"]["url"], quote=True)
    drawdown = summary["risk"]["drawdown"]
    post = summary["postPublication"]

    return f'''<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(summary["title"])}</title>
  <style>
    :root {{
      --ink:#e9efe6; --muted:#98a39b; --dim:#66736a; --base:#090d0b; --panel:#101612;
      --panel2:#151d17; --grid:#263229; --lime:#d4f76b; --cyan:#65d6ff; --amber:#ffb454;
      --red:#ff6b5f; --violet:#a776ff; --paper:#c8d0c9;
    }}
    * {{ box-sizing:border-box; }}
    html {{ scroll-behavior:smooth; background:var(--base); }}
    body {{ margin:0; overflow-x:hidden; color:var(--ink); background:
      linear-gradient(rgba(101,214,255,.035) 1px,transparent 1px),
      linear-gradient(90deg,rgba(101,214,255,.035) 1px,transparent 1px),
      radial-gradient(circle at 82% 8%,rgba(255,107,95,.11),transparent 34%), var(--base);
      background-size:32px 32px,32px 32px,auto; font-family:"IBM Plex Sans","Noto Sans SC","PingFang SC",sans-serif;
      line-height:1.65; }}
    body:before {{ content:""; position:fixed; inset:0; pointer-events:none; opacity:.11; z-index:99;
      background:repeating-linear-gradient(0deg,transparent 0 3px,rgba(255,255,255,.08) 4px); }}
    a {{ color:var(--cyan); }} code {{ color:var(--lime); font-family:"IBM Plex Mono","SFMono-Regular",monospace; font-size:.88em; overflow-wrap:anywhere; }}
    p,li {{ overflow-wrap:anywhere; }}
    main {{ width:min(1500px,calc(100% - 32px)); margin:auto; padding:24px 0 80px; }}
    .hero {{ min-height:78vh; display:grid; grid-template-columns:1.4fr .6fr; gap:20px; align-items:end; padding:8vh 0 38px; border-bottom:1px solid var(--grid); }}
    .hero>*,section,.protocol-card,.split>div,.profile-grid article,.evidence-grid article {{ min-width:0; }}
    .stamp {{ display:inline-flex; gap:10px; align-items:center; color:#140a08; background:var(--red); font-weight:900; letter-spacing:.16em; padding:9px 14px; transform:rotate(-1deg); box-shadow:8px 8px 0 #481b18; }}
    .kicker,.eyebrow {{ color:var(--lime); text-transform:uppercase; letter-spacing:.18em; font-size:.72rem; font-weight:800; }}
    h1,h2,h3 {{ font-family:"Avenir Next Condensed","DIN Condensed","Noto Sans SC",sans-serif; line-height:1.08; margin:0; }}
    h1 {{ font-size:clamp(3rem,7vw,7.2rem); letter-spacing:-.055em; max-width:1050px; margin:30px 0 20px; }}
    h2 {{ font-size:clamp(1.7rem,3vw,3rem); letter-spacing:-.025em; }} h3 {{ font-size:1.2rem; }}
    .hero-lede {{ max-width:950px; color:var(--paper); font-size:clamp(1.05rem,1.6vw,1.35rem); }}
    .hero-side {{ border-left:1px solid var(--grid); padding-left:24px; display:grid; gap:18px; }}
    .hero-side b {{ display:block; font-size:2rem; color:var(--red); line-height:1; }}
    .hero-side span {{ color:var(--muted); font-size:.82rem; }}
    .period-strip {{ grid-column:1/-1; display:flex; flex-wrap:wrap; gap:10px 22px; padding-top:28px; font-family:"IBM Plex Mono",monospace; color:var(--muted); }}
    .period-strip span {{ min-width:0; overflow-wrap:anywhere; }}
    .period-strip strong {{ color:var(--ink); }}
    section {{ margin-top:22px; border:1px solid var(--grid); background:linear-gradient(145deg,rgba(21,29,23,.96),rgba(10,15,12,.96)); box-shadow:0 24px 80px rgba(0,0,0,.25); }}
    .section-head {{ padding:22px 24px; border-bottom:1px solid var(--grid); display:flex; justify-content:space-between; gap:20px; align-items:end; }}
    .section-head p {{ margin:0; max-width:760px; color:var(--muted); }} .content {{ padding:24px; }}
    .protocols {{ display:grid; grid-template-columns:repeat(5,1fr); border-top:1px solid var(--grid); }}
    .protocol-card {{ padding:22px; border-right:1px solid var(--grid); min-height:220px; }} .protocol-card:last-child {{ border:0; }}
    .protocol-card h3 {{ margin:12px 0; }} .protocol-card p {{ color:var(--muted); font-size:.9rem; margin:0; }}
    .verdict-grid {{ display:grid; grid-template-columns:1.1fr 1.9fr; }}
    .verdict-copy {{ padding:28px; border-right:1px solid var(--grid); }} .verdict-copy .big {{ font-size:clamp(2.5rem,6vw,6rem); color:var(--red); font-weight:900; line-height:.9; margin:20px 0; }}
    .verdict-copy p {{ color:var(--paper); }}
    .kpis {{ display:grid; grid-template-columns:repeat(3,1fr); }} .kpi {{ padding:24px; border-right:1px solid var(--grid); border-bottom:1px solid var(--grid); min-height:142px; }}
    .kpi:nth-child(3n) {{ border-right:0; }} .kpi span {{ color:var(--muted); font-size:.78rem; letter-spacing:.08em; text-transform:uppercase; }}
    .kpi b {{ display:block; font-size:clamp(1.6rem,3vw,3rem); line-height:1; margin:14px 0 7px; }} .bad-number {{ color:var(--red); }} .good-number {{ color:var(--cyan); }}
    .kpi small {{ color:var(--dim); }}
    .table-wrap {{ overflow:auto; max-width:100%; }} table {{ width:100%; border-collapse:collapse; font-size:.84rem; white-space:nowrap; }}
    th {{ position:sticky; top:0; z-index:2; color:var(--lime); background:#0d130f; text-align:left; font-size:.72rem; letter-spacing:.05em; text-transform:uppercase; }}
    th,td {{ padding:11px 13px; border-bottom:1px solid var(--grid); border-right:1px solid rgba(38,50,41,.65); }} tbody tr:hover {{ background:rgba(101,214,255,.045); }}
    td.ok {{ color:var(--lime); font-weight:800; }} td.bad {{ color:var(--red); font-weight:800; }}
    .split {{ display:grid; grid-template-columns:1fr 1fr; }} .split>div {{ padding:24px; }} .split>div+div {{ border-left:1px solid var(--grid); }}
    .chart {{ min-height:310px; padding:16px 12px 6px; }} .chart svg {{ width:100%; height:auto; overflow:visible; }}
    .legend {{ display:flex; flex-wrap:wrap; gap:9px 18px; color:var(--muted); font-size:.8rem; padding:0 22px 18px; }} .legend i {{ width:18px; height:3px; display:inline-block; margin-right:7px; vertical-align:middle; }}
    .callout {{ border-left:5px solid var(--red); background:rgba(255,107,95,.07); padding:18px 20px; color:var(--paper); }}
    .note {{ color:var(--muted); font-size:.88rem; }} .warning {{ color:var(--amber); }}
    .profile-grid {{ display:grid; grid-template-columns:repeat(3,1fr); }} .profile-grid article {{ padding:22px; border-right:1px solid var(--grid); border-bottom:1px solid var(--grid); }} .profile-grid article:nth-child(3n) {{ border-right:0; }}
    .profile-grid h3 {{ color:var(--cyan); margin-bottom:10px; }} .profile-grid p,.profile-grid li {{ color:var(--muted); font-size:.9rem; }}
    .evidence-grid {{ display:grid; grid-template-columns:repeat(3,1fr); }} .evidence-grid article {{ padding:24px; border-right:1px solid var(--grid); }} .evidence-grid article:last-child {{ border:0; }}
    .evidence-grid h3 {{ margin-bottom:14px; }} .evidence-grid li {{ margin:9px 0; color:var(--muted); }}
    .action {{ display:grid; grid-template-columns:70px 1fr; gap:18px; padding:22px 0; border-bottom:1px solid var(--grid); }} .action:last-child {{ border:0; }}
    .action>span {{ color:#090d0b; background:var(--lime); width:54px; height:54px; display:grid; place-items:center; font-weight:900; border-radius:50%; }}
    .action h3 {{ color:var(--ink); margin-bottom:8px; }} .action p {{ color:var(--muted); margin:5px 0; }}
    .reason-list {{ list-style:none; padding:0; margin:0; display:grid; grid-template-columns:repeat(3,1fr); gap:8px; }} .reason-list li {{ display:flex; justify-content:space-between; padding:10px 12px; border:1px solid var(--grid); color:var(--muted); }} .reason-list b {{ color:var(--amber); }}
    .footer {{ padding:30px 0; color:var(--dim); font-family:"IBM Plex Mono",monospace; font-size:.78rem; }}
    @keyframes enter {{ from {{ opacity:0; transform:translateY(12px); }} to {{ opacity:1; transform:none; }} }}
    .hero>* {{ animation:enter .7s both; }} .hero>*:nth-child(2) {{ animation-delay:.12s; }}
    @media (max-width:1050px) {{ .hero,.verdict-grid,.split {{ grid-template-columns:1fr; }} .hero-side,.split>div+div {{ border-left:0; border-top:1px solid var(--grid); padding-left:0; }} .protocols {{ grid-template-columns:1fr 1fr; }} .profile-grid {{ grid-template-columns:1fr 1fr; }} .evidence-grid {{ grid-template-columns:1fr; }} .evidence-grid article {{ border-right:0; border-bottom:1px solid var(--grid); }} }}
    @media (max-width:680px) {{ main {{ width:min(100% - 18px,1500px); }} .hero {{ min-height:auto; padding-top:50px; }} h1 {{ font-size:3.25rem; }} .protocols,.profile-grid,.kpis {{ grid-template-columns:1fr; }} .protocol-card,.profile-grid article,.kpi {{ border-right:0; }} .reason-list {{ grid-template-columns:1fr; }} .section-head {{ display:block; }} .section-head p {{ margin-top:10px; }} }}
    @media print {{ body {{ background:#fff; color:#111; }} body:before {{ display:none; }} section {{ break-inside:avoid; box-shadow:none; }} }}
  </style>
</head>
<body><main>
  <header class="hero">
    <div>
      <span class="stamp">{escape(summary["status"])}</span>
      <p class="kicker">FORENSIC STRATEGY REVIEW · 公开规则近似复现</p>
      <h1>B1 趋势回调<br>没有复现神话</h1>
      <p class="hero-lede">{escape(summary["oneSentenceConclusion"])}</p>
    </div>
    <aside class="hero-side">
      <div><b>{_fmt(primary["annualizedReturn"], "pct")}</b><span>可信长历史净年化，事前目标 ≥ 50%</span></div>
      <div><b>{_fmt(primary["maxDrawdown"], "pct")}</b><span>最大回撤，风险预算 ≥ -30%</span></div>
      <div><b>{summary["gateSummary"]["passed"]}/{len(summary["gates"])}</b><span>事前门禁通过数</span></div>
    </aside>
    <div class="period-strip"><span>完整正式回测周期 <strong>{summary["period"]["formalStart"]} → {summary["period"]["formalEnd"]}</strong></span><span>约 <strong>{summary["period"]["calendarYears"]:.1f} 年</strong></span><span><strong>{summary["period"]["openDays"]}</strong> 个开市日</span><span>统一基准本金 <strong>¥100,000</strong></span><span>这不是一年回测，逐年行只是子区间</span></div>
  </header>

  <section>
    <div class="section-head"><div><span class="eyebrow">READ THIS FIRST</span><h2>先看懂报告中的五个方案</h2></div><p>报告全程使用中文名称，不用编号替代策略。配置文件和运行ID只在最后的复现身份中出现。</p></div>
    <div class="protocols">{glossary}</div>
  </section>

  <section>
    <div class="verdict-grid">
      <div class="verdict-copy"><span class="eyebrow">强制结论</span><div class="big">不通过</div><p>{escape(summary["oneSentenceConclusion"])}</p><div class="callout">最关键的不是“没有达到50%年化”，而是长历史财富下降73.35%、最大回撤超过90%，说明当前代理机制本身没有长期可用性。</div></div>
      <div class="kpis">
        <div class="kpi"><span>初始本金</span><b>¥100,000</b><small>所有本地方案统一口径</small></div>
        <div class="kpi"><span>主版本期末资产</span><b class="bad-number">{_fmt(primary["finalCapital"], "money")}</b><small>累计盈亏 {_fmt(primary["profitAndLoss"], "money")}</small></div>
        <div class="kpi"><span>沪深300期末资产</span><b class="good-number">{_fmt(benchmark["finalCapital"], "money")}</b><small>同期累计 {_fmt(benchmark["totalReturn"], "pct")}</small></div>
        <div class="kpi"><span>主版本CAGR</span><b class="bad-number">{_fmt(primary["annualizedReturn"], "pct")}</b><small>距离50%目标 {0.5-primary["annualizedReturn"]:.2%}</small></div>
        <div class="kpi"><span>主版本最大回撤</span><b class="bad-number">{_fmt(primary["maxDrawdown"], "pct")}</b><small>{drawdown["peakDate"]} → {drawdown["troughDate"]}</small></div>
        <div class="kpi"><span>Walk-forward</span><b class="bad-number">{summary["walkForward"]["positiveCount"]}/{summary["walkForward"]["windowCount"]}</b><small>正收益窗口；跑赢基准 {summary["walkForward"]["beatBenchmarkCount"]}/{summary["walkForward"]["windowCount"]}</small></div>
      </div>
    </div>
  </section>

  <section>
    <div class="section-head"><div><span class="eyebrow">SOURCE REPLICATION</span><h2>先回答：原网页复现出来了吗？</h2></div><p><b class="bad-number">没有。</b> 买入次数接近，但收益和回撤两项核心数值均未达到事前复现门槛。</p></div>
    <div class="content"><p class="note">原网页本金为 {_fmt(summary["publicSource"]["reportedInitialCapital"], "money")}；下表统一按 {_fmt(summary["initialCapital"], "money")} 等比例展示，收益率不变。</p><div class="table-wrap">{source_table}</div><div class="split"><div><h3>来源矛盾与不可见部分</h3><ul>{contradiction_items}</ul></div><div><h3>怎样理解差距</h3><p class="note">网页机械口径对照已经给了同周期最有利的收盘价、碎股和零成本，但累计收益仍只有 {_fmt(summary["sourceComparison"][1]["totalReturn"], "pct")}；因此85.50个百分点差距主要不能归因于本地费用，而更可能来自未公开的全市场股票池、完整B1因子和活跃市值波段。买入次数接近只能说明交易频率相似，不能证明选中了相同股票。</p><p><a href="{source_url}">打开投资科学原始回测页面 ↗</a></p></div></div></div>
  </section>

  <section>
    <div class="section-head"><div><span class="eyebrow">PRE-REGISTERED GATES</span><h2>事前门禁：仅买入次数接近</h2></div><p>11项中1项通过、10项失败；门槛在查看本地策略收益之前写入，失败后没有搜索参数、缩短区间或加杠杆。</p></div>
    <div class="table-wrap">{gates}</div>
  </section>

  <section>
    <div class="section-head"><div><span class="eyebrow">STRATEGY PROFILE</span><h2>这个策略究竟在做什么</h2></div><p>它不是“下跌就抄底”，而是只在中期趋势仍向上时，等待短期深回调后买入。</p></div>
    <div class="profile-grid">
      <article><h3>经济假设</h3><p>{escape(summary["strategyProfile"]["economicHypothesis"])}</p><p>{escape(summary["strategyProfile"]["counterparty"])}</p></article>
      <article><h3>股票池与市场门</h3><p>{escape(summary["strategyProfile"]["universe"])}</p><p>{escape(summary["strategyProfile"]["marketGate"])}</p></article>
      <article><h3>买入条件</h3><p>{escape(summary["strategyProfile"]["entry"])}</p><p><code>{escape(summary["strategyProfile"]["score"])}</code></p></article>
      <article><h3>组合构建</h3><p>{escape(summary["strategyProfile"]["portfolio"])}</p></article>
      <article><h3>卖出规则</h3><p>{escape(summary["strategyProfile"]["exit"])}</p></article>
      <article><h3>风险边界</h3><p>{escape(summary["strategyProfile"]["risk"])}</p><ul>{''.join(f'<li>{escape(item)}</li>' for item in summary["strategyProfile"]["failureModes"])}</ul></article>
    </div>
  </section>

  <section>
    <div class="section-head"><div><span class="eyebrow">LONG HISTORY</span><h2>长历史总体指标</h2></div><p>主版本、来源参数对照与双倍成本都使用同一冻结快照；沪深300是同币种、同频率方向基准。</p></div>
    <div class="table-wrap">{long_table}</div>
    <div class="chart">{charts["longCapital"]}</div><div class="legend"><span><i style="background:#ff6b5f"></i>长历史主版本</span><span><i style="background:#ffb454"></i>页面参数一致性对照</span><span><i style="background:#a776ff"></i>双倍成本压力</span><span><i style="background:#65d6ff"></i>沪深300指数</span></div>
  </section>

  <section>
    <div class="section-head"><div><span class="eyebrow">EXECUTION LEDGER</span><h2>理想收益如何在现实成交中消失</h2></div><p>同周期从收盘碎股零成本的17.45%，降到次日开盘整手净收益1.33%；长期成本压力进一步放大。</p></div>
    <div class="table-wrap">{execution_table}</div>
    <div class="split"><div><h3>长历史请求原因</h3><ul class="reason-list">{reason_items}</ul><p class="note">cash_capacity 同时覆盖完全阻塞与部分成交；涨跌停和停牌按冻结日线明确处理。</p></div><div><h3>容量仍然缺失</h3><p class="warning">{escape(summary["risk"]["advParticipation"])}</p><p>{escape(summary["risk"]["profitFactor"])}</p><p>所以这份报告可以评价机制与执行摩擦，但不能给出“可投多少钱”的容量结论。</p></div></div>
    <div class="split"><div><div class="chart">{charts["turnover"]}</div></div><div><div class="chart">{charts["cost"]}</div></div></div>
  </section>

  <section>
    <div class="section-head"><div><span class="eyebrow">DRAWDOWN</span><h2>回撤不是短暂波动，而是长期失效</h2></div><p>高点到谷底损失 {_fmt(drawdown["lossAmount"], "money")}；截至样本结束{'已恢复' if drawdown["recovered"] else '仍未恢复'}。</p></div>
    <div class="chart">{charts["drawdown"]}</div><div class="legend"><span><i style="background:#ff6b5f"></i>长历史主版本</span><span><i style="background:#ffb454"></i>页面参数一致性对照</span><span><i style="background:#a776ff"></i>双倍成本压力</span><span><i style="background:#65d6ff"></i>沪深300指数</span></div>
    <div class="split"><div><h3>最深回撤路径</h3><p>前高：{drawdown["peakDate"]} · {_fmt(drawdown["peakCapital"], "money")}<br>谷底：{drawdown["troughDate"]} · {_fmt(drawdown["troughCapital"], "money")}<br>回撤：<b class="bad-number">{_fmt(drawdown["maxDrawdown"], "pct")}</b><br>恢复：{escape(drawdown["recoveryDate"] or "样本结束仍未恢复")}</p></div><div><h3>尾部与alpha</h3><p>策略日VaR95 {_fmt(summary["risk"]["tail"]["strategy"]["var95"], "pct")}；日ES95 {_fmt(summary["risk"]["tail"]["strategy"]["es95"], "pct")}。</p><p>HAC年化alpha {_fmt(summary["risk"]["hacAlpha"]["annualizedAlpha"], "pct")}；95%区间 {_fmt(summary["risk"]["hacAlpha"]["ci95Low"], "pct")} 至 {_fmt(summary["risk"]["hacAlpha"]["ci95High"], "pct")}；t={summary["risk"]["hacAlpha"]["alphaTStatistic"]:.2f}。</p></div></div>
  </section>

  <section>
    <div class="section-head"><div><span class="eyebrow">YEAR BY YEAR</span><h2>逐年稳定性：每一行都只是总周期的子区间</h2></div><p>2012和2026是边界部分年度，不能拿其中任一行替代完整2012至2026结论。</p></div><div class="table-wrap">{yearly}</div>
  </section>

  <section>
    <div class="section-head"><div><span class="eyebrow">MARKET REGIMES</span><h2>方向 × 波动率环境矩阵</h2></div><p class="warning">{escape(summary["regimeDefinition"]["warning"])}</p></div><div class="table-wrap">{regimes}</div>
    <div class="content"><p class="note">方向：{escape(summary["regimeDefinition"]["direction"])} 波动：{escape(summary["regimeDefinition"]["volatility"])} 阈值={summary["regimeDefinition"]["varianceThreshold"]:.6f}。</p></div>
  </section>

  <section>
    <div class="section-head"><div><span class="eyebrow">STRESS WINDOWS</span><h2>客观压力窗口</h2></div><p class="warning">这些窗口没有写入B1事前登记，只作事后失效定位，不进入通过门禁。</p></div><div class="table-wrap">{stress}</div>
  </section>

  <section>
    <div class="section-head"><div><span class="eyebrow">WALK-FORWARD</span><h2>11个时间测试窗：最近9个全部为负</h2></div><p>正收益 {summary["walkForward"]["positiveCount"]}/{summary["walkForward"]["windowCount"]}；跑赢沪深300 {summary["walkForward"]["beatBenchmarkCount"]}/{summary["walkForward"]["windowCount"]}。{escape(summary["walkForward"]["warning"])}</p></div><div class="table-wrap">{walk_forward}</div>
    <div class="content"><div class="callout">真正前瞻段 {post["startDate"]} → {post["endDate"]} 只有 {post["observations"]} 个收益日：策略 {_fmt(post["strategyReturn"], "pct")}、沪深300 {_fmt(post["benchmarkReturn"], "pct")}。{escape(post["evidenceStatus"])}</div></div>
  </section>

  <section>
    <div class="section-head"><div><span class="eyebrow">CONCENTRATION</span><h2>Top2 不等于最多持有2只</h2></div><p>释放现金会继续买入新候选；长历史最多持有 {summary["risk"]["maxHoldingCount"]} 只，单票漂移后最高 {_fmt(summary["risk"]["maxSingleWeight"], "pct")}。</p></div>
    <div class="split"><div><div class="chart">{charts["exposure"]}</div></div><div><div class="chart">{charts["concentration"]}</div></div></div>
    <div class="content"><p>平均持仓数 {summary["risk"]["averageHoldingCount"]:.2f}；平均HHI {summary["risk"]["averageHhi"]:.3f}，最大HHI {summary["risk"]["maxHhi"]:.3f}。持仓数量与暴露共用图轴，只用于观察时序，不用于比较单位。</p></div>
  </section>

  <section>
    <div class="section-head"><div><span class="eyebrow">OPTIMIZATION</span><h2>优化方向：先修忠实度和换手，不在失败样本调阈值</h2></div><p>这些是下一轮可证伪的研究路径，不是对收益的承诺。</p></div><div class="content">{optimization}</div>
  </section>

  <section>
    <div class="section-head"><div><span class="eyebrow">EVIDENCE BALANCE</span><h2>支持、反对与尚缺证据</h2></div><p>研究管线可信，不代表策略有效；两类结论必须分开。</p></div>
    <div class="evidence-grid"><article><h3 class="good-number">支持证据</h3>{_list(summary["supportingEvidence"])}</article><article><h3 class="bad-number">反对证据</h3>{_list(summary["opposingEvidence"])}</article><article><h3 class="warning">尚缺证据</h3>{_list(summary["missingEvidence"])}</article></div>
  </section>

  <section>
    <div class="section-head"><div><span class="eyebrow">DATA & REPRODUCTION</span><h2>数据质量与复现身份</h2></div><p>五份运行均在断网容器中从冻结输入重新计算，结果指纹全部匹配。</p></div>
    <div class="split"><div><h3>质量门禁</h3><p>状态 <code>{escape(summary["dataEvidence"]["qualityStatus"])}</code>；通过规则 {summary["dataEvidence"]["passedRules"]}；失败规则 {summary["dataEvidence"]["failedRules"]}。</p><p>quality run <code>{escape(summary["quality"]["qualityRunId"])}</code><br>snapshot <code>{escape(summary["quality"]["snapshotId"])}</code><br>code <code>{escape(summary["quality"]["codeCommit"])}</code><br>schema <code>{escape(summary["quality"]["schemaRevision"])}</code></p><h3>显式warning</h3><ul>{warning_items}</ul></div><div><h3>停止条件</h3>{_list(summary["stopConditions"])}<h3>过拟合语义</h3><p>{escape(summary["overfitting"]["dsr"])}</p><p>{escape(summary["overfitting"]["pbo"])}</p><p>{escape(summary["overfitting"]["sourceRisk"])}</p></div></div>
    <div class="table-wrap">{reproduction}</div>
  </section>

  <footer class="footer">REPORT_ID {escape(summary["reportId"])} · RESEARCH ONLY · NOT INVESTMENT ADVICE · 生成日期 {escape(summary["researchDate"])} · 所有金额按¥100,000统一展示</footer>
</main></body></html>'''


def _line_svg(
    series: dict[str, pd.Series],
    colors: dict[str, str],
) -> str:
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
    width, height, pad = 760.0, 260.0, 30.0
    polylines = []
    for label, values in normalized.items():
        sampled = values.iloc[:: max(len(values) // 360, 1)]
        if sampled.index[-1] != values.index[-1]:
            sampled = pd.concat([sampled, values.iloc[[-1]]])
        points = []
        for index, value in enumerate(sampled):
            x = pad + index * (width - 2 * pad) / max(len(sampled) - 1, 1)
            y = height - pad - (float(value) - low) / span * (height - 2 * pad)
            points.append(f"{x:.2f},{y:.2f}")
        polylines.append(
            f'<polyline aria-label="{escape(label)}" fill="none" stroke="{colors[label]}" stroke-width="2" vector-effect="non-scaling-stroke" points="{" ".join(points)}" />'
        )
    high_label = f"{high:,.0f}" if abs(high) >= 1_000 else f"{high:.3f}"
    low_label = f"{low:,.0f}" if abs(low) >= 1_000 else f"{low:.3f}"
    return (
        f'<svg viewBox="0 0 {int(width)} {int(height)}" role="img">'
        f'<line x1="{pad}" y1="{pad}" x2="{pad}" y2="{height-pad}" stroke="#3b493f" />'
        f'<line x1="{pad}" y1="{height-pad}" x2="{width-pad}" y2="{height-pad}" stroke="#3b493f" />'
        f'<text x="36" y="20" fill="#98a39b" font-size="11">{high_label}</text>'
        f'<text x="36" y="252" fill="#98a39b" font-size="11">{low_label}</text>'
        + "".join(polylines)
        + "</svg>"
    )


def _table(
    rows: list[dict[str, Any]],
    columns: tuple[tuple[str, str, str], ...],
) -> str:
    head = "".join(f"<th>{escape(title)}</th>" for _key, title, _kind in columns)
    body = []
    for row in rows:
        cells = []
        for key, _title, kind in columns:
            value = row.get(key)
            css = "ok" if kind == "bool" and value else "bad" if kind == "bool" else ""
            cells.append(f'<td class="{css}">{_fmt(value, kind)}</td>')
        body.append("<tr>" + "".join(cells) + "</tr>")
    return f"<table><thead><tr>{head}</tr></thead><tbody>{''.join(body)}</tbody></table>"


def _fmt(value: Any, kind: str) -> str:
    if value is None or (isinstance(value, float) and not math.isfinite(value)):
        return "—"
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
    return escape(str(value))


def _list(items: list[str]) -> str:
    return "<ul>" + "".join(f"<li>{escape(item)}</li>" for item in items) + "</ul>"


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
    if isinstance(value, (pd.Timestamp, pd.Period)):
        return str(value)
    return value


if __name__ == "__main__":
    raise SystemExit(main())
