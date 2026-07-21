from __future__ import annotations

import unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app import main
from backend.app.database import Base
from backend.app.models import (
    DataOverviewSnapshot,
    Fund,
    FundDailyBar,
    Stock,
    StockAdjustFactor,
    StockDailyBar,
    StockDailyBasic,
    StockFinancialIndicator,
    StockLimitPrice,
    StockListing,
    StockSuspendEvent,
)
from backend.app.schemas import DataQualityRunRequest


class DataApiContractTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)

        with self.Session() as db:
            db.add(Stock(ts_code="600703.SH", symbol="600703", name="三安光电", area="湖北", industry="半导体", market="主板", list_date=date(1996, 5, 28)))
            db.add(Stock(ts_code="000001.SZ", symbol="000001", name="平安银行", area="深圳", industry="银行", market="主板", list_date=date(1991, 4, 3)))
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
            db.add_all(
                [
                    StockDailyBasic(
                        ts_code="600703.SH",
                        trade_date=date(2026, 5, 29),
                        close=12.3,
                        turnover_rate=1.5,
                        pe_ttm=22.4,
                        pb=2.1,
                    ),
                    StockFinancialIndicator(
                        ts_code="600703.SH",
                        ann_date=date(2026, 4, 30),
                        end_date=date(2026, 3, 31),
                        eps=0.25,
                        roe=4.2,
                    ),
                    StockListing(
                        ts_code="600703.SH",
                        symbol="600703",
                        name="三安光电",
                        area="湖北",
                        industry="半导体",
                        market="主板",
                        exchange="SSE",
                        list_status="L",
                        list_date=date(1996, 5, 28),
                    ),
                    StockLimitPrice(
                        ts_code="600703.SH",
                        trade_date=date(2026, 5, 29),
                        pre_close=12.15,
                        up_limit=13.37,
                        down_limit=10.94,
                    ),
                    StockSuspendEvent(
                        ts_code="600703.SH",
                        trade_date=date(2026, 5, 28),
                        suspend_type="S",
                        suspend_timing="09:30-10:30",
                    ),
                    StockAdjustFactor(
                        ts_code="600703.SH",
                        trade_date=date(2026, 5, 29),
                        adj_factor=3.14,
                    ),
                ]
            )
            db.commit()

    def open_session(self):
        return self.Session()

    def tearDown(self):
        main.app.dependency_overrides.clear()

    def test_health_and_db_overview_are_data_only(self):
        with self.open_session() as db:
            health = main.health(db, include_counts=True)
            light_health = main.health(db, include_counts=False)
            overview = main.get_db_overview(db)
            cached_overview = main.get_db_overview(db)
            snapshot = db.get(DataOverviewSnapshot, "default")
            db.add(
                StockDailyBar(
                    ts_code="600703.SH",
                    trade_date=date(2026, 6, 1),
                    open=12.3,
                    high=12.6,
                    low=12.2,
                    close=12.5,
                )
            )
            db.commit()
            stale_overview = main.get_db_overview(db)
            refreshed_overview = main.get_db_overview(db, refresh=True)

        self.assertEqual(health["service"], "quant-data-workspace")
        self.assertIn("tables", health)
        self.assertNotIn("tables", light_health)
        payload = overview
        self.assertEqual(payload["aShare"]["stocks"], 2)
        self.assertEqual(payload["aShare"]["dailyBars"]["maxDate"], "2026-05-29")
        self.assertEqual(payload["tables"]["stockDailyBars"], 1)
        self.assertIsNotNone(snapshot)
        self.assertEqual(cached_overview["aShare"]["dailyBars"]["rows"], 1)
        self.assertEqual(stale_overview["aShare"]["dailyBars"]["rows"], 1)
        self.assertEqual(refreshed_overview["aShare"]["dailyBars"]["rows"], 2)

    def test_light_sync_progress_skips_coverage_queries(self):
        with self.open_session() as db:
            payload = main.get_sync_progress(db, include_coverage=False)

        self.assertIn("runs", payload)
        self.assertNotIn("coverage", payload)

    def test_stock_queries_return_raw_db_data(self):
        with self.open_session() as db:
            stocks = main.list_stocks(q="三安", db=db)
            screen = main.screen_stocks(q="600703", db=db)
            first_page = main.screen_stocks(limit=1, offset=0, db=db)
            second_page = main.screen_stocks(limit=1, offset=1, db=db)
            bars = main.get_daily_bars("600703.SH", date(2026, 5, 1), date(2026, 5, 31), db)
            all_bars = main.get_daily_bars("600703.SH", db=db)

        self.assertEqual(stocks[0].ts_code, "600703.SH")
        self.assertEqual(screen.items[0].close, 12.3)
        self.assertEqual(screen.total, 1)
        self.assertFalse(hasattr(screen.items[0], "signal_summary"))
        self.assertEqual(first_page.total, 2)
        self.assertEqual(first_page.limit, 1)
        self.assertEqual(first_page.offset, 0)
        self.assertEqual(second_page.offset, 1)
        self.assertNotEqual(first_page.items[0].ts_code, second_page.items[0].ts_code)
        self.assertEqual(bars[0].trade_date, date(2026, 5, 29))
        self.assertEqual(all_bars[0].trade_date, date(2026, 5, 29))

    def test_stock_detail_and_histories_are_read_only_actual_data(self):
        with self.open_session() as db:
            valuation = main.get_stock_valuation_history("600703.sh", db=db)
            financial = main.get_stock_financial_history("600703.sh", db=db)
            detail = main.get_stock_detail("600703.sh", db=db)

        self.assertEqual(valuation[0]["tradeDate"], "2026-05-29")
        self.assertEqual(financial[0]["annDate"], "2026-04-30")
        self.assertEqual(detail.stock.ts_code, "600703.SH")
        self.assertEqual(detail.latest_bar.trade_date, date(2026, 5, 29))
        self.assertEqual(detail.valuation["peTtm"], 22.4)
        self.assertEqual(detail.financial["roe"], 4.2)
        self.assertEqual(detail.listing["listStatus"], "L")
        self.assertEqual(detail.latest_limit_price["tradeDate"], "2026-05-29")
        self.assertEqual(detail.latest_suspend_event["tradeDate"], "2026-05-28")
        self.assertEqual(detail.latest_adjust_factor["adjFactor"], 3.14)

    def test_fund_catalog_can_be_limited_to_the_requested_daily_bar_window(self):
        with self.open_session() as db:
            db.add_all(
                [
                    Fund(ts_code="150008.SZ", name="瑞和小康", market="E"),
                    Fund(ts_code="512480.SH", name="半导体 ETF", market="E"),
                    FundDailyBar(ts_code="150008.SZ", trade_date=date(2015, 4, 30), close=1.02),
                    FundDailyBar(ts_code="512480.SH", trade_date=date(2026, 5, 29), close=1.23),
                ]
            )
            db.commit()

            unfiltered = main.list_funds(db=db)
            recent = main.list_funds(
                daily_start_date=date(2025, 7, 21),
                daily_end_date=date(2026, 7, 21),
                db=db,
            )

        self.assertEqual([row["tsCode"] for row in unfiltered], ["150008.SZ", "512480.SH"])
        self.assertEqual([row["tsCode"] for row in recent], ["512480.SH"])

    def test_strategy_and_backtest_routes_are_gone(self):
        paths = {route.path for route in main.app.routes}
        self.assertNotIn("/api/strategy-evaluations", paths)
        self.assertNotIn("/api/strategies/executable/cross-section-strength-risk8", paths)
        self.assertNotIn("/api/backtests/run", paths)

    def test_industry_membership_quality_request_uses_source_key_without_inline_members(self):
        request = DataQualityRunRequest(
            scope="a_share_cross_section",
            start_date=date(2026, 1, 2),
            end_date=date(2026, 1, 5),
            universe_type="industry_membership",
            universe_source="industry_members",
            universe_source_key="synind.si",
            benchmark="synidx.sh",
        )

        self.assertEqual(request.universe, [])
        self.assertEqual(request.universe_source_key, "SYNIND.SI")
        self.assertEqual(request.benchmark, "SYNIDX.SH")
        for override in (
            {"universe": ["SYN001.SZ"]},
            {"universe_source": "local/path.txt"},
            {"universe_source_key": None},
            {"universe_as_of_date": date(2026, 1, 2)},
        ):
            values = {
                "scope": "a_share_cross_section",
                "start_date": date(2026, 1, 2),
                "end_date": date(2026, 1, 5),
                "universe_type": "industry_membership",
                "universe_source": "industry_members",
                "universe_source_key": "SYNIND.SI",
                **override,
            }
            with self.subTest(override=override), self.assertRaises(ValueError):
                DataQualityRunRequest(**values)

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
        self.assertEqual(overview["aShare"]["adjustFactors"]["rows"], 2)
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
