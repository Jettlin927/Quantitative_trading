from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .config import (
    COST_RATE,
    SATELLITE_DEFENSE_ASSET,
    SATELLITE_MAX_DRAWDOWN_FLOOR,
    SATELLITE_RISK_ASSETS,
    SATELLITE_TARGET_ANNUAL_RETURN,
    TRADING_DAYS,
)
from .metrics import calculate_metrics
from .reports import markdown_table
from .strategies import normalize_weights


@dataclass(frozen=True)
class SatelliteConfig:
    name: str
    risk_assets: tuple[str, ...] = tuple(SATELLITE_RISK_ASSETS)
    defense_asset: str = SATELLITE_DEFENSE_ASSET
    top_n: int = 1
    momentum_window: int = 20
    volatility_window: int = 20
    trend_window: int = 120
    rebalance_interval: int = 5
    cost_rate: float = COST_RATE
    drawdown_half: float = -0.15
    drawdown_quarter: float = -0.22
    drawdown_stop: float = SATELLITE_MAX_DRAWDOWN_FLOOR
    cooldown_days: int = 21
    target_volatility: float | None = None


def drawdown_scale(drawdown: float, config: SatelliteConfig) -> float:
    if drawdown <= config.drawdown_stop:
        return 0.0
    if drawdown <= config.drawdown_quarter:
        return 0.25
    if drawdown <= config.drawdown_half:
        return 0.5
    return 1.0


def build_satellite_target_weights(
    prices: pd.DataFrame,
    date: pd.Timestamp,
    config: SatelliteConfig,
    returns: pd.DataFrame | None = None,
) -> dict[str, float]:
    loc = prices.index.get_loc(date)
    min_history = max(config.momentum_window, config.volatility_window, config.trend_window)
    if loc < min_history:
        return {config.defense_asset: 1.0}

    returns = prices.pct_change() if returns is None else returns
    scores: dict[str, float] = {}
    for symbol in config.risk_assets:
        if symbol not in prices.columns:
            continue
        current = prices.loc[date, symbol]
        past = prices[symbol].iloc[loc - config.momentum_window]
        momentum = current / past - 1.0
        volatility = returns[symbol].iloc[loc - config.volatility_window + 1 : loc + 1].std(ddof=0)
        moving_average = prices[symbol].iloc[loc - config.trend_window + 1 : loc + 1].mean()
        if pd.isna(momentum) or pd.isna(volatility) or volatility <= 0:
            continue
        if current < moving_average:
            continue
        ram = momentum / volatility
        if ram > 0:
            scores[symbol] = float(ram)

    if not scores:
        return {config.defense_asset: 1.0}

    selected = sorted(scores.items(), key=lambda item: item[1], reverse=True)[: config.top_n]
    total_score = sum(score for _symbol, score in selected)
    return {symbol: score / total_score for symbol, score in selected}


def _apply_scale_to_defense(
    target: dict[str, float],
    scale: float,
    defense_asset: str,
) -> dict[str, float]:
    if scale <= 0:
        return {defense_asset: 1.0}
    scaled = {symbol: weight * scale for symbol, weight in target.items() if symbol != defense_asset}
    defense_weight = 1.0 - sum(scaled.values())
    if defense_weight > 1e-12:
        scaled[defense_asset] = defense_weight
    return scaled


def run_satellite_nav(
    prices: pd.DataFrame,
    config: SatelliteConfig,
    eval_start: str,
    eval_end: str,
) -> tuple[pd.Series, pd.DataFrame, dict[str, float]]:
    eval_prices = prices.loc[eval_start:eval_end]
    if eval_prices.empty:
        raise ValueError(f"No prices in evaluation range {eval_start} to {eval_end}")

    columns = list(prices.columns)
    returns = prices.pct_change().fillna(0.0)
    rebalance_dates = set(eval_prices.index[:: config.rebalance_interval])
    nav = pd.Series(index=eval_prices.index, dtype=float)
    weights_history = pd.DataFrame(index=eval_prices.index, columns=columns, dtype=float)
    nav.iloc[0] = 1.0
    current_weights = pd.Series(0.0, index=columns)
    peak_nav = 1.0
    cooldown_left = 0
    cooldown_days_total = 0
    total_turnover = 0.0
    rebalance_count = 0

    for i, date in enumerate(eval_prices.index):
        peak_nav = max(peak_nav, float(nav.iloc[i]))
        current_drawdown = float(nav.iloc[i] / peak_nav - 1.0)
        force_defense = False
        if current_drawdown <= config.drawdown_stop:
            cooldown_left = max(cooldown_left, config.cooldown_days)
            force_defense = True
        elif cooldown_left > 0:
            force_defense = True

        if date in rebalance_dates or i == 0 or force_defense:
            if force_defense:
                raw_target = {config.defense_asset: 1.0}
            else:
                raw_target = build_satellite_target_weights(prices, date, config, returns=returns)
                scale = drawdown_scale(current_drawdown, config)
                raw_target = _apply_scale_to_defense(raw_target, scale, config.defense_asset)
            target_weights = normalize_weights(raw_target, columns)
            turnover = float((target_weights - current_weights).abs().sum())
            if turnover > 1e-12:
                nav.iloc[i] = nav.iloc[i] * (1 - turnover * config.cost_rate)
                total_turnover += turnover
                rebalance_count += 1
            current_weights = target_weights

        weights_history.loc[date] = current_weights
        if cooldown_left > 0:
            cooldown_left -= 1
            cooldown_days_total += 1
        if i + 1 < len(eval_prices.index):
            next_date = eval_prices.index[i + 1]
            daily_return = float((current_weights * returns.loc[next_date, columns]).sum())
            nav.iloc[i + 1] = nav.iloc[i] * (1 + daily_return)

    risk_columns = [symbol for symbol in config.risk_assets if symbol in weights_history.columns]
    stats = {
        "total_turnover": total_turnover,
        "rebalance_count": float(rebalance_count),
        "estimated_cost": total_turnover * config.cost_rate,
        "cooldown_days": float(cooldown_days_total),
        "risk_asset_exposure": float(weights_history[risk_columns].sum(axis=1).mean()) if risk_columns else 0.0,
    }
    return nav, weights_history, stats


def run_satellite_config(
    prices: pd.DataFrame,
    config: SatelliteConfig,
    eval_start: str,
    eval_end: str,
) -> dict[str, float | str | bool]:
    nav, _weights, run_stats = run_satellite_nav(prices, config, eval_start, eval_end)
    row: dict[str, float | str | bool] = {"strategy": config.name}
    row.update(calculate_metrics(nav))
    row.update(run_stats)
    row.update(
        {
            "top_n": config.top_n,
            "strategy_family": "ram_trend",
            "fixed_weights": "",
            "gross_exposure": 1.0,
            "momentum_window": config.momentum_window,
            "volatility_window": config.volatility_window,
            "trend_window": config.trend_window,
            "rebalance_interval": config.rebalance_interval,
            "cost_rate": config.cost_rate,
            "drawdown_half": config.drawdown_half,
            "drawdown_quarter": config.drawdown_quarter,
            "drawdown_stop": config.drawdown_stop,
            "passes_return_gate": row["annual_return"] >= SATELLITE_TARGET_ANNUAL_RETURN,
            "passes_drawdown_gate": row["max_drawdown"] >= SATELLITE_MAX_DRAWDOWN_FLOOR,
        }
    )
    return row


def run_fixed_blend_config(
    prices: pd.DataFrame,
    name: str,
    weights: dict[str, float],
    gross_exposure: float,
    eval_start: str,
    eval_end: str,
    defense_asset: str = SATELLITE_DEFENSE_ASSET,
    rebalance_interval: int = 21,
    cost_rate: float = COST_RATE,
    borrow_rate: float = 0.04,
    drawdown_half: float = -0.15,
    drawdown_quarter: float = -0.22,
    drawdown_stop: float = SATELLITE_MAX_DRAWDOWN_FLOOR,
    cooldown_days: int = 21,
) -> dict[str, float | str | bool]:
    eval_prices = prices.loc[eval_start:eval_end]
    if eval_prices.empty:
        raise ValueError(f"No prices in evaluation range {eval_start} to {eval_end}")

    columns = list(prices.columns)
    returns = prices.pct_change().fillna(0.0)
    nav = pd.Series(index=eval_prices.index, dtype=float)
    nav.iloc[0] = 1.0
    current_weights = pd.Series(0.0, index=columns)
    rebalance_dates = set(eval_prices.index[::rebalance_interval])
    peak_nav = 1.0
    cooldown_left = 0
    cooldown_days_total = 0
    total_turnover = 0.0
    rebalance_count = 0

    base = normalize_weights(weights, columns)
    base.loc[defense_asset] = 0.0
    risk_total = float(base.sum())
    if risk_total <= 0:
        base.loc[defense_asset] = 1.0
    else:
        base = base / risk_total
        base.loc[defense_asset] = 0.0

    for i, date in enumerate(eval_prices.index):
        peak_nav = max(peak_nav, float(nav.iloc[i]))
        current_drawdown = float(nav.iloc[i] / peak_nav - 1.0)
        force_defense = False
        if current_drawdown <= drawdown_stop:
            cooldown_left = max(cooldown_left, cooldown_days)
            force_defense = True
        elif cooldown_left > 0:
            force_defense = True

        if date in rebalance_dates or i == 0 or force_defense:
            if force_defense:
                target_weights = pd.Series(0.0, index=columns)
                target_weights.loc[defense_asset] = 1.0
            else:
                scale_config = SatelliteConfig(
                    name=name,
                    drawdown_half=drawdown_half,
                    drawdown_quarter=drawdown_quarter,
                    drawdown_stop=drawdown_stop,
                    cooldown_days=cooldown_days,
                )
                exposure = gross_exposure * drawdown_scale(current_drawdown, scale_config)
                target_weights = base * exposure
                if exposure < 1.0:
                    target_weights.loc[defense_asset] = 1.0 - exposure

            turnover = float((target_weights - current_weights).abs().sum())
            if turnover > 1e-12:
                nav.iloc[i] = nav.iloc[i] * (1 - turnover * cost_rate)
                total_turnover += turnover
                rebalance_count += 1
            current_weights = target_weights

        if cooldown_left > 0:
            cooldown_left -= 1
            cooldown_days_total += 1
        if i + 1 < len(eval_prices.index):
            next_date = eval_prices.index[i + 1]
            gross = float(current_weights.drop(labels=[defense_asset], errors="ignore").abs().sum())
            borrow_cost = max(0.0, gross - 1.0) * borrow_rate / TRADING_DAYS
            daily_return = float((current_weights * returns.loc[next_date, columns]).sum()) - borrow_cost
            nav.iloc[i + 1] = nav.iloc[i] * (1 + daily_return)

    row: dict[str, float | str | bool] = {"strategy": name}
    row.update(calculate_metrics(nav))
    row.update(
        {
            "strategy_family": "fixed_blend",
            "fixed_weights": ";".join(f"{symbol}:{weight:.2f}" for symbol, weight in weights.items()),
            "gross_exposure": gross_exposure,
            "top_n": "",
            "momentum_window": "",
            "volatility_window": "",
            "trend_window": "",
            "rebalance_interval": rebalance_interval,
            "cost_rate": cost_rate,
            "drawdown_half": drawdown_half,
            "drawdown_quarter": drawdown_quarter,
            "drawdown_stop": drawdown_stop,
            "total_turnover": total_turnover,
            "rebalance_count": float(rebalance_count),
            "estimated_cost": total_turnover * cost_rate,
            "cooldown_days": float(cooldown_days_total),
            "risk_asset_exposure": gross_exposure,
        }
    )
    row["passes_return_gate"] = row["annual_return"] >= SATELLITE_TARGET_ANNUAL_RETURN
    row["passes_drawdown_gate"] = row["max_drawdown"] >= SATELLITE_MAX_DRAWDOWN_FLOOR
    return row


def fixed_blend_configs() -> list[dict[str, object]]:
    blends = [
        ("fixed_513100_518880_50_50", {"513100": 0.50, "518880": 0.50}),
        ("fixed_513100_518880_60_40", {"513100": 0.60, "518880": 0.40}),
        ("fixed_513100_518880_512480_40_40_20", {"513100": 0.40, "518880": 0.40, "512480": 0.20}),
        ("fixed_513100_518880_512480_35_35_30", {"513100": 0.35, "518880": 0.35, "512480": 0.30}),
        ("fixed_518880", {"518880": 1.00}),
    ]
    configs: list[dict[str, object]] = []
    for base_name, weights in blends:
        for exposure in [1.0, 1.5, 2.0, 2.5, 3.0]:
            configs.append(
                {
                    "name": f"{base_name}_x{str(exposure).replace('.', '_')}",
                    "weights": weights,
                    "gross_exposure": exposure,
                }
            )
    return configs


def compute_asset_diagnostics(prices: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, float | str]] = []
    for symbol in prices.columns:
        series = prices[symbol].dropna()
        nav = series / series.iloc[0]
        stats = calculate_metrics(nav)
        returns = series.pct_change().dropna()
        rows.append(
            {
                "symbol": symbol,
                "start": str(nav.index.min().date()),
                "end": str(nav.index.max().date()),
                "observations": float(len(nav)),
                "annual_return": stats["annual_return"],
                "annual_volatility": stats["annual_volatility"],
                "max_drawdown": stats["max_drawdown"],
                "calmar": stats["calmar"],
                "worst_daily_return": float(returns.min()) if len(returns) else 0.0,
            }
        )
    return pd.DataFrame(rows).sort_values(["annual_return", "calmar"], ascending=False)


def select_satellite_candidate(scan_df: pd.DataFrame) -> tuple[pd.Series, dict[str, bool | str]]:
    passing = scan_df[(scan_df["passes_return_gate"]) & (scan_df["passes_drawdown_gate"])].copy()
    if passing.empty:
        drawdown_passing = scan_df[scan_df["passes_drawdown_gate"]].copy()
        if drawdown_passing.empty:
            ranked = scan_df.sort_values(["max_drawdown", "annual_return", "calmar"], ascending=False)
        else:
            ranked = drawdown_passing.sort_values(["annual_return", "calmar"], ascending=False)
        gate = {
            "has_passing_candidate": False,
            "message": "没有候选同时通过 50% 年化和 -30% 最大回撤门槛；以下为先过回撤闸门、再按年化收益排序的 near-miss。",
        }
        return ranked.iloc[0], gate
    ranked = passing.sort_values(["calmar", "annual_return"], ascending=False)
    gate = {
        "has_passing_candidate": True,
        "message": "存在候选同时通过 50% 年化和 -30% 最大回撤门槛；以下为当前卫星候选。",
    }
    return ranked.iloc[0], gate


def _pct(value: float) -> str:
    return f"{value * 100:.2f}%"


def build_satellite_final_markdown(
    candidate: pd.Series,
    gate: dict[str, bool | str],
    scan_df: pd.DataFrame,
) -> str:
    top = scan_df.sort_values(["passes_drawdown_gate", "annual_return", "calmar"], ascending=False).head(10)
    lines = [
        "# Satellite 50% DD30 Candidate",
        "",
        f"- Gate: {gate['message']}",
        "- Target annual return: `50%+`.",
        "- Maximum drawdown floor: `-30%`.",
        f"- Candidate: `{candidate['strategy']}`",
        f"- Annual return: `{_pct(float(candidate['annual_return']))}`",
        f"- Max drawdown: `{_pct(float(candidate['max_drawdown']))}`",
        f"- Calmar: `{float(candidate['calmar']):.2f}`",
        f"- Estimated turnover cost drag: `{_pct(float(candidate['estimated_cost']))}`",
        f"- Rebalance count: `{int(candidate['rebalance_count'])}`",
        f"- Cooldown days: `{int(candidate['cooldown_days'])}`",
        f"- Average risk-asset exposure: `{_pct(float(candidate['risk_asset_exposure']))}`",
        "",
        "This is a satellite-sleeve research result, not a full-portfolio strategy and not an execution instruction.",
        "",
        "## Top 10 Candidates",
        "",
        markdown_table(
            top[
                [
                    "strategy",
                    "annual_return",
                    "max_drawdown",
                    "calmar",
                    "passes_return_gate",
                    "passes_drawdown_gate",
                    "estimated_cost",
                ]
            ],
            index=False,
            floatfmt=".4f",
        ),
    ]
    return "\n".join(lines) + "\n"


def satellite_config_grid(cost_rate: float = COST_RATE) -> list[SatelliteConfig]:
    configs: list[SatelliteConfig] = []
    for top_n in [1, 2]:
        for momentum_window in [10, 20, 60, 120]:
            for volatility_window in [10, 20, 60]:
                for trend_window in [60, 120, 200]:
                    for rebalance_interval in [5, 10, 21]:
                        configs.append(
                            SatelliteConfig(
                                name=(
                                    f"sat_top{top_n}_m{momentum_window}_v{volatility_window}"
                                    f"_t{trend_window}_f{rebalance_interval}_dd30"
                                ),
                                top_n=top_n,
                                momentum_window=momentum_window,
                                volatility_window=volatility_window,
                                trend_window=trend_window,
                                rebalance_interval=rebalance_interval,
                                cost_rate=cost_rate,
                            )
                        )
    return configs
