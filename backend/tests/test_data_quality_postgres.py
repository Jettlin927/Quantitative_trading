from __future__ import annotations

import os
import unittest
from datetime import date
from uuid import uuid4

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from backend.app.database import Base
from backend.app.data_quality.contracts import QualityCheckContract
from backend.app.data_quality.runner import configure_quality_read_transaction
from backend.app.data_quality.runner import run_data_quality_check
from backend.app.models import Index, IndexDailyBar, StockAdjustFactor, StockDailyBar, StockLimitPrice, StockListing, TradeCalendar


@unittest.skipUnless(os.getenv("TEST_POSTGRES_URL"), "需要隔离的 TEST_POSTGRES_URL")
class DataQualityPostgresTest(unittest.TestCase):
    def test_quality_transaction_is_read_only_and_has_timeout(self):
        engine = create_engine(os.environ["TEST_POSTGRES_URL"], pool_pre_ping=True)
        try:
            with Session(engine) as db, db.begin():
                configure_quality_read_transaction(db, statement_timeout_ms=2500)
                self.assertEqual(db.scalar(text("show transaction_read_only")), "on")
                self.assertEqual(db.scalar(text("show transaction_isolation")), "repeatable read")
                self.assertEqual(db.scalar(text("show statement_timeout")), "2500ms")
        finally:
            engine.dispose()

    def test_quality_rules_and_registry_run_in_isolated_schema(self):
        database_url = os.environ["TEST_POSTGRES_URL"]
        schema = f"quality_test_{uuid4().hex}"
        admin_engine = create_engine(database_url, pool_pre_ping=True)
        with admin_engine.begin() as connection:
            connection.execute(text(f'create schema "{schema}"'))
        engine = create_engine(
            database_url,
            pool_pre_ping=True,
            connect_args={"options": f"-csearch_path={schema}"},
        )
        try:
            Base.metadata.create_all(engine)
            with Session(engine) as db:
                db.add_all(
                    [
                        TradeCalendar(exchange="SSE", cal_date=date(2026, 1, 2), is_open=True),
                        StockListing(
                            ts_code="000001.SZ",
                            symbol="000001",
                            name="测试股票",
                            exchange="SZSE",
                            list_status="L",
                            list_date=date(1991, 4, 3),
                        ),
                        StockDailyBar(
                            ts_code="000001.SZ",
                            trade_date=date(2026, 1, 2),
                            open=10,
                            high=11,
                            low=9,
                            close=10,
                            vol=100,
                            amount=1000,
                        ),
                        StockAdjustFactor(ts_code="000001.SZ", trade_date=date(2026, 1, 2), adj_factor=1),
                        StockLimitPrice(
                            ts_code="000001.SZ",
                            trade_date=date(2026, 1, 2),
                            pre_close=10,
                            up_limit=11,
                            down_limit=9,
                        ),
                        Index(ts_code="000300.SH", name="测试基准", market="CSI"),
                        IndexDailyBar(
                            ts_code="000300.SH",
                            trade_date=date(2026, 1, 2),
                            open=4000,
                            high=4010,
                            low=3990,
                            close=4005,
                            vol=1000,
                            amount=10000,
                        ),
                    ]
                )
                db.commit()

                contract = QualityCheckContract.create(
                    scope="a_share_cross_section",
                    start_date=date(2026, 1, 2),
                    end_date=date(2026, 1, 2),
                    universe=["000001.SZ"],
                    universe_source="backend/tests/fixtures/quality-universe.txt",
                    universe_as_of_date=date(2026, 1, 2),
                    benchmark="000300.SH",
                    statement_timeout_ms=5000,
                )
                report = run_data_quality_check(db, contract, code_commit="postgres-test")

            self.assertEqual(report["status"], "ready")
            self.assertTrue(report["results"])
        finally:
            engine.dispose()
            with admin_engine.begin() as connection:
                connection.execute(text(f'drop schema if exists "{schema}" cascade'))
            admin_engine.dispose()


if __name__ == "__main__":
    unittest.main()
