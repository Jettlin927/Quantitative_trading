# B1 Tushare Data Source Smoke

## Purpose

Validate that the B1 A-share trend-pullback experiment can use Tushare as a stock and market-index data provider before running a larger walk-forward scan.

## Environment Check

- `TUSHARE_TOKEN`: present in the local environment during smoke validation.
- `tushare` package: available in the system Python used for the smoke run.
- `.venv` note: the project virtualenv used for unit tests does not currently include `tushare`; install it there or use an interpreter that has the package before running Tushare backtests.

## Data Interfaces

- Universe: `stock_basic`, filtered through the local eligible A-share rules.
- Stock bars: `pro_bar` with `adj=qfq`.
- Market filter: `index_daily` for `000300.SH`.
- Cache: normalized CSV files with `tushare` in the filename.

## Smoke Commands

```bash
python3 - <<'PY'
from pathlib import Path
from my_quant.strategy_research.experiment.b1_trend_pullback import fetch_a_share_bars_tushare, load_a_share_symbols_tushare

symbols = load_a_share_symbols_tushare(limit=5)
print("symbols", symbols)
bars = fetch_a_share_bars_tushare("000001", "20250101", "20250110", Path("/tmp/xquant_tushare_smoke"), refresh=True)
print("rows", len(bars))
print("columns", ",".join(bars.columns))
print("first_date", bars.index.min().date())
print("last_date", bars.index.max().date())
PY
```

```bash
python3 -m my_quant.strategy_research.run_b1_trend_pullback \
  --data-provider tushare \
  --max-symbols 5 \
  --history-start 2024-06-01 \
  --start 2025-01-01 \
  --end 2025-01-31 \
  --data-dir /tmp/xquant_tushare_cli_smoke \
  --results-dir /tmp/xquant_tushare_cli_smoke_results \
  --output-prefix b1_tushare_smoke
```

## Observed Output

- Universe smoke returned symbols: `000001`, `000002`, `000006`, `000007`, `000008`.
- `000001` bars returned `7` rows from `2025-01-02` to `2025-01-10`.
- Normalized columns: `open`, `high`, `low`, `close`, `volume`, `amount`.
- CLI smoke completed with `5` loaded symbols and wrote `/tmp/xquant_tushare_cli_smoke_results/b1_tushare_smoke_summary.md`.

## Interpretation

This validates the data-source plumbing only. It does not validate the strategy target. The active goal still requires a Tushare-backed full sample, 2026 out-of-sample check, and walk-forward validation with annual return `>= 50%` and max drawdown no worse than `-30%`.
