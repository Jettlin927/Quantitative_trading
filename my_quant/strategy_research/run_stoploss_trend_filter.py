from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from my_quant.strategy_research.experiment.stoploss_trend_filter import write_stoploss_report


DEFAULT_OUTPUT_DIR = Path("docs/research/backtest-reports/stoploss-trend-filter-2026-06-26")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build stoploss/trend-filter report for strategy 05.")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    paths = write_stoploss_report(args.output_dir)
    print(
        json.dumps(
            {
                "output_dir": str(paths.output_dir),
                "summary_csv": str(paths.summary_csv),
                "nav_csv": str(paths.nav_csv),
                "report_html": str(paths.report_html),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
