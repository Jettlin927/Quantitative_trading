from __future__ import annotations

import unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import create_engine, func, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import sessionmaker

from backend.app import main
from backend.app.database import Base
from backend.app.models import FundAdjustFactor, StockLimitPrice, StockListing, StockSuspendEvent


class ResearchDataContractTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)

    def test_p1_tables_mappers_and_routes_exist(self):
        for table_name in ["stock_listings", "stock_limit_prices", "stock_suspend_events", "fund_adjust_factors"]:
            self.assertIn(table_name, Base.metadata.tables)

        listing = main.stock_listing_record_to_row(
            {"ts_code": "000001.SZ", "symbol": "000001", "name": "平安银行", "list_status": "L", "list_date": "19910403"}
        )
        limit_price = main.stock_limit_price_record_to_row(
            {"ts_code": "000001.SZ", "trade_date": "20260709", "pre_close": 10, "up_limit": 11, "down_limit": 9}
        )
        suspend = main.stock_suspend_event_record_to_row(
            {"ts_code": "000001.SZ", "trade_date": "20260709", "suspend_type": "S", "suspend_timing": None}
        )

        self.assertEqual(listing["list_date"], date(1991, 4, 3))
        self.assertEqual(limit_price["up_limit"], 11)
        self.assertEqual(suspend["suspend_timing"], "")

        paths = {route.path for route in main.app.routes}
        for path in [
            "/api/tushare/sync-stock-listings",
            "/api/tushare/sync-market-limit-prices",
            "/api/tushare/sync-market-suspend-events",
            "/api/tushare/sync-fund-adjust-factors",
            "/api/stock-listings",
            "/api/stocks/{ts_code}/limit-prices",
            "/api/stocks/{ts_code}/suspend-events",
            "/api/funds/{ts_code}/adjust-factors",
            "/api/research/readiness",
        ]:
            self.assertIn(path, paths)

    def test_p1_sync_is_idempotent_and_queryable(self):
        market_payload = SimpleNamespace(
            start_date=date(2026, 7, 9),
            end_date=date(2026, 7, 9),
            token=None,
            skip_existing=False,
            min_existing_rows=1,
            max_trade_dates=0,
        )
        suspend_payload = SimpleNamespace(start_date=date(2026, 7, 9), end_date=date(2026, 7, 9), token=None, max_trade_dates=0)

        with self.Session() as db, patch.object(main, "get_pro_api", return_value=FakeTushare()):
            for _ in range(2):
                main.sync_stock_listings(SimpleNamespace(statuses=["L", "D"], token=None), db)
                main.sync_market_limit_prices(market_payload, db)
                main.sync_market_suspend_events(suspend_payload, db)
                main.sync_fund_adjust_factors(SimpleNamespace(ts_code="512480.SH", start_date=date(2026, 7, 9), end_date=date(2026, 7, 9), token=None), db)

            self.assertEqual(db.scalar(select(func.count(StockListing.ts_code))), 2)
            self.assertEqual(db.scalar(select(func.count(StockLimitPrice.id))), 1)
            self.assertEqual(db.scalar(select(func.count(StockSuspendEvent.id))), 1)
            self.assertEqual(db.scalar(select(func.count(FundAdjustFactor.id))), 1)
            self.assertEqual(main.list_stock_listings(as_of=date(2020, 1, 1), list_status=None, limit=20, db=db)[0]["tsCode"], "000001.SZ")
            self.assertEqual(main.get_stock_limit_prices("000001.SZ", date(2026, 7, 9), date(2026, 7, 9), db)[0]["upLimit"], 11.0)
            self.assertEqual(main.get_stock_suspend_events("000001.SZ", date(2026, 7, 9), date(2026, 7, 9), db)[0]["suspendType"], "S")
            self.assertEqual(main.get_fund_adjust_factors("512480.SH", date(2026, 7, 9), date(2026, 7, 9), db)[0]["adjFactor"], 1.2345)

    def test_postgres_natural_key_only_upsert_uses_do_nothing(self):
        class FakePostgresSession:
            bind = SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))

            def __init__(self):
                self.statements = []
                self.committed = False

            def execute(self, statement):
                self.statements.append(statement)

            def commit(self):
                self.committed = True

        db = FakePostgresSession()
        row = {
            "ts_code": "000001.SZ",
            "trade_date": date(2026, 7, 9),
            "suspend_type": "S",
            "suspend_timing": "",
        }

        count = main.upsert_rows(
            db,
            StockSuspendEvent,
            [row],
            ["ts_code", "trade_date", "suspend_type", "suspend_timing"],
        )

        statement_sql = str(db.statements[0].compile(dialect=postgresql.dialect()))
        self.assertEqual(count, 1)
        self.assertTrue(db.committed)
        self.assertIn("ON CONFLICT", statement_sql)
        self.assertIn("DO NOTHING", statement_sql)


class FakeFrame:
    def __init__(self, records):
        self.records = records

    def to_dict(self, orient):
        if orient != "records":
            raise AssertionError(f"unexpected orient: {orient}")
        return self.records


class FakeTushare:
    def stock_basic(self, list_status="L", **_kwargs):
        records = {
            "L": [
                {
                    "ts_code": "000001.SZ",
                    "symbol": "000001",
                    "name": "平安银行",
                    "area": "深圳",
                    "industry": "银行",
                    "market": "主板",
                    "exchange": "SZSE",
                    "list_status": "L",
                    "list_date": "19910403",
                    "delist_date": None,
                }
            ],
            "D": [
                {
                    "ts_code": "000999.SZ",
                    "symbol": "000999",
                    "name": "退市样本",
                    "area": "深圳",
                    "industry": "综合",
                    "market": "主板",
                    "exchange": "SZSE",
                    "list_status": "D",
                    "list_date": "19990101",
                    "delist_date": "20181231",
                }
            ],
        }
        return FakeFrame(records.get(list_status, []))

    def trade_cal(self, **_kwargs):
        return FakeFrame([{"exchange": "SSE", "cal_date": "20260709", "is_open": 1, "pretrade_date": "20260708"}])

    def stk_limit(self, **_kwargs):
        return FakeFrame([{"ts_code": "000001.SZ", "trade_date": "20260709", "pre_close": 10, "up_limit": 11, "down_limit": 9}])

    def suspend_d(self, **_kwargs):
        return FakeFrame([{"ts_code": "000001.SZ", "trade_date": "20260709", "suspend_type": "S", "suspend_timing": None}])

    def fund_adj(self, **_kwargs):
        return FakeFrame([{"ts_code": "512480.SH", "trade_date": "20260709", "adj_factor": 1.2345}])


if __name__ == "__main__":
    unittest.main()
