from __future__ import annotations

import unittest
from datetime import date

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app import main
from backend.app.database import Base
from backend.app.models import Stock, StockDailyBar


class DataApiContractTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)

        with self.Session() as db:
            db.add(Stock(ts_code="600703.SH", symbol="600703", name="三安光电", area="湖北", industry="半导体", market="主板", list_date=date(1996, 5, 28)))
            db.add(
                StockDailyBar(
                    ts_code="600703.SH",
                    trade_date=date(2026, 5, 29),
                    open=12.1,
                    high=12.4,
                    low=12.0,
                    close=12.3,
                    pct_chg=1.23,
                    vol=1000,
                    amount=1200,
                )
            )
            db.commit()

    def open_session(self):
        return self.Session()

    def tearDown(self):
        main.app.dependency_overrides.clear()

    def test_health_and_db_overview_are_data_only(self):
        with self.open_session() as db:
            health = main.health(db)
            overview = main.get_db_overview(db)

        self.assertEqual(health["service"], "quant-data-workspace")
        payload = overview
        self.assertEqual(payload["aShare"]["stocks"], 1)
        self.assertEqual(payload["aShare"]["dailyBars"]["maxDate"], "2026-05-29")

    def test_stock_queries_return_raw_db_data(self):
        with self.open_session() as db:
            stocks = main.list_stocks(q="三安", db=db)
            screen = main.screen_stocks(q="600703", db=db)
            bars = main.get_daily_bars("600703.SH", date(2026, 5, 1), date(2026, 5, 31), db)

        self.assertEqual(stocks[0].ts_code, "600703.SH")
        self.assertEqual(screen[0].close, 12.3)
        self.assertFalse(hasattr(screen[0], "signal_summary"))
        self.assertEqual(bars[0].trade_date, date(2026, 5, 29))

    def test_strategy_and_backtest_routes_are_gone(self):
        paths = {route.path for route in main.app.routes}
        self.assertNotIn("/api/strategy-evaluations", paths)
        self.assertNotIn("/api/strategies/executable/cross-section-strength-risk8", paths)
        self.assertNotIn("/api/backtests/run", paths)


if __name__ == "__main__":
    unittest.main()
