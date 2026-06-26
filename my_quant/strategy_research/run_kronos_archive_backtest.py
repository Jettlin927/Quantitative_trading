from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from my_quant.strategy_research.experiment.kronos_archive_backtest import (
    run_prediction_archive_dir,
    write_archive_outputs,
)


DEFAULT_PREDICTION_DIR = Path.home() / "Documents" / "kronos-预测" / "Kronos" / "webui" / "prediction_results"
DEFAULT_OUTPUT_DIR = Path("docs/research/backtest-reports/kronos-archive-backtest-2026-06-26")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backtest archived Kronos webui prediction JSON files.")
    parser.add_argument("--prediction-dir", type=Path, default=DEFAULT_PREDICTION_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    rows, summary = run_prediction_archive_dir(args.prediction_dir)
    summary["prediction_dir"] = str(args.prediction_dir)
    summary["scope_note"] = "external Kronos webui crypto futures 5m archive; not HK equity validation"
    paths = write_archive_outputs(args.output_dir, rows, summary)
    print(json.dumps({"summary": summary, "paths": paths}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
