from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app import main, market_data_ingestion, sync_worker
from backend.app.database import Base
from backend.app.models import DataSyncJob, DataSyncRun, TradeCalendar
from backend.app.schemas import SyncJobCreate, SyncTradeCalendarRequest


class Frame:
    def __init__(self, rows: list[dict]):
        self.rows = rows

    def to_dict(self, orient: str) -> list[dict]:
        if orient != "records":
            raise AssertionError(f"unexpected orient: {orient}")
        return self.rows


class MarketDataIngestionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)

    def tearDown(self) -> None:
        self.engine.dispose()

    def test_registry_is_the_single_action_contract_and_worker_uses_it(self) -> None:
        self.assertEqual(
            set(market_data_ingestion.ACTION_REGISTRY),
            set(market_data_ingestion.IngestionAction),
        )
        self.assertEqual(
            sync_worker.SUPPORTED_SYNC_ACTIONS,
            {action.value for action in market_data_ingestion.IngestionAction},
        )
        trade_calendar = market_data_ingestion.get_action_spec("trade_calendar")
        self.assertIs(trade_calendar.payload_model, SyncTradeCalendarRequest)
        self.assertEqual(trade_calendar.secret_fields, frozenset({"token"}))
        self.assertTrue(trade_calendar.allow_sync_http)
        self.assertTrue(trade_calendar.allow_worker)
        self.assertFalse(trade_calendar.metadata.is_experimental)

    def test_command_normalization_removes_secret_from_payload_hash_and_repr(self) -> None:
        raw = {
            "start_date": "2026-07-10",
            "end_date": "2026-07-11",
            "exchange": "",
            "token": "must-not-leak",
        }
        command = market_data_ingestion.build_command("trade_calendar", raw)
        same_without_secret = market_data_ingestion.build_command(
            "trade_calendar", {key: value for key, value in raw.items() if key != "token"}
        )

        self.assertNotIn("token", command.payload)
        self.assertNotIn("must-not-leak", repr(command))
        self.assertEqual(command.payload_hash, same_without_secret.payload_hash)

    def test_sync_http_and_worker_share_canonical_trade_calendar_execution(self) -> None:
        pro = Mock()
        pro.trade_cal.return_value = Frame(
            [
                {"exchange": "SSE", "cal_date": "20260711", "is_open": 1, "pretrade_date": "20260710"},
                {"exchange": "SSE", "cal_date": "20260711", "is_open": 1, "pretrade_date": "20260710"},
            ]
        )
        payload = SyncTradeCalendarRequest(
            start_date=date(2026, 7, 11),
            end_date=date(2026, 7, 11),
            exchange="",
            token="ephemeral-token",
        )

        with self.Session() as db, patch.object(main, "get_pro_api", side_effect=lambda _token: pro):
            http_result = main.sync_trade_calendar(payload, db)
            queued = main.create_sync_job(
                SyncJobCreate(
                    action="trade_calendar",
                    payload={"start_date": "2026-07-11", "end_date": "2026-07-11", "exchange": ""},
                ),
                db,
            )

        now = datetime.now(timezone.utc) + timedelta(days=1)
        claim = sync_worker.claim_next_job("worker-tracer", session_factory=self.Session, now=now)
        with patch.object(market_data_ingestion, "get_pro_api", side_effect=lambda _token: pro):
            worker_status = sync_worker.run_claimed_job(
                claim,
                session_factory=self.Session,
                heartbeat_interval_seconds=0,
                now=now,
            )
        with self.Session() as db:
            worker_job = db.get(DataSyncJob, queued["id"])
            rows = db.scalar(select(func.count()).select_from(TradeCalendar))
            runs = list(db.scalars(select(DataSyncRun).order_by(DataSyncRun.id)))

        self.assertEqual(http_result, {"status": "ok", "rows_upserted": 1})
        self.assertEqual(worker_status, "ok")
        self.assertEqual(worker_job.rows_upserted, http_result["rows_upserted"])
        self.assertEqual(rows, 1)
        self.assertEqual([run.target for run in runs], ["trade_calendar", "trade_calendar"])
        self.assertEqual([run.rows_upserted for run in runs], [1, 1])
        self.assertEqual(pro.trade_cal.call_count, 2)
        self.assertEqual(pro.trade_cal.call_args_list[0].kwargs["start_date"], "20260711")

    def test_worker_does_not_reverse_import_application_entry(self) -> None:
        source = Path(sync_worker.__file__).read_text(encoding="utf-8")
        self.assertNotIn("from .main import", source)


if __name__ == "__main__":
    unittest.main()
