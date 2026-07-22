from __future__ import annotations

import argparse
from pathlib import Path

from my_quant.us_holdings.broker_ledger import update_ledger_from_input


DEFAULT_PRIVATE_ROOT = Path("outputs/private/us_hsbc")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Append body-confirmed HSBC Gmail fills to a private local CSV ledger."
    )
    parser.add_argument("--input", required=True, help="JSONL or CSV file with confirmed Gmail trade fields.")
    parser.add_argument("--ledger", default=str(DEFAULT_PRIVATE_ROOT / "hsbc_executed_trades.csv"))
    parser.add_argument("--holdings", default=str(DEFAULT_PRIVATE_ROOT / "hsbc_current_holdings.csv"))
    parser.add_argument("--html", default=str(DEFAULT_PRIVATE_ROOT / "holdings.html"))
    args = parser.parse_args()

    result = update_ledger_from_input(
        input_path=Path(args.input),
        ledger_path=Path(args.ledger),
        holdings_path=Path(args.holdings),
        html_path=Path(args.html) if args.html else None,
    )
    print(
        "ledger_updated "
        f"input={result['input_count']} "
        f"added={result['added_count']} "
        f"ledger_rows={result['ledger_count']} "
        f"holdings={result['holding_count']} "
        f"ledger={result['ledger_path']} "
        f"holdings_csv={result['holdings_path']} "
        f"html={result['html_path']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
