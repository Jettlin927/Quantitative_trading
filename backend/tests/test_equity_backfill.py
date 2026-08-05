from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
import unittest

from scripts.ops.backfill_equity_history import (
    build_equity_snapshots,
    et_trading_days,
)
from backend.app.personal_workspace.portfolio import HoldingState


class EquityBackfillTest(unittest.TestCase):
    def test_et_trading_days_returns_weekdays_only(self) -> None:
        # 2026-08-04 是周二；回推 7 个工作日应跨过周末
        days = et_trading_days(date(2026, 8, 4), 7)
        self.assertEqual(
            [item.isoformat() for item in days],
            [
                "2026-07-27",
                "2026-07-28",
                "2026-07-29",
                "2026-07-30",
                "2026-07-31",
                "2026-08-03",
                "2026-08-04",
            ],
        )
        self.assertTrue(all(item.weekday() < 5 for item in days))

    def test_build_equity_snapshots_computes_equity_and_skips_missing_days(self) -> None:
        holdings = (
            HoldingState(
                holding_id="h1",
                symbol="ACME",
                name="Acme",
                quantity=Decimal("2"),
                average_cost=Decimal("100"),
            ),
            HoldingState(
                holding_id="h2",
                symbol="BETA",
                name="Beta",
                quantity=Decimal("3"),
                average_cost=Decimal("50"),
            ),
        )
        days = et_trading_days(date(2026, 8, 4), 3)
        closes = {
            ("ACME", date(2026, 8, 4)): Decimal("120"),
            ("BETA", date(2026, 8, 4)): Decimal("60"),
            ("ACME", date(2026, 8, 3)): Decimal("110"),
            # BETA 缺 8/3 → 该日整体跳过
        }
        observed_at = datetime(2026, 8, 5, 2, 0, tzinfo=timezone.utc)
        snapshots = build_equity_snapshots(
            holdings=holdings,
            usd_cash=Decimal("10"),
            trading_days=days,
            close_provider=lambda symbol, day: closes.get((symbol, day)),
            observed_at=observed_at,
        )
        self.assertEqual(len(snapshots), 1)
        item = snapshots[0]
        self.assertEqual(item.market_day, date(2026, 8, 4))
        # 2*120 + 3*60 + 10 = 430
        self.assertEqual(item.total_equity, Decimal("430"))
        self.assertEqual(item.total_market_value, Decimal("420"))
        self.assertEqual(item.usd_cash, Decimal("10"))
        self.assertEqual(item.holdings_count, 2)
        self.assertEqual(item.priced_count, 2)
        self.assertTrue(item.after_close)
        self.assertEqual(item.payload["prices"]["ACME"]["price"], "120")

    def test_build_equity_snapshots_empty_holdings(self) -> None:
        days = et_trading_days(date(2026, 8, 4), 2)
        snapshots = build_equity_snapshots(
            holdings=(),
            usd_cash=Decimal("10"),
            trading_days=days,
            close_provider=lambda symbol, day: Decimal("1"),
            observed_at=datetime(2026, 8, 5, 2, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(snapshots, ())


if __name__ == "__main__":
    unittest.main()
