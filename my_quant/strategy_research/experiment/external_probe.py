from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

from .config import COST_RATE, RESULTS_DIR, SATELLITE_MAX_DRAWDOWN_FLOOR, SATELLITE_TARGET_ANNUAL_RETURN, TRADING_DAYS
from .metrics import calculate_metrics
from .reports import markdown_table


EXTERNAL_ASSETS = ["TQQQ", "SOXL", "TECL", "UPRO", "BTC-USD", "ETH-USD", "GLD", "TLT"]
EXTERNAL_RISK_ASSETS = ["TQQQ", "SOXL", "TECL", "UPRO", "BTC-USD", "ETH-USD"]
EXTERNAL_DEFENSE_ASSETS = ["CASH", "GLD", "TLT"]
EXTERNAL_EVAL_START = "2021-01-01"
EXTERNAL_EVAL_END = "2026-06-15"


@dataclass(frozen=True)
class ExternalRamConfig:
    top_n: int
    momentum_window: int
    volatility_window: int
    trend_window: int
    rebalance_interval: int
    gross_exposure: float
    defense_asset: str
    drawdown_half: float
    drawdown_quarter: float

    @property
    def name(self) -> str:
        exposure = str(self.gross_exposure).replace(".", "_")
        return (
            f"ext_ram_top{self.top_n}_m{self.momentum_window}_v{self.volatility_window}"
            f"_t{self.trend_window}_f{self.rebalance_interval}_x{exposure}_{self.defense_asset}"
        )


@dataclass(frozen=True)
class ExternalFixedConfig:
    name: str
    weights: dict[str, float]
    gross_exposure: float
    drawdown_half: float
    drawdown_quarter: float


def load_external_prices(cache_dir: Path, refresh: bool = False) -> pd.DataFrame:
    try:
        import yfinance as yf
    except ImportError as exc:
        raise RuntimeError("yfinance is required for external satellite probe") from exc

    cache_dir.mkdir(parents=True, exist_ok=True)
    series: dict[str, pd.Series] = {}
    for symbol in EXTERNAL_ASSETS:
        cache_path = cache_dir / f"{symbol.replace('-', '_')}.csv"
        if cache_path.exists() and not refresh:
            df = pd.read_csv(cache_path, parse_dates=["date"])
        else:
            df = yf.download(
                symbol,
                start="2017-01-01",
                end="2026-06-16",
                auto_adjust=True,
                progress=False,
                threads=False,
            )
            if df.empty:
                raise RuntimeError(f"yfinance returned empty data for {symbol}")
            df = df.reset_index()
            date_col = "Date" if "Date" in df.columns else "date"
            close_col = "Close" if "Close" in df.columns else "close"
            df = df.rename(columns={date_col: "date", close_col: "close"})[["date", "close"]]
            df.to_csv(cache_path, index=False)
        df = df.copy()
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        df["close"] = pd.to_numeric(df["close"], errors="coerce")
        df = df.dropna(subset=["date", "close"])
        close = df.sort_values("date").set_index("date")["close"].astype(float)
        close.name = symbol
        series[symbol] = close

    prices = pd.concat(series.values(), axis=1).sort_index().ffill()
    prices["CASH"] = 1.0
    return prices.dropna(subset=["TQQQ", "SOXL", "TECL", "UPRO", "BTC-USD", "GLD"])


def _window_positions(index: pd.DatetimeIndex, start: str = EXTERNAL_EVAL_START, end: str = EXTERNAL_EVAL_END) -> tuple[int, int]:
    start_pos = index.searchsorted(pd.Timestamp(start))
    end_pos = index.searchsorted(pd.Timestamp(end), side="right") - 1
    if end_pos <= start_pos:
        raise ValueError(f"Invalid external probe window: {start} to {end}")
    return int(start_pos), int(end_pos)


def _nav_metrics(nav: np.ndarray, index: pd.DatetimeIndex) -> dict[str, float]:
    return calculate_metrics(pd.Series(nav, index=index))


def _gate_row(row: dict[str, object]) -> dict[str, object]:
    row["passes_return_gate"] = float(row["annual_return"]) >= SATELLITE_TARGET_ANNUAL_RETURN
    row["passes_drawdown_gate"] = float(row["max_drawdown"]) >= SATELLITE_MAX_DRAWDOWN_FLOOR
    return row


def external_ram_configs() -> list[ExternalRamConfig]:
    configs: list[ExternalRamConfig] = []
    for top_n in [1, 2]:
        for momentum_window in [10, 20, 60, 120]:
            for volatility_window in [10, 20, 60]:
                for trend_window in [50, 100, 200]:
                    for rebalance_interval in [5, 10, 21]:
                        for gross_exposure in [1.0, 1.5, 2.0, 2.5]:
                            for defense_asset in EXTERNAL_DEFENSE_ASSETS:
                                for drawdown_half, drawdown_quarter in [(-0.08, -0.14), (-0.12, -0.20), (-0.15, -0.22)]:
                                    configs.append(
                                        ExternalRamConfig(
                                            top_n=top_n,
                                            momentum_window=momentum_window,
                                            volatility_window=volatility_window,
                                            trend_window=trend_window,
                                            rebalance_interval=rebalance_interval,
                                            gross_exposure=gross_exposure,
                                            defense_asset=defense_asset,
                                            drawdown_half=drawdown_half,
                                            drawdown_quarter=drawdown_quarter,
                                        )
                                    )
    return configs


def external_fixed_configs() -> list[ExternalFixedConfig]:
    raw_blends = [
        ("ext_fixed_tecl_gld_50_50", {"TECL": 0.50, "GLD": 0.50}),
        ("ext_fixed_tqqq_gld_50_50", {"TQQQ": 0.50, "GLD": 0.50}),
        ("ext_fixed_btc_gld_50_50", {"BTC-USD": 0.50, "GLD": 0.50}),
        ("ext_fixed_tqqq_btc_gld_equal", {"TQQQ": 1 / 3, "BTC-USD": 1 / 3, "GLD": 1 / 3}),
        ("ext_fixed_tqqq_soxl_btc_gld_equal", {"TQQQ": 0.25, "SOXL": 0.25, "BTC-USD": 0.25, "GLD": 0.25}),
    ]
    configs: list[ExternalFixedConfig] = []
    for name, weights in raw_blends:
        for gross_exposure in [1.0, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0]:
            for drawdown_half, drawdown_quarter in [(-0.08, -0.14), (-0.12, -0.20), (-0.15, -0.22)]:
                exposure = str(gross_exposure).replace(".", "_")
                configs.append(
                    ExternalFixedConfig(
                        name=f"{name}_x{exposure}",
                        weights=weights,
                        gross_exposure=gross_exposure,
                        drawdown_half=drawdown_half,
                        drawdown_quarter=drawdown_quarter,
                    )
                )
    return configs


def _drawdown_scale(drawdown: float, half: float, quarter: float, stop: float = SATELLITE_MAX_DRAWDOWN_FLOOR) -> float:
    if drawdown <= stop:
        return 0.0
    if drawdown <= quarter:
        return 0.25
    if drawdown <= half:
        return 0.5
    return 1.0


def run_external_ram_config(prices: pd.DataFrame, config: ExternalRamConfig) -> dict[str, object]:
    returns = prices.pct_change().fillna(0.0)
    index = prices.index
    columns = list(prices.columns)
    col_index = {column: i for i, column in enumerate(columns)}
    price_values = prices.to_numpy(dtype=float)
    return_values = returns.to_numpy(dtype=float)
    start_pos, end_pos = _window_positions(index)
    nav = np.ones(end_pos - start_pos + 1)
    weights = np.zeros(len(columns))
    peak_nav = 1.0
    cooldown_left = 0
    total_turnover = 0.0
    rebalance_count = 0
    risk_indices = [col_index[symbol] for symbol in EXTERNAL_RISK_ASSETS]
    defense_index = col_index[config.defense_asset]
    cash_index = col_index["CASH"]

    for local_i, pos in enumerate(range(start_pos, end_pos + 1)):
        peak_nav = max(peak_nav, float(nav[local_i]))
        current_drawdown = float(nav[local_i] / peak_nav - 1.0)
        force_defense = current_drawdown <= SATELLITE_MAX_DRAWDOWN_FLOOR or cooldown_left > 0
        if current_drawdown <= SATELLITE_MAX_DRAWDOWN_FLOOR:
            cooldown_left = max(cooldown_left, 21)

        if local_i == 0 or local_i % config.rebalance_interval == 0 or force_defense:
            target_weights = np.zeros(len(columns))
            if force_defense:
                target_weights[defense_index] = 1.0
            else:
                scores: list[tuple[float, int]] = []
                min_history = max(config.momentum_window, config.volatility_window, config.trend_window)
                if pos >= min_history:
                    for asset_index in risk_indices:
                        current = price_values[pos, asset_index]
                        past = price_values[pos - config.momentum_window, asset_index]
                        momentum = current / past - 1.0
                        volatility = float(np.nanstd(return_values[pos - config.volatility_window + 1 : pos + 1, asset_index]))
                        moving_average = float(np.nanmean(price_values[pos - config.trend_window + 1 : pos + 1, asset_index]))
                        if volatility > 0 and momentum > 0 and current >= moving_average:
                            scores.append((momentum / volatility, asset_index))
                if scores:
                    selected = sorted(scores, reverse=True)[: config.top_n]
                    total_score = sum(score for score, _asset_index in selected)
                    exposure = config.gross_exposure * _drawdown_scale(
                        current_drawdown,
                        config.drawdown_half,
                        config.drawdown_quarter,
                    )
                    for score, asset_index in selected:
                        target_weights[asset_index] = score / total_score * exposure
                    if exposure < 1.0:
                        target_weights[cash_index] = 1.0 - exposure
                else:
                    target_weights[defense_index] = 1.0

            turnover = float(np.abs(target_weights - weights).sum())
            if turnover > 1e-12:
                nav[local_i] *= 1 - turnover * COST_RATE
                total_turnover += turnover
                rebalance_count += 1
            weights = target_weights

        if cooldown_left > 0:
            cooldown_left -= 1
        if local_i + 1 < len(nav):
            gross = float(np.abs(weights[[i for i, column in enumerate(columns) if column != "CASH"]]).sum())
            borrow_cost = max(0.0, gross - 1.0) * 0.05 / TRADING_DAYS
            nav[local_i + 1] = nav[local_i] * (1 + float(weights @ return_values[pos + 1]) - borrow_cost)

    row: dict[str, object] = {
        "strategy": config.name,
        "strategy_family": "external_ram",
        "top_n": config.top_n,
        "momentum_window": config.momentum_window,
        "volatility_window": config.volatility_window,
        "trend_window": config.trend_window,
        "rebalance_interval": config.rebalance_interval,
        "gross_exposure": config.gross_exposure,
        "defense_asset": config.defense_asset,
        "drawdown_half": config.drawdown_half,
        "drawdown_quarter": config.drawdown_quarter,
        "total_turnover": total_turnover,
        "rebalance_count": float(rebalance_count),
        "estimated_cost": total_turnover * COST_RATE,
    }
    row.update(_nav_metrics(nav, index[start_pos : end_pos + 1]))
    return _gate_row(row)


def run_external_fixed_config(prices: pd.DataFrame, config: ExternalFixedConfig) -> dict[str, object]:
    returns = prices.pct_change().fillna(0.0)
    index = prices.index
    columns = list(prices.columns)
    col_index = {column: i for i, column in enumerate(columns)}
    return_values = returns.to_numpy(dtype=float)
    start_pos, end_pos = _window_positions(index)
    nav = np.ones(end_pos - start_pos + 1)
    weights = np.zeros(len(columns))
    base_weights = np.zeros(len(columns))
    for symbol, weight in config.weights.items():
        base_weights[col_index[symbol]] = weight
    cash_index = col_index["CASH"]
    peak_nav = 1.0
    cooldown_left = 0
    total_turnover = 0.0
    rebalance_count = 0

    for local_i, pos in enumerate(range(start_pos, end_pos + 1)):
        peak_nav = max(peak_nav, float(nav[local_i]))
        current_drawdown = float(nav[local_i] / peak_nav - 1.0)
        force_defense = current_drawdown <= SATELLITE_MAX_DRAWDOWN_FLOOR or cooldown_left > 0
        if current_drawdown <= SATELLITE_MAX_DRAWDOWN_FLOOR:
            cooldown_left = max(cooldown_left, 21)

        if local_i == 0 or local_i % 21 == 0 or force_defense:
            target_weights = np.zeros(len(columns))
            if force_defense:
                target_weights[cash_index] = 1.0
            else:
                exposure = config.gross_exposure * _drawdown_scale(
                    current_drawdown,
                    config.drawdown_half,
                    config.drawdown_quarter,
                )
                target_weights = base_weights * exposure
                if exposure < 1.0:
                    target_weights[cash_index] = 1.0 - exposure
            turnover = float(np.abs(target_weights - weights).sum())
            if turnover > 1e-12:
                nav[local_i] *= 1 - turnover * COST_RATE
                total_turnover += turnover
                rebalance_count += 1
            weights = target_weights

        if cooldown_left > 0:
            cooldown_left -= 1
        if local_i + 1 < len(nav):
            gross = float(np.abs(weights[[i for i, column in enumerate(columns) if column != "CASH"]]).sum())
            borrow_cost = max(0.0, gross - 1.0) * 0.05 / TRADING_DAYS
            nav[local_i + 1] = nav[local_i] * (1 + float(weights @ return_values[pos + 1]) - borrow_cost)

    row: dict[str, object] = {
        "strategy": config.name,
        "strategy_family": "external_fixed",
        "fixed_weights": ";".join(f"{symbol}:{weight:.2f}" for symbol, weight in config.weights.items()),
        "gross_exposure": config.gross_exposure,
        "drawdown_half": config.drawdown_half,
        "drawdown_quarter": config.drawdown_quarter,
        "total_turnover": total_turnover,
        "rebalance_count": float(rebalance_count),
        "estimated_cost": total_turnover * COST_RATE,
    }
    row.update(_nav_metrics(nav, index[start_pos : end_pos + 1]))
    return _gate_row(row)


def _best_probe_row(rows: pd.DataFrame) -> pd.Series:
    passing = rows[(rows["passes_return_gate"]) & (rows["passes_drawdown_gate"])]
    if not passing.empty:
        return passing.sort_values(["annual_return", "calmar"], ascending=False).iloc[0]
    dd_passing = rows[rows["passes_drawdown_gate"]]
    if not dd_passing.empty:
        return dd_passing.sort_values(["annual_return", "calmar"], ascending=False).iloc[0]
    return rows.sort_values(["max_drawdown", "annual_return"], ascending=False).iloc[0]


def build_external_probe_summary(ram_df: pd.DataFrame, fixed_df: pd.DataFrame) -> str:
    combined = pd.concat([ram_df, fixed_df], ignore_index=True)
    best = _best_probe_row(combined)
    passing_count = int(((combined["annual_return"] >= SATELLITE_TARGET_ANNUAL_RETURN) & (combined["max_drawdown"] >= SATELLITE_MAX_DRAWDOWN_FLOOR)).sum())
    return_pass_count = int((combined["annual_return"] >= SATELLITE_TARGET_ANNUAL_RETURN).sum())
    top_dd = combined[combined["max_drawdown"] >= SATELLITE_MAX_DRAWDOWN_FLOOR].sort_values(["annual_return", "calmar"], ascending=False).head(10)
    top_return = combined.sort_values(["annual_return", "calmar"], ascending=False).head(10)
    lines = [
        "# External High-Volatility Satellite Probe",
        "",
        f"- Evaluation window: `{EXTERNAL_EVAL_START}` to `{EXTERNAL_EVAL_END}`.",
        f"- Assets: `{', '.join(EXTERNAL_ASSETS)}`.",
        f"- 50% annual return and -30% max drawdown passing rows: `{passing_count}`.",
        f"- Rows with annual return >= 50% before drawdown gate: `{return_pass_count}`.",
        f"- Best drawdown-qualified near-miss: `{best['strategy']}`.",
        f"- Annual return: `{float(best['annual_return']) * 100:.2f}%`.",
        f"- Max drawdown: `{float(best['max_drawdown']) * 100:.2f}%`.",
        "",
        "## Top Drawdown-Qualified Rows",
        "",
        markdown_table(
            top_dd[["strategy", "strategy_family", "annual_return", "max_drawdown", "calmar", "gross_exposure"]],
            index=False,
            floatfmt=".4f",
        ),
        "",
        "## Top Return Rows",
        "",
        markdown_table(
            top_return[["strategy", "strategy_family", "annual_return", "max_drawdown", "calmar", "gross_exposure"]],
            index=False,
            floatfmt=".4f",
        ),
        "",
        "Interpretation: this probe extends beyond the China ETF universe. It is still a research screen, not a tradable instruction.",
    ]
    return "\n".join(lines) + "\n"


def run_external_probe(results_dir: Path = RESULTS_DIR, refresh: bool = False) -> tuple[pd.DataFrame, pd.DataFrame]:
    cache_dir = results_dir.parent / "external_data_cache"
    prices = load_external_prices(cache_dir, refresh=refresh)
    results_dir.mkdir(parents=True, exist_ok=True)

    ram_df = pd.DataFrame([run_external_ram_config(prices, config) for config in external_ram_configs()])
    ram_df = ram_df.sort_values(["passes_drawdown_gate", "annual_return", "calmar"], ascending=False)
    ram_df.to_csv(results_dir / "satellite_external_ram_probe.csv", index=False)

    fixed_df = pd.DataFrame([run_external_fixed_config(prices, config) for config in external_fixed_configs()])
    fixed_df = fixed_df.sort_values(["passes_drawdown_gate", "annual_return", "calmar"], ascending=False)
    fixed_df.to_csv(results_dir / "satellite_external_fixed_probe.csv", index=False)

    summary = build_external_probe_summary(ram_df, fixed_df)
    (results_dir / "satellite_external_probe_summary.md").write_text(summary, encoding="utf-8")

    combined = pd.concat([ram_df, fixed_df], ignore_index=True)
    best = _best_probe_row(combined)
    manifest = {
        "experiment": "satellite_external_high_volatility_probe",
        "target_annual_return": SATELLITE_TARGET_ANNUAL_RETURN,
        "max_drawdown_floor": SATELLITE_MAX_DRAWDOWN_FLOOR,
        "evaluation_start": EXTERNAL_EVAL_START,
        "evaluation_end": EXTERNAL_EVAL_END,
        "latest_price_date": str(prices.index.max().date()),
        "ram_rows": int(len(ram_df)),
        "fixed_rows": int(len(fixed_df)),
        "passing_rows": int(((combined["annual_return"] >= SATELLITE_TARGET_ANNUAL_RETURN) & (combined["max_drawdown"] >= SATELLITE_MAX_DRAWDOWN_FLOOR)).sum()),
        "return_gate_rows": int((combined["annual_return"] >= SATELLITE_TARGET_ANNUAL_RETURN).sum()),
        "best_near_miss": str(best["strategy"]),
        "best_near_miss_annual_return": float(best["annual_return"]),
        "best_near_miss_max_drawdown": float(best["max_drawdown"]),
        "artifacts": [
            "satellite_external_ram_probe.csv",
            "satellite_external_fixed_probe.csv",
            "satellite_external_probe_summary.md",
            "satellite_external_probe_manifest.json",
        ],
    }
    (results_dir / "satellite_external_probe_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return ram_df, fixed_df


def main() -> None:
    run_external_probe()
    print((RESULTS_DIR / "satellite_external_probe_summary.md").read_text(encoding="utf-8"))
