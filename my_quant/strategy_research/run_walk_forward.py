from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from my_quant.strategy_research.experiment.config import ExperimentPaths, scan_configs
from my_quant.strategy_research.experiment.data import load_prices
from my_quant.strategy_research.experiment.validation import walk_forward_analysis


def main() -> None:
    paths = ExperimentPaths()
    prices = load_prices(data_dir=paths.data_dir)
    result = walk_forward_analysis(prices, scan_configs())
    paths.results_dir.mkdir(parents=True, exist_ok=True)
    result.to_csv(paths.results_dir / "walk_forward_full_summary.csv", index=False)
    print(result.tail(20).to_string(index=False))


if __name__ == "__main__":
    main()
