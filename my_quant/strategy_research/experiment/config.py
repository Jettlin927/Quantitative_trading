from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data_cache"
RESULTS_DIR = ROOT / "results"

EVAL_START = "2021-01-04"
EVAL_END = "2026-06-15"
TRAIN_START = "2017-09-01"
TRAIN_END = "2020-12-31"
COST_RATE = 0.001
TRADING_DAYS = 252

SYMBOL_NAMES = {
    "510300": "沪深300ETF",
    "513100": "纳指100ETF",
    "518880": "黄金ETF",
    "511260": "10年国债ETF",
    "511880": "银华日利ETF",
}

BASELINE_WEIGHTS = {
    "510300": 0.25,
    "511260": 0.25,
    "518880": 0.25,
    "511880": 0.25,
}

ALL_ASSETS = ["510300", "513100", "518880", "511260", "511880"]
RISK_ASSETS = ["510300", "513100", "518880", "511260"]
DEFENSE_ASSET = "511880"

SATELLITE_DEFENSE_ASSET = "511880"
SATELLITE_RISK_ASSETS = [
    "510300",  # 沪深300
    "513100",  # 纳指100
    "518880",  # 黄金
    "159915",  # 创业板
    "588000",  # 科创50
    "512480",  # 半导体
    "513330",  # 恒生互联网
    "513180",  # 恒生科技
    "512400",  # 有色金属
    "516160",  # 新能源
    "512660",  # 军工
    "512690",  # 酒
]
SATELLITE_UNIVERSE = SATELLITE_RISK_ASSETS + ["511260", SATELLITE_DEFENSE_ASSET]
SATELLITE_TARGET_ANNUAL_RETURN = 0.50
SATELLITE_MAX_DRAWDOWN_FLOOR = -0.30

SYMBOL_NAMES.update(
    {
        "159915": "创业板ETF",
        "588000": "科创50ETF",
        "512480": "半导体ETF",
        "513330": "恒生互联网ETF",
        "513180": "恒生科技ETF",
        "512400": "有色金属ETF",
        "516160": "新能源ETF",
        "512660": "军工ETF",
        "512690": "酒ETF",
    }
)


@dataclass(frozen=True)
class ExperimentPaths:
    root: Path = ROOT
    data_dir: Path = DATA_DIR
    results_dir: Path = RESULTS_DIR


@dataclass(frozen=True)
class StrategyConfig:
    name: str
    kind: str
    top_n: int | None = None
    momentum_window: int | None = None
    volatility_window: int | None = None
    interval_days: int | None = None
    cost_rate: float = 0.0


def base_configs(cost_rate: float = COST_RATE) -> list[StrategyConfig]:
    return [
        StrategyConfig("baseline_china_permanent_25_annual_no_cost", "permanent"),
        StrategyConfig("equal_weight_5_assets_monthly_cost", "equal_weight", interval_days=21, cost_rate=cost_rate),
        StrategyConfig("risk_parity_5_assets_v20_monthly_cost", "risk_parity", volatility_window=20, interval_days=21, cost_rate=cost_rate),
        StrategyConfig("risk_parity_5_assets_v60_monthly_cost", "risk_parity", volatility_window=60, interval_days=21, cost_rate=cost_rate),
        StrategyConfig("ram_top1_m60_v60_monthly_cost", "ram_topn", top_n=1, momentum_window=60, volatility_window=60, interval_days=21, cost_rate=cost_rate),
        StrategyConfig("ram_top2_m60_v60_monthly_cost", "ram_topn", top_n=2, momentum_window=60, volatility_window=60, interval_days=21, cost_rate=cost_rate),
        StrategyConfig("ram_top3_m60_v60_monthly_cost", "ram_topn", top_n=3, momentum_window=60, volatility_window=60, interval_days=21, cost_rate=cost_rate),
        StrategyConfig("ram_top2_m60_v60_monthly_trend_filter_cost", "ram_topn_trend_filter", top_n=2, momentum_window=60, volatility_window=60, interval_days=21, cost_rate=cost_rate),
    ]


def scan_configs(cost_rate: float = COST_RATE) -> list[StrategyConfig]:
    configs: list[StrategyConfig] = []
    for top_n in [1, 2, 3]:
        for momentum_window in [20, 60, 120, 180]:
            for volatility_window in [20, 60, 120]:
                for interval_days in [10, 21, 42, 63]:
                    configs.append(
                        StrategyConfig(
                            name=f"ram_top{top_n}_m{momentum_window}_v{volatility_window}_f{interval_days}_cost",
                            kind="ram_topn",
                            top_n=top_n,
                            momentum_window=momentum_window,
                            volatility_window=volatility_window,
                            interval_days=interval_days,
                            cost_rate=cost_rate,
                        )
                    )
    return configs
