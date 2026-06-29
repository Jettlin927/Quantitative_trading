from __future__ import annotations

import unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

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

    def test_p0_table_contracts_and_mappers_exist(self):
        for table_name in [
            "trade_calendars",
            "stock_adjust_factors",
            "indices",
            "index_daily_bars",
            "funds",
            "fund_daily_bars",
            "industry_classifications",
            "industry_members",
        ]:
            self.assertIn(table_name, Base.metadata.tables)

        self.assertEqual(main.trade_calendar_record_to_row({"exchange": "SSE", "cal_date": "20260629", "is_open": 1})["cal_date"], date(2026, 6, 29))
        self.assertEqual(main.adjust_factor_record_to_row({"ts_code": "600703.SH", "trade_date": "20260629", "adj_factor": 12.3456})["ts_code"], "600703.SH")
        self.assertEqual(main.index_basic_record_to_row({"ts_code": "000300.SH", "name": "沪深300", "market": "SSE"})["ts_code"], "000300.SH")
        self.assertEqual(main.fund_basic_record_to_row({"ts_code": "512480.SH", "name": "半导体ETF", "market": "E"})["ts_code"], "512480.SH")
        self.assertEqual(main.industry_classification_record_to_row({"index_code": "801081.SI", "industry_name": "半导体", "level": "L2", "src": "SW2021"})["index_code"], "801081.SI")

    def test_p0_sync_routes_upsert_and_overview(self):
        paths = {route.path for route in main.app.routes}
        for path in [
            "/api/tushare/sync-trade-calendar",
            "/api/tushare/sync-adjust-factors",
            "/api/tushare/sync-index-basic",
            "/api/tushare/sync-index-daily",
            "/api/tushare/sync-fund-basic",
            "/api/tushare/sync-fund-daily",
            "/api/tushare/sync-industry-classifications",
        ]:
            self.assertIn(path, paths)

        with self.open_session() as db, patch.object(main, "get_pro_api", return_value=FakeTushare()):
            self.assertEqual(main.sync_trade_calendar(SimpleNamespace(start_date=date(2026, 6, 1), end_date=date(2026, 6, 30), exchange="", token=None), db)["rows_upserted"], 2)
            self.assertEqual(main.sync_adjust_factors(SimpleNamespace(ts_code="600703.SH", start_date=date(2026, 6, 1), end_date=date(2026, 6, 30), token=None), db)["rows_upserted"], 1)
            self.assertEqual(main.sync_index_basic(SimpleNamespace(markets=["CSI", "SSE", "SZSE", "SW"], token=None), db)["rows_upserted"], 4)
            self.assertEqual(main.sync_index_daily(SimpleNamespace(ts_codes=["000300.SH"], start_date=date(2026, 6, 1), end_date=date(2026, 6, 30), token=None), db)["rows_upserted"], 1)
            self.assertEqual(main.sync_fund_basic(SimpleNamespace(market="E", token=None), db)["rows_upserted"], 2)
            self.assertEqual(main.sync_fund_daily(SimpleNamespace(ts_codes=["512480.SH"], start_date=date(2026, 6, 1), end_date=date(2026, 6, 30), token=None), db)["rows_upserted"], 1)
            self.assertEqual(main.sync_industry_classifications(SimpleNamespace(src="SW2021", index_codes=["801081.SI"], token=None), db)["rows_upserted"], 4)

            overview = main.get_db_overview(db)

        self.assertEqual(overview["aShare"]["tradeCalendar"]["rows"], 2)
        self.assertEqual(overview["aShare"]["adjustFactors"]["rows"], 1)
        self.assertEqual(overview["aShare"]["indices"]["rows"], 4)
        self.assertEqual(overview["aShare"]["indexDailyBars"]["rows"], 1)
        self.assertEqual(overview["aShare"]["funds"]["rows"], 2)
        self.assertEqual(overview["aShare"]["fundDailyBars"]["rows"], 1)
        self.assertEqual(overview["aShare"]["industries"]["rows"], 1)
        self.assertEqual(overview["aShare"]["industryMembers"], 3)

    def test_p0_readonly_queries_return_data_only(self):
        with self.open_session() as db, patch.object(main, "get_pro_api", return_value=FakeTushare()):
            main.sync_trade_calendar(SimpleNamespace(start_date=date(2026, 6, 1), end_date=date(2026, 6, 30), exchange="", token=None), db)
            main.sync_adjust_factors(SimpleNamespace(ts_code="600703.SH", start_date=date(2026, 6, 1), end_date=date(2026, 6, 30), token=None), db)
            main.sync_index_basic(SimpleNamespace(markets=["CSI", "SSE", "SZSE", "SW"], token=None), db)
            main.sync_index_daily(SimpleNamespace(ts_codes=["000300.SH"], start_date=date(2026, 6, 1), end_date=date(2026, 6, 30), token=None), db)
            main.sync_fund_basic(SimpleNamespace(market="E", token=None), db)
            main.sync_fund_daily(SimpleNamespace(ts_codes=["512480.SH"], start_date=date(2026, 6, 1), end_date=date(2026, 6, 30), token=None), db)
            main.sync_industry_classifications(SimpleNamespace(src="SW2021", index_codes=["801081.SI"], token=None), db)

            self.assertTrue(main.get_trade_calendar_day(date(2026, 6, 2), db)["isOpen"])
            self.assertEqual(len(main.get_recent_trade_calendars(5, db)), 2)
            self.assertEqual(main.get_stock_adjust_factors("600703.SH", date(2026, 6, 1), date(2026, 6, 30), db)[0]["tsCode"], "600703.SH")
            self.assertEqual(main.list_indices(q="沪深300", limit=20, db=db)[0]["tsCode"], "000300.SH")
            self.assertEqual(main.get_index_daily_bars("000300.SH", date(2026, 6, 1), date(2026, 6, 30), db)[0]["tsCode"], "000300.SH")
            self.assertEqual(main.list_funds(q="半导体", limit=20, db=db)[0]["tsCode"], "512480.SH")
            self.assertEqual(main.get_fund_daily_bars("512480.SH", date(2026, 6, 1), date(2026, 6, 30), db)[0]["tsCode"], "512480.SH")
            self.assertEqual(main.list_industries(q="半导体", limit=20, db=db)[0]["indexCode"], "801081.SI")
            self.assertEqual(main.get_industry_members("801081.SI", db)[0]["conCode"], "600703.SH")


class FakeFrame:
    def __init__(self, records):
        self.records = records

    def to_dict(self, orient):
        self.assert_orient_records(orient)
        return self.records

    @staticmethod
    def assert_orient_records(orient):
        if orient != "records":
            raise AssertionError(f"unexpected orient: {orient}")


class FakeTushare:
    def trade_cal(self, **_kwargs):
        return FakeFrame(
            [
                {"exchange": "SSE", "cal_date": "20260601", "is_open": 0, "pretrade_date": "20260529"},
                {"exchange": "SSE", "cal_date": "20260602", "is_open": 1, "pretrade_date": "20260601"},
            ]
        )

    def adj_factor(self, **_kwargs):
        return FakeFrame([{"ts_code": "600703.SH", "trade_date": "20260602", "adj_factor": 12.3456}])

    def index_basic(self, market="", **_kwargs):
        records = {
            "CSI": [{"ts_code": "000300.SH", "name": "沪深300", "market": "CSI", "publisher": "中证", "category": "宽基"}],
            "SSE": [{"ts_code": "000001.SH", "name": "上证指数", "market": "SSE", "publisher": "上交所", "category": "宽基"}],
            "SZSE": [{"ts_code": "399006.SZ", "name": "创业板指", "market": "SZSE", "publisher": "深交所", "category": "宽基"}],
            "SW": [{"ts_code": "801081.SI", "name": "半导体", "market": "SW", "publisher": "申万", "category": "行业"}],
        }
        return FakeFrame(records.get(market, []))

    def index_daily(self, **_kwargs):
        return FakeFrame(
            [
                {
                    "ts_code": "000300.SH",
                    "trade_date": "20260602",
                    "open": 4000,
                    "high": 4010,
                    "low": 3990,
                    "close": 4008,
                    "pre_close": 3998,
                    "change": 10,
                    "pct_chg": 0.25,
                    "vol": 100000,
                    "amount": 200000,
                }
            ]
        )

    def fund_basic(self, **_kwargs):
        return FakeFrame(
            [
                {"ts_code": "512480.SH", "name": "半导体ETF", "market": "E", "fund_type": "ETF", "management": "国联安"},
                {"ts_code": "512760.SH", "name": "芯片ETF", "market": "E", "fund_type": "ETF", "management": "国泰"},
            ]
        )

    def fund_daily(self, **_kwargs):
        return FakeFrame(
            [
                {
                    "ts_code": "512480.SH",
                    "trade_date": "20260602",
                    "open": 0.8,
                    "high": 0.82,
                    "low": 0.79,
                    "close": 0.81,
                    "pre_close": 0.8,
                    "change": 0.01,
                    "pct_chg": 1.25,
                    "vol": 100000,
                    "amount": 81000,
                }
            ]
        )

    def index_classify(self, **_kwargs):
        return FakeFrame([{"index_code": "801081.SI", "industry_name": "半导体", "level": "L2", "industry_code": "801081", "src": "SW2021"}])

    def index_member_all(self, **_kwargs):
        return FakeFrame(
            [
                {"index_code": "801081.SI", "con_code": "600703.SH", "con_name": "三安光电", "in_date": "20200101", "out_date": None, "is_new": "Y"},
                {"index_code": "801081.SI", "con_code": "688981.SH", "con_name": "中芯国际", "in_date": "20200101", "out_date": None, "is_new": "Y"},
                {"index_code": "801081.SI", "con_code": "002371.SZ", "con_name": "北方华创", "in_date": "20200101", "out_date": None, "is_new": "Y"},
            ]
        )


if __name__ == "__main__":
    unittest.main()
