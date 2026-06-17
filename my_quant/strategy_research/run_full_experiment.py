from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from my_quant.strategy_research.experiment.pipeline import main


if __name__ == "__main__":
    main()
