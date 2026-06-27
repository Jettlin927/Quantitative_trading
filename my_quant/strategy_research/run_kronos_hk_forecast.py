#!/usr/bin/env python3
"""Forecast HK-listed BYD and Xiaomi daily prices with an external Kronos checkout."""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import json
import math
import os
import sys
from dataclasses import dataclass
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import yfinance as yf


RESEARCH_ROOT = Path(__file__).resolve().parent


DEFAULT_TICKERS = {
    "1211.HK": "BYD H",
    "1810.HK": "Xiaomi",
}


@dataclass
class ForecastBundle:
    ticker: str
    name: str
    history: pd.DataFrame
    paths: pd.DataFrame
    stats: pd.DataFrame
    metrics: dict[str, float | str]
    chart_path: Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lookback", type=int, default=400)
    parser.add_argument("--pred-len", type=int, default=60)
    parser.add_argument("--samples", type=int, default=8)
    parser.add_argument("--model", default="NeoQuasar/Kronos-mini")
    parser.add_argument("--tokenizer", default="NeoQuasar/Kronos-Tokenizer-2k")
    parser.add_argument("--device", default=None)
    parser.add_argument(
        "--kronos-dir",
        default=None,
        help="Path to an external Kronos checkout. Defaults to KRONOS_DIR or common local locations.",
    )
    parser.add_argument("--out-dir", default=str(RESEARCH_ROOT / "web_report" / "kronos_hk_forecast"))
    return parser.parse_args()


def resolve_kronos_dir(raw_path: str | None = None) -> Path:
    candidates: list[Path] = []
    if raw_path:
        candidates.append(Path(raw_path).expanduser())
    env_path = os.environ.get("KRONOS_DIR")
    if env_path:
        candidates.append(Path(env_path).expanduser())
    candidates.extend(
        [
            RESEARCH_ROOT / "external" / "Kronos",
            Path.home() / "Documents" / "kronos-预测" / "Kronos",
        ]
    )
    for candidate in candidates:
        if (candidate / "model" / "__init__.py").exists():
            return candidate
    checked = ", ".join(str(candidate) for candidate in candidates)
    raise RuntimeError(f"Kronos checkout not found. Pass --kronos-dir. Checked: {checked}")


def load_kronos_classes(kronos_dir: Path):
    if str(kronos_dir) not in sys.path:
        sys.path.insert(0, str(kronos_dir))
    from model import Kronos, KronosPredictor, KronosTokenizer

    return Kronos, KronosPredictor, KronosTokenizer


def flatten_yf_columns(df: pd.DataFrame) -> pd.DataFrame:
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [col[0] for col in df.columns]
    return df


def load_history(ticker: str, lookback: int) -> pd.DataFrame:
    raw = yf.download(
        ticker,
        period="3y",
        interval="1d",
        auto_adjust=False,
        progress=False,
        threads=False,
    )
    raw = flatten_yf_columns(raw)
    if raw.empty:
        raise RuntimeError(f"No data downloaded for {ticker}")

    raw = raw.rename(
        columns={
            "Open": "open",
            "High": "high",
            "Low": "low",
            "Close": "close",
            "Volume": "volume",
        }
    )
    raw.index = pd.to_datetime(raw.index)
    raw = raw.reset_index().rename(columns={"Date": "date"})
    raw["amount"] = raw["close"] * raw["volume"]
    cols = ["date", "open", "high", "low", "close", "volume", "amount"]
    raw = raw[cols].dropna().sort_values("date").reset_index(drop=True)
    if len(raw) < lookback:
        raise RuntimeError(f"{ticker} has only {len(raw)} rows, need {lookback}")
    return raw


def future_business_days(last_date: pd.Timestamp, pred_len: int) -> pd.Series:
    return pd.Series(pd.bdate_range(start=last_date + pd.Timedelta(days=1), periods=pred_len))


def make_predictor(args: argparse.Namespace) -> KronosPredictor:
    kronos_dir = resolve_kronos_dir(args.kronos_dir)
    Kronos, KronosPredictor, KronosTokenizer = load_kronos_classes(kronos_dir)
    tokenizer = KronosTokenizer.from_pretrained(args.tokenizer)
    model = Kronos.from_pretrained(args.model)
    return KronosPredictor(model, tokenizer, device=args.device, max_context=max(args.lookback, 512))


def predict_paths(
    predictor: KronosPredictor,
    histories: dict[str, pd.DataFrame],
    tickers: list[str],
    args: argparse.Namespace,
) -> dict[str, pd.DataFrame]:
    import torch

    prepared = []
    x_timestamps = []
    y_timestamps = []

    for ticker in tickers:
        df = histories[ticker]
        x_df = df.iloc[-args.lookback:][["open", "high", "low", "close", "volume", "amount"]]
        prepared.append(x_df)
        x_timestamps.append(pd.Series(df.iloc[-args.lookback:]["date"]))
        y_timestamps.append(future_business_days(df["date"].iloc[-1], args.pred_len))

    by_ticker: dict[str, list[pd.Series]] = {ticker: [] for ticker in tickers}
    for seed in range(args.samples):
        torch.manual_seed(seed)
        np.random.seed(seed)
        pred_list = predictor.predict_batch(
            df_list=prepared,
            x_timestamp_list=x_timestamps,
            y_timestamp_list=y_timestamps,
            pred_len=args.pred_len,
            T=1.0,
            top_p=0.9,
            sample_count=1,
            verbose=False,
        )
        for ticker, pred_df in zip(tickers, pred_list):
            by_ticker[ticker].append(pred_df["close"].rename(f"path_{seed:02d}"))

    return {ticker: pd.concat(paths, axis=1) for ticker, paths in by_ticker.items()}


def summarize_paths(paths: pd.DataFrame) -> pd.DataFrame:
    out = pd.DataFrame(index=paths.index)
    out["p10"] = paths.quantile(0.10, axis=1)
    out["p25"] = paths.quantile(0.25, axis=1)
    out["median"] = paths.median(axis=1)
    out["p75"] = paths.quantile(0.75, axis=1)
    out["p90"] = paths.quantile(0.90, axis=1)
    out["mean"] = paths.mean(axis=1)
    return out


def horizon_row(stats: pd.DataFrame, last_close: float, horizon: int) -> dict[str, float]:
    pos = min(horizon, len(stats)) - 1
    row = stats.iloc[pos]
    return {
        "days": pos + 1,
        "median_close": float(row["median"]),
        "median_return": float(row["median"] / last_close - 1.0),
        "p10_return": float(row["p10"] / last_close - 1.0),
        "p90_return": float(row["p90"] / last_close - 1.0),
    }


def model_label(model_name: str) -> str:
    return model_name.rsplit("/", 1)[-1]


def draw_chart(ticker: str, name: str, history: pd.DataFrame, stats: pd.DataFrame, out_dir: Path, model_name: str) -> Path:
    recent = history.tail(120)
    fig, ax = plt.subplots(figsize=(11, 5.8))
    ax.plot(recent["date"], recent["close"], color="#2f5d8c", linewidth=1.6, label="History")
    ax.plot(stats.index, stats["median"], color="#c23b22", linewidth=1.8, label="Kronos median")
    ax.fill_between(stats.index, stats["p25"], stats["p75"], color="#e39a83", alpha=0.28, label="P25-P75")
    ax.fill_between(stats.index, stats["p10"], stats["p90"], color="#e39a83", alpha=0.13, label="P10-P90")
    ax.axhline(history["close"].iloc[-1], color="#666666", linewidth=0.8, linestyle="--")
    ax.set_title(f"{name} ({ticker}) {model_label(model_name)} daily forecast")
    ax.set_ylabel("HKD")
    ax.grid(True, alpha=0.25)
    ax.legend(loc="best")
    fig.autofmt_xdate()
    fig.tight_layout()
    path = out_dir / f"{ticker.replace('.', '_')}_forecast.png"
    fig.savefig(path, dpi=160)
    plt.close(fig)
    return path


def build_metrics(stats: pd.DataFrame, history: pd.DataFrame) -> dict[str, float | str]:
    last_close = float(history["close"].iloc[-1])
    horizons = {f"h{h}": horizon_row(stats, last_close, h) for h in (5, 20, 60)}
    ma20 = float(history["close"].tail(20).mean())
    ma60 = float(history["close"].tail(60).mean())
    vol20 = float(history["close"].pct_change().tail(20).std() * math.sqrt(252))
    m = {
        "last_date": history["date"].iloc[-1].strftime("%Y-%m-%d"),
        "last_close": last_close,
        "ma20": ma20,
        "ma60": ma60,
        "vol20_ann": vol20,
    }
    for key, row in horizons.items():
        for sub_key, value in row.items():
            m[f"{key}_{sub_key}"] = value
    return m


def pct(x: float) -> str:
    return f"{x * 100:+.1f}%"


def money(x: float) -> str:
    return f"{x:.2f}"


def image_data_uri(path: Path) -> str:
    data = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:image/png;base64,{data}"


def write_report(bundles: list[ForecastBundle], args: argparse.Namespace, out_dir: Path) -> Path:
    created_at = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    rows = []
    cards = []
    for b in bundles:
        m = b.metrics
        rows.append(
            "<tr>"
            f"<td>{b.name}</td><td>{b.ticker}</td><td>{m['last_date']}</td>"
            f"<td>{money(float(m['last_close']))}</td>"
            f"<td>{pct(float(m['h5_median_return']))}</td>"
            f"<td>{pct(float(m['h20_median_return']))}</td>"
            f"<td>{pct(float(m['h60_median_return']))}</td>"
            f"<td>{pct(float(m['h20_p10_return']))} / {pct(float(m['h20_p90_return']))}</td>"
            "</tr>"
        )
        cards.append(
            f"""
            <section>
              <h2>{b.name} ({b.ticker})</h2>
              <img src="{image_data_uri(b.chart_path)}" alt="{b.ticker} forecast chart" />
              <p>Last close: <b>{money(float(m['last_close']))}</b> on {m['last_date']}.
              20-day median path: <b>{pct(float(m['h20_median_return']))}</b>;
              20-day P10/P90 range: <b>{pct(float(m['h20_p10_return']))}</b> to <b>{pct(float(m['h20_p90_return']))}</b>.
              20-day annualized realized volatility: <b>{pct(float(m['vol20_ann']))}</b>.</p>
            </section>
            """
        )

    html = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <title>Kronos HK Forecast</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 32px; color: #17202a; }}
    h1 {{ margin-bottom: 4px; }}
    .meta {{ color: #5f6c7b; margin-top: 0; }}
    table {{ border-collapse: collapse; width: 100%; margin: 24px 0; }}
    th, td {{ border-bottom: 1px solid #dde3ea; padding: 10px 8px; text-align: right; }}
    th:first-child, td:first-child, th:nth-child(2), td:nth-child(2), th:nth-child(3), td:nth-child(3) {{ text-align: left; }}
    section {{ margin: 28px 0 38px; }}
    img {{ max-width: 100%; border: 1px solid #dde3ea; }}
    .note {{ background: #f4f7fa; padding: 14px 16px; border-left: 4px solid #2f5d8c; }}
  </style>
</head>
<body>
  <h1>{model_label(args.model)} 港股日线预测</h1>
  <p class="meta">Generated at {created_at}; lookback={args.lookback}, pred_len={args.pred_len}, samples={args.samples}, model={args.model}.</p>
  <table>
    <thead>
      <tr>
        <th>名称</th><th>Ticker</th><th>最后数据日</th><th>收盘</th><th>5日中位数</th><th>20日中位数</th><th>60日中位数</th><th>20日P10/P90</th>
      </tr>
    </thead>
    <tbody>
      {''.join(rows)}
    </tbody>
  </table>
  {''.join(cards)}
  <p class="note">模型只看K线形态和成交量，不读取新闻、估值、财报、港股通资金或宏观变量。预测是采样路径的统计摘要，不是保证收益。</p>
</body>
</html>
"""
    path = out_dir / "kronos_hk_forecast.html"
    path.write_text(html, encoding="utf-8")
    return path


def main() -> None:
    args = parse_args()
    kronos_dir = resolve_kronos_dir(args.kronos_dir)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    tickers = list(DEFAULT_TICKERS)
    histories = {ticker: load_history(ticker, args.lookback) for ticker in tickers}
    predictor = make_predictor(args)
    all_paths = predict_paths(predictor, histories, tickers, args)

    bundles: list[ForecastBundle] = []
    summary_rows = []
    for ticker in tickers:
        name = DEFAULT_TICKERS[ticker]
        paths = all_paths[ticker]
        stats = summarize_paths(paths)
        history = histories[ticker]
        chart_path = draw_chart(ticker, name, history, stats, out_dir, args.model)
        metrics = build_metrics(stats, history)

        safe = ticker.replace(".", "_")
        history.to_csv(out_dir / f"{safe}_history.csv", index=False)
        paths.to_csv(out_dir / f"{safe}_forecast_paths.csv", index_label="date")
        stats.to_csv(out_dir / f"{safe}_forecast_stats.csv", index_label="date")

        row = {"ticker": ticker, "name": name, **metrics}
        summary_rows.append(row)
        bundles.append(ForecastBundle(ticker, name, history, paths, stats, metrics, chart_path))

    pd.DataFrame(summary_rows).to_csv(out_dir / "summary.csv", index=False)
    report_path = write_report(bundles, args, out_dir)
    manifest = {
        "created_at": dt.datetime.now().isoformat(timespec="seconds"),
        "model": args.model,
        "tokenizer": args.tokenizer,
        "lookback": args.lookback,
        "pred_len": args.pred_len,
        "samples": args.samples,
        "kronos_dir": str(kronos_dir),
        "report": str(report_path),
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(report_path)


if __name__ == "__main__":
    main()
