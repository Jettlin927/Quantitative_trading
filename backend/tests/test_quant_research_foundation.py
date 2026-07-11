from __future__ import annotations

import gzip
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from backend.app.database import Base
from backend.app.models import StockAdjustFactor, StockDailyBar, StockLimitPrice, StockListing, StockSuspendEvent
from backend.app.quant_research.calendar import (
    build_open_trade_calendar,
    canonical_trade_calendar_bytes,
    trade_calendar_content_sha256,
)
from backend.app.quant_research.dataset import active_members_as_of, attach_fundamentals_asof, build_adjusted_price_panel
from backend.app.quant_research.manifest import build_run_manifest
from backend.app.quant_research.metrics import summarize_performance
from backend.app.quant_research.portfolio import CostModel, simulate_target_weights
from backend.app.quant_research.readiness import evaluate_research_readiness
from backend.app.quant_research.repository import load_stock_research_panel
from backend.app.quant_research.universe import (
    build_explicit_universe,
    build_historical_membership_panel,
    build_historical_universe,
    evaluate_universe_provenance,
)
from backend.app.quant_research.validation import build_walk_forward_windows


def formal_targets(rows: list[dict[str, object]]) -> pd.DataFrame:
    return pd.DataFrame([{**row, "available_date": row.get("available_date", row["signal_date"])} for row in rows])


FIXTURE_DIR = Path(__file__).parent / "fixtures"
UNIVERSE_ONE = FIXTURE_DIR / "universe-000001.txt"
UNIVERSE_TWO = FIXTURE_DIR / "universe-000001-000002.txt"
HISTORICAL_SOURCE = FIXTURE_DIR / "quant_research_golden" / "industry_members.csv"
TEST_CALENDAR_DIR = tempfile.TemporaryDirectory(prefix="quant-trade-calendar-")


def calendar_for_dates(dates: list[object] | pd.Series) -> object:
    records = [{"exchange": "SSE", "cal_date": value, "is_open": True} for value in list(dates)]
    content = canonical_trade_calendar_bytes(records)
    source = Path(TEST_CALENDAR_DIR.name) / f"{trade_calendar_content_sha256(records)}.csv"
    source.write_bytes(content)
    return build_open_trade_calendar(
        records,
        source_artifact=str(source),
        source_artifact_sha256=trade_calendar_content_sha256(records),
    )


def open_dates(prices: pd.DataFrame) -> object:
    return calendar_for_dates(prices["trade_date"].drop_duplicates().tolist())


class QuantResearchDatasetTest(unittest.TestCase):
    def test_builds_causal_adjusted_prices(self):
        bars = pd.DataFrame(
            [
                {"ts_code": "000001.SZ", "trade_date": "2026-01-02", "open": 10, "high": 10.5, "low": 9.5, "close": 10, "amount": 1000},
                {"ts_code": "000001.SZ", "trade_date": "2026-01-05", "open": 11, "high": 12, "low": 10.5, "close": 11, "amount": 1200},
            ]
        )
        factors = pd.DataFrame(
            [
                {"ts_code": "000001.SZ", "trade_date": "2026-01-02", "adj_factor": 1},
                {"ts_code": "000001.SZ", "trade_date": "2026-01-05", "adj_factor": 2},
            ]
        )

        adjusted = build_adjusted_price_panel(bars, factors)

        self.assertEqual(adjusted["adj_close"].tolist(), [10.0, 22.0])
        self.assertEqual(adjusted["total_return_index"].tolist(), [1.0, 2.2])
        self.assertAlmostEqual(adjusted.iloc[1]["adjusted_return"], 1.2)

    def test_adjusted_history_prefix_is_unchanged_by_future_factor(self):
        bars = pd.DataFrame(
            [
                {"ts_code": "000001.SZ", "trade_date": "2026-01-02", "open": 10, "high": 10, "low": 10, "close": 10},
                {"ts_code": "000001.SZ", "trade_date": "2026-01-05", "open": 11, "high": 11, "low": 11, "close": 11},
            ]
        )
        factors = pd.DataFrame(
            [
                {"ts_code": "000001.SZ", "trade_date": "2026-01-02", "adj_factor": 1},
                {"ts_code": "000001.SZ", "trade_date": "2026-01-05", "adj_factor": 2},
            ]
        )
        baseline = build_adjusted_price_panel(bars, factors)
        extended = build_adjusted_price_panel(
            pd.concat(
                [
                    bars,
                    pd.DataFrame(
                        [{"ts_code": "000001.SZ", "trade_date": "2026-01-06", "open": 12, "high": 12, "low": 12, "close": 12}]
                    ),
                ],
                ignore_index=True,
            ),
            pd.concat(
                [
                    factors,
                    pd.DataFrame([{"ts_code": "000001.SZ", "trade_date": "2026-01-06", "adj_factor": 4}]),
                ],
                ignore_index=True,
            ),
        )

        pd.testing.assert_frame_equal(
            baseline[["adj_open", "adj_close", "adjusted_return", "total_return_index"]],
            extended.iloc[:2][["adj_open", "adj_close", "adjusted_return", "total_return_index"]].reset_index(drop=True),
        )

    def test_rejects_missing_adjust_factor(self):
        bars = pd.DataFrame([{"ts_code": "000001.SZ", "trade_date": "2026-01-02", "open": 10, "high": 10, "low": 10, "close": 10}])
        factors = pd.DataFrame(columns=["ts_code", "trade_date", "adj_factor"])

        with self.assertRaisesRegex(ValueError, "复权因子"):
            build_adjusted_price_panel(bars, factors)

    def test_attaches_only_announced_fundamentals(self):
        panel = pd.DataFrame(
            [
                {"ts_code": "000001.SZ", "trade_date": "2026-01-05", "adj_close": 10},
                {"ts_code": "000001.SZ", "trade_date": "2026-01-09", "adj_close": 11},
            ]
        )
        fundamentals = pd.DataFrame(
            [
                {"ts_code": "000001.SZ", "ann_date": "2026-01-07", "end_date": "2025-12-31", "roe": 12.5},
            ]
        )

        merged = attach_fundamentals_asof(
            panel,
            fundamentals,
            trade_calendar=calendar_for_dates(
                ["2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08", "2026-01-09"]
            ),
        )

        self.assertTrue(pd.isna(merged.iloc[0]["roe"]))
        self.assertEqual(merged.iloc[1]["roe"], 12.5)
        self.assertLessEqual(merged.iloc[1]["ann_date"], merged.iloc[1]["trade_date"])
        self.assertEqual(str(merged.iloc[1]["available_date"].date()), "2026-01-08")

    def test_same_day_fundamental_is_only_available_next_trade_date(self):
        panel = pd.DataFrame(
            [
                {"ts_code": "000001.SZ", "trade_date": "2026-01-09"},
                {"ts_code": "000001.SZ", "trade_date": "2026-01-12"},
            ]
        )
        fundamentals = pd.DataFrame(
            [{"ts_code": "000001.SZ", "ann_date": "2026-01-09", "end_date": "2025-12-31", "roe": 10.0}]
        )

        merged = attach_fundamentals_asof(
            panel,
            fundamentals,
            trade_calendar=calendar_for_dates(["2026-01-09", "2026-01-12"]),
        )

        self.assertTrue(pd.isna(merged.iloc[0]["roe"]))
        self.assertEqual(merged.iloc[1]["roe"], 10.0)
        self.assertEqual(str(merged.iloc[1]["available_date"].date()), "2026-01-12")

    def test_same_available_date_requires_explicit_period_policy(self):
        panel = pd.DataFrame(
            [
                {"ts_code": "000001.SZ", "trade_date": "2026-01-09"},
                {"ts_code": "000001.SZ", "trade_date": "2026-01-12"},
            ]
        )
        fundamentals = pd.DataFrame(
            [
                {"ts_code": "000001.SZ", "ann_date": "2026-01-09", "end_date": "2025-09-30", "roe": 8.0},
                {"ts_code": "000001.SZ", "ann_date": "2026-01-09", "end_date": "2025-12-31", "roe": 10.0},
            ]
        )

        with self.assertRaisesRegex(ValueError, "period_policy"):
            attach_fundamentals_asof(
                panel,
                fundamentals,
                trade_calendar=calendar_for_dates(["2026-01-09", "2026-01-12"]),
            )
        merged = attach_fundamentals_asof(
            panel,
            fundamentals,
            trade_calendar=calendar_for_dates(["2026-01-09", "2026-01-12"]),
            period_policy="latest_end_date",
        )
        self.assertEqual(merged.iloc[1]["roe"], 10.0)
        self.assertEqual(str(merged.iloc[1]["end_date"].date()), "2025-12-31")

    def test_fundamental_history_prefix_is_unchanged_by_future_announcement(self):
        baseline_panel = pd.DataFrame(
            [
                {"ts_code": "000001.SZ", "trade_date": "2026-01-09"},
                {"ts_code": "000001.SZ", "trade_date": "2026-01-12"},
            ]
        )
        baseline_fundamentals = pd.DataFrame(
            [{"ts_code": "000001.SZ", "ann_date": "2026-01-09", "end_date": "2025-12-31", "roe": 10.0}]
        )
        official_calendar = calendar_for_dates(["2026-01-09", "2026-01-12", "2026-01-13"])
        baseline = attach_fundamentals_asof(
            baseline_panel,
            baseline_fundamentals,
            trade_calendar=official_calendar,
        )
        extended = attach_fundamentals_asof(
            pd.concat(
                [baseline_panel, pd.DataFrame([{"ts_code": "000001.SZ", "trade_date": "2026-01-13"}])],
                ignore_index=True,
            ),
            pd.concat(
                [
                    baseline_fundamentals,
                    pd.DataFrame([{"ts_code": "000001.SZ", "ann_date": "2026-01-12", "end_date": "2026-03-31", "roe": 20.0}]),
                ],
                ignore_index=True,
            ),
            trade_calendar=official_calendar,
        )

        pd.testing.assert_frame_equal(
            baseline[["trade_date", "ann_date", "available_date", "end_date", "roe"]],
            extended.iloc[:2][["trade_date", "ann_date", "available_date", "end_date", "roe"]].reset_index(drop=True),
        )

    def test_fundamentals_require_explicit_official_trade_calendar(self):
        panel = pd.DataFrame([{"ts_code": "000001.SZ", "trade_date": "2026-01-09"}])
        fundamentals = pd.DataFrame(
            [{"ts_code": "000001.SZ", "ann_date": "2026-01-07", "end_date": "2025-12-31", "roe": 10.0}]
        )

        with self.assertRaisesRegex((TypeError, ValueError), "trade_calendar|交易日历"):
            attach_fundamentals_asof(panel, fundamentals)

    def test_filters_historical_industry_membership(self):
        memberships = pd.DataFrame(
            [
                {"index_code": "801080.SI", "con_code": "A", "in_date": "2020-01-01", "out_date": "2024-12-31"},
                {"index_code": "801080.SI", "con_code": "B", "in_date": "2025-01-01", "out_date": None},
            ]
        )

        self.assertEqual(active_members_as_of(memberships, "2024-06-30", "801080.SI"), {"A"})
        self.assertEqual(active_members_as_of(memberships, "2025-06-30", "801080.SI"), {"B"})

    def test_repository_requires_explicit_historical_universe_and_tradability(self):
        engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(engine)
        with Session(engine) as db:
            db.add(StockListing(ts_code="000001.SZ", symbol="000001", name="平安银行", list_status="L", list_date=pd.Timestamp("1991-04-03").date()))
            db.add(StockDailyBar(ts_code="000001.SZ", trade_date=pd.Timestamp("2026-01-02").date(), open=10, high=11, low=9, close=10, pre_close=9.8))
            db.add(StockAdjustFactor(ts_code="000001.SZ", trade_date=pd.Timestamp("2026-01-02").date(), adj_factor=2))
            db.add(StockLimitPrice(ts_code="000001.SZ", trade_date=pd.Timestamp("2026-01-02").date(), pre_close=9.8, up_limit=10.78, down_limit=8.82))
            db.commit()

        universe = build_explicit_universe(
            ["000001.SZ"],
            as_of_date="2026-01-02",
            source=str(UNIVERSE_ONE),
        )
        panel = load_stock_research_panel(
            engine,
            universe,
            pd.Timestamp("2026-01-02").date(),
            pd.Timestamp("2026-01-02").date(),
        )

        self.assertEqual(panel.iloc[0]["adj_close"], 10)
        self.assertTrue(panel.iloc[0]["is_buyable_at_open"])
        self.assertTrue(panel.attrs["universeProvenance"]["survivorshipRisk"])
        with self.assertRaisesRegex(ValueError, "universe"):
            load_stock_research_panel(
                engine,
                ["000001.SZ"],
                pd.Timestamp("2026-01-02").date(),
                pd.Timestamp("2026-01-02").date(),
            )

    def test_repository_only_blocks_suspension_at_market_open(self):
        engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(engine)
        with Session(engine) as db:
            db.add(StockListing(ts_code="000001.SZ", symbol="000001", name="平安银行", list_status="L", list_date=pd.Timestamp("1991-04-03").date()))
            for trade_date, timing in (("2026-01-05", "09:30-10:00"), ("2026-01-06", "13:00-15:00")):
                day = pd.Timestamp(trade_date).date()
                db.add(StockDailyBar(ts_code="000001.SZ", trade_date=day, open=10, high=11, low=9, close=10, pre_close=10))
                db.add(StockAdjustFactor(ts_code="000001.SZ", trade_date=day, adj_factor=1))
                db.add(StockLimitPrice(ts_code="000001.SZ", trade_date=day, pre_close=10, up_limit=11, down_limit=9))
                db.add(StockSuspendEvent(ts_code="000001.SZ", trade_date=day, suspend_type="S", suspend_timing=timing))
            db.commit()

        universe = build_explicit_universe(["000001.SZ"], as_of_date="2026-01-05", source=str(UNIVERSE_ONE))
        panel = load_stock_research_panel(
            engine,
            universe,
            pd.Timestamp("2026-01-05").date(),
            pd.Timestamp("2026-01-06").date(),
        )

        self.assertFalse(panel.iloc[0]["is_buyable_at_open"])
        self.assertTrue(panel.iloc[1]["is_buyable_at_open"])

    def test_repository_marks_missing_full_day_suspension_for_explicit_carry(self):
        engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(engine)
        with Session(engine) as db:
            db.add(StockListing(ts_code="000001.SZ", symbol="000001", name="平安银行", list_status="L", list_date=pd.Timestamp("1991-04-03").date()))
            db.add(StockDailyBar(ts_code="000001.SZ", trade_date=pd.Timestamp("2026-01-05").date(), open=10, high=11, low=9, close=10, pre_close=10))
            db.add(StockAdjustFactor(ts_code="000001.SZ", trade_date=pd.Timestamp("2026-01-05").date(), adj_factor=1))
            db.add(StockLimitPrice(ts_code="000001.SZ", trade_date=pd.Timestamp("2026-01-05").date(), pre_close=10, up_limit=11, down_limit=9))
            db.add(StockSuspendEvent(ts_code="000001.SZ", trade_date=pd.Timestamp("2026-01-06").date(), suspend_type="S", suspend_timing="全天"))
            db.commit()

        universe = build_explicit_universe(["000001.SZ"], as_of_date="2026-01-05", source=str(UNIVERSE_ONE))
        panel = load_stock_research_panel(
            engine,
            universe,
            pd.Timestamp("2026-01-05").date(),
            pd.Timestamp("2026-01-06").date(),
        )

        self.assertEqual(len(panel), 2)
        self.assertTrue(panel.iloc[1]["is_valuation_carried"])
        self.assertEqual(panel.iloc[1]["valuation_carry_reason"], "full_day_suspension")
        self.assertTrue(pd.isna(panel.iloc[1]["adj_close"]))
        self.assertFalse(panel.iloc[1]["is_sellable_at_open"])

    def test_repository_does_not_append_suspension_after_delisting(self):
        engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(engine)
        with Session(engine) as db:
            db.add(
                StockListing(
                    ts_code="000001.SZ",
                    symbol="000001",
                    name="合成退市股",
                    list_status="D",
                    list_date=pd.Timestamp("2020-01-01").date(),
                    delist_date=pd.Timestamp("2026-01-05").date(),
                )
            )
            db.add(StockDailyBar(ts_code="000001.SZ", trade_date=pd.Timestamp("2026-01-05").date(), open=10, high=10, low=10, close=10, pre_close=10))
            db.add(StockAdjustFactor(ts_code="000001.SZ", trade_date=pd.Timestamp("2026-01-05").date(), adj_factor=1))
            db.add(StockLimitPrice(ts_code="000001.SZ", trade_date=pd.Timestamp("2026-01-05").date(), pre_close=10, up_limit=11, down_limit=9))
            db.add(StockSuspendEvent(ts_code="000001.SZ", trade_date=pd.Timestamp("2026-01-06").date(), suspend_type="S", suspend_timing="全天"))
            db.commit()

        universe = build_explicit_universe(["000001.SZ"], as_of_date="2026-01-05", source=str(UNIVERSE_ONE))
        panel = load_stock_research_panel(
            engine,
            universe,
            pd.Timestamp("2026-01-05").date(),
            pd.Timestamp("2026-01-06").date(),
        )

        self.assertEqual(panel["trade_date"].dt.strftime("%Y-%m-%d").tolist(), ["2026-01-05"])

    def test_repository_rejects_delisted_status_without_delist_date(self):
        engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(engine)
        day = pd.Timestamp("2026-01-05").date()
        with Session(engine) as db:
            db.add(
                StockListing(
                    ts_code="000001.SZ",
                    symbol="000001",
                    name="缺退市边界",
                    list_status="D",
                    list_date=pd.Timestamp("2020-01-01").date(),
                    delist_date=None,
                )
            )
            db.add(StockDailyBar(ts_code="000001.SZ", trade_date=day, open=10, high=10, low=10, close=10))
            db.add(StockAdjustFactor(ts_code="000001.SZ", trade_date=day, adj_factor=1))
            db.add(StockLimitPrice(ts_code="000001.SZ", trade_date=day, up_limit=11, down_limit=9))
            db.commit()

        universe = build_explicit_universe(
            ["000001.SZ"],
            as_of_date="2026-01-05",
            source=str(UNIVERSE_ONE),
        )
        with self.assertRaisesRegex(ValueError, "delist_date"):
            load_stock_research_panel(engine, universe, day, day)

    def test_repository_filters_rows_by_historical_member_artifact(self):
        engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(engine)
        dates = [pd.Timestamp("2026-01-05").date(), pd.Timestamp("2026-01-06").date()]
        with Session(engine) as db:
            for code in ("A.SZ", "B.SZ"):
                db.add(
                    StockListing(
                        ts_code=code,
                        symbol=code[0],
                        name=f"合成{code[0]}",
                        list_status="L",
                        list_date=pd.Timestamp("2020-01-01").date(),
                    )
                )
                for day in dates:
                    db.add(StockDailyBar(ts_code=code, trade_date=day, open=10, high=10, low=10, close=10, pre_close=10))
                    db.add(StockAdjustFactor(ts_code=code, trade_date=day, adj_factor=1))
                    db.add(StockLimitPrice(ts_code=code, trade_date=day, pre_close=10, up_limit=11, down_limit=9))
            db.commit()
        memberships = pd.DataFrame(
            [
                {"index_code": "SYN.SI", "con_code": "A.SZ", "in_date": "2020-01-01", "out_date": "2026-01-05"},
                {"index_code": "SYN.SI", "con_code": "B.SZ", "in_date": "2026-01-06", "out_date": None},
            ]
        )
        listings = pd.DataFrame(
            [
                {"ts_code": "A.SZ", "list_date": "2020-01-01", "delist_date": None},
                {"ts_code": "B.SZ", "list_date": "2020-01-01", "delist_date": None},
            ]
        )
        universe = build_historical_universe(
            memberships,
            listings,
            dates,
            "SYN.SI",
            source=str(HISTORICAL_SOURCE),
        )

        panel = load_stock_research_panel(engine, universe, dates[0], dates[-1])

        self.assertEqual(
            panel[["trade_date", "ts_code"]].to_dict("records"),
            [
                {"trade_date": pd.Timestamp("2026-01-05"), "ts_code": "A.SZ"},
                {"trade_date": pd.Timestamp("2026-01-06"), "ts_code": "B.SZ"},
            ],
        )
        self.assertEqual(panel.attrs["universeProvenance"]["status"], "ready")


class QuantResearchUniverseTest(unittest.TestCase):
    def test_explicit_universe_hash_is_order_independent(self):
        first = build_explicit_universe(
            ["000002.SZ", "000001.SZ"],
            as_of_date="2025-12-31",
            source=str(UNIVERSE_TWO),
        )
        second = build_explicit_universe(
            ["000001.SZ", "000002.SZ"],
            as_of_date="2025-12-31",
            source=str(UNIVERSE_TWO),
        )

        self.assertEqual(first["members"], ["000001.SZ", "000002.SZ"])
        self.assertEqual(first["universeHash"], second["universeHash"])
        self.assertEqual(first["memberArtifact"]["count"], 2)
        self.assertEqual(first["memberArtifact"]["sha256"], second["memberArtifact"]["sha256"])

    def test_cross_section_requires_historical_universe_provenance(self):
        valid = build_explicit_universe(
            ["000001.SZ"],
            as_of_date="2025-12-31",
            source=str(UNIVERSE_ONE),
        )
        missing = {**valid, "asOfDate": None}
        future = build_explicit_universe(
            ["000001.SZ"],
            as_of_date="2026-06-01",
            source=str(UNIVERSE_ONE),
        )
        historical = valid

        self.assertIn("missing_as_of_date", evaluate_universe_provenance(missing, "a_share_cross_section", "2026-01-01")["blockers"])
        self.assertIn("survivorship_risk", evaluate_universe_provenance(future, "a_share_cross_section", "2026-01-01")["blockers"])
        historical_result = evaluate_universe_provenance(historical, "a_share_cross_section", "2026-01-01")
        self.assertIn("static_universe", historical_result["warnings"])
        self.assertTrue(historical_result["survivorshipRisk"])

        forged = {"mode": "historical_membership", "source": "x"}
        self.assertEqual(evaluate_universe_provenance(forged, "a_share_cross_section", "2026-01-01")["status"], "blocked")

        with self.assertRaisesRegex(ValueError, "source 文件不存在"):
            build_explicit_universe(
                ["000001.SZ"],
                as_of_date="2025-12-31",
                source="/definitely/not/a/real.csv",
            )
        with self.assertRaisesRegex(ValueError, "as_of_date"):
            build_explicit_universe(
                ["000001.SZ"],
                as_of_date=pd.NaT,
                source=str(UNIVERSE_ONE),
            )

    def test_builds_historical_membership_by_trade_date(self):
        memberships = pd.DataFrame(
            [
                {"index_code": "801080.SI", "con_code": "A", "in_date": "2020-01-01", "out_date": "2026-01-05"},
                {"index_code": "801080.SI", "con_code": "B", "in_date": "2026-01-06", "out_date": None},
            ]
        )

        listings = pd.DataFrame(
            [
                {"ts_code": "A", "list_date": "2020-01-01", "delist_date": "2026-01-05"},
                {"ts_code": "B", "list_date": "2026-01-07", "delist_date": None},
            ]
        )
        panel = build_historical_membership_panel(
            memberships,
            listings,
            ["2026-01-05", "2026-01-06", "2026-01-07"],
            "801080.SI",
        )

        self.assertEqual(panel.to_dict("records"), [
            {"trade_date": pd.Timestamp("2026-01-05"), "ts_code": "A"},
            {"trade_date": pd.Timestamp("2026-01-07"), "ts_code": "B"},
        ])

        universe = build_historical_universe(
            memberships,
            listings,
            ["2026-01-05", "2026-01-06", "2026-01-07"],
            "801080.SI",
            source=str(HISTORICAL_SOURCE),
        )
        self.assertEqual(evaluate_universe_provenance(universe, "a_share_cross_section", "2026-01-05")["status"], "ready")
        tampered = {**universe, "memberArtifact": {**universe["memberArtifact"], "count": 99}}
        self.assertEqual(evaluate_universe_provenance(tampered, "a_share_cross_section", "2026-01-05")["status"], "blocked")


class QuantResearchPortfolioTest(unittest.TestCase):
    def test_accepts_verified_gzip_calendar_artifact(self):
        records = [
            {"exchange": "SSE", "cal_date": "2026-01-02", "is_open": True},
            {"exchange": "SSE", "cal_date": "2026-01-03", "is_open": False},
        ]
        source = Path(TEST_CALENDAR_DIR.name) / "calendar.csv.gz"
        with gzip.GzipFile(filename=source, mode="wb", mtime=0) as compressed:
            compressed.write(canonical_trade_calendar_bytes(records))

        calendar = build_open_trade_calendar(
            records,
            source_artifact=str(source),
            source_artifact_sha256=trade_calendar_content_sha256(records),
        )

        self.assertEqual(calendar.open_dates, ("2026-01-02",))

    def test_executes_close_signal_at_next_trade_open(self):
        prices = pd.DataFrame(
            [
                {"ts_code": "A", "trade_date": "2026-01-02", "adj_open": 10, "adj_close": 10, "is_buyable_at_open": True, "is_sellable_at_open": True},
                {"ts_code": "A", "trade_date": "2026-01-05", "adj_open": 11, "adj_close": 12, "is_buyable_at_open": True, "is_sellable_at_open": True},
                {"ts_code": "A", "trade_date": "2026-01-06", "adj_open": 12, "adj_close": 12, "is_buyable_at_open": True, "is_sellable_at_open": True},
            ]
        )
        targets = formal_targets([{"signal_date": "2026-01-02", "ts_code": "A", "target_weight": 1.0}])

        nav = simulate_target_weights(
            prices,
            targets,
            trade_calendar=open_dates(prices),
            cost=CostModel(buy_rate=0, sell_rate=0, slippage_rate=0),
        )

        self.assertEqual(nav.iloc[0]["nav"], 1.0)
        self.assertEqual(str(nav.iloc[1]["executed_signal_date"].date()), "2026-01-02")
        self.assertAlmostEqual(nav.iloc[1]["nav"], 12 / 11)

    def test_rejects_missing_price_for_held_asset(self):
        prices = pd.DataFrame(
            [
                {"ts_code": "A", "trade_date": "2026-01-02", "adj_open": 10, "adj_close": 10, "is_buyable_at_open": True, "is_sellable_at_open": True},
                {"ts_code": "A", "trade_date": "2026-01-05", "adj_open": 10, "adj_close": 10, "is_buyable_at_open": True, "is_sellable_at_open": True},
                {"ts_code": "B", "trade_date": "2026-01-06", "adj_open": 10, "adj_close": 10, "is_buyable_at_open": True, "is_sellable_at_open": True},
            ]
        )
        targets = formal_targets([{"signal_date": "2026-01-02", "ts_code": "A", "target_weight": 1.0}])

        with self.assertRaisesRegex(ValueError, "缺少持仓价格"):
            simulate_target_weights(
                prices,
                targets,
                trade_calendar=open_dates(prices),
                cost=CostModel(buy_rate=0, sell_rate=0, slippage_rate=0),
            )

    def test_zero_weight_target_moves_portfolio_to_cash(self):
        prices = pd.DataFrame(
            [
                {"ts_code": "A", "trade_date": "2026-01-02", "adj_open": 10, "adj_close": 10, "is_buyable_at_open": True, "is_sellable_at_open": True},
                {"ts_code": "A", "trade_date": "2026-01-05", "adj_open": 10, "adj_close": 10, "is_buyable_at_open": True, "is_sellable_at_open": True},
                {"ts_code": "A", "trade_date": "2026-01-06", "adj_open": 10, "adj_close": 10, "is_buyable_at_open": True, "is_sellable_at_open": True},
            ]
        )
        targets = formal_targets(
            [
                {"signal_date": "2026-01-02", "ts_code": "A", "target_weight": 1.0},
                {"signal_date": "2026-01-05", "ts_code": "A", "target_weight": 0.0},
            ]
        )

        nav = simulate_target_weights(
            prices,
            targets,
            trade_calendar=open_dates(prices),
            cost=CostModel(buy_rate=0, sell_rate=0, slippage_rate=0),
        )

        self.assertEqual(nav.iloc[-1]["gross_exposure"], 0.0)
        self.assertEqual(nav.iloc[-1]["cash_weight"], 1.0)

    def test_unfilled_buy_is_recorded_without_fabricating_execution(self):
        prices = pd.DataFrame(
            [
                {"ts_code": "A", "trade_date": "2026-01-02", "adj_open": 10, "adj_close": 10, "is_buyable_at_open": True, "is_sellable_at_open": True},
                {"ts_code": "A", "trade_date": "2026-01-05", "adj_open": 11, "adj_close": 11, "is_buyable_at_open": False, "is_sellable_at_open": True},
            ]
        )
        targets = formal_targets([{"signal_date": "2026-01-02", "ts_code": "A", "target_weight": 1.0}])

        nav = simulate_target_weights(
            prices,
            targets,
            trade_calendar=open_dates(prices),
            cost=CostModel(buy_rate=0, sell_rate=0, slippage_rate=0),
        )

        self.assertEqual(nav.iloc[-1]["gross_exposure"], 0.0)
        self.assertEqual(nav.iloc[-1]["blocked_buys"], "A")
        self.assertEqual(nav.iloc[-1]["unfilled_target_weight"], 1.0)

    def test_unfilled_sell_keeps_frozen_position(self):
        prices = pd.DataFrame(
            [
                {"ts_code": "A", "trade_date": "2026-01-02", "adj_open": 10, "adj_close": 10, "is_buyable_at_open": True, "is_sellable_at_open": True},
                {"ts_code": "A", "trade_date": "2026-01-05", "adj_open": 10, "adj_close": 10, "is_buyable_at_open": True, "is_sellable_at_open": True},
                {"ts_code": "A", "trade_date": "2026-01-06", "adj_open": 10, "adj_close": 10, "is_buyable_at_open": True, "is_sellable_at_open": False},
            ]
        )
        targets = formal_targets(
            [
                {"signal_date": "2026-01-02", "ts_code": "A", "target_weight": 1.0},
                {"signal_date": "2026-01-05", "ts_code": "A", "target_weight": 0.0},
            ]
        )

        nav = simulate_target_weights(
            prices,
            targets,
            trade_calendar=open_dates(prices),
            cost=CostModel(buy_rate=0, sell_rate=0, slippage_rate=0),
        )

        self.assertEqual(nav.iloc[-1]["gross_exposure"], 1.0)
        self.assertEqual(nav.iloc[-1]["blocked_sells"], "A")
        self.assertEqual(nav.iloc[-1]["unfilled_target_weight"], 1.0)

    def test_full_day_suspension_explicitly_carries_last_valuation(self):
        prices = pd.DataFrame(
            [
                {"ts_code": "A", "trade_date": "2026-01-02", "adj_open": 10, "adj_close": 10, "is_buyable_at_open": True, "is_sellable_at_open": True, "is_valuation_carried": False, "valuation_carry_reason": "", "is_suspended": False, "is_suspended_at_open": False},
                {"ts_code": "A", "trade_date": "2026-01-05", "adj_open": 10, "adj_close": 10, "is_buyable_at_open": True, "is_sellable_at_open": True, "is_valuation_carried": False, "valuation_carry_reason": "", "is_suspended": False, "is_suspended_at_open": False},
                {"ts_code": "A", "trade_date": "2026-01-06", "adj_open": None, "adj_close": None, "is_buyable_at_open": False, "is_sellable_at_open": False, "is_valuation_carried": True, "valuation_carry_reason": "full_day_suspension", "is_suspended": True, "is_suspended_at_open": True},
            ]
        )
        targets = formal_targets(
            [
                {"signal_date": "2026-01-02", "ts_code": "A", "target_weight": 1.0},
                {"signal_date": "2026-01-05", "ts_code": "A", "target_weight": 0.0},
            ]
        )

        nav = simulate_target_weights(
            prices,
            targets,
            trade_calendar=open_dates(prices),
            cost=CostModel(buy_rate=0, sell_rate=0, slippage_rate=0),
        )

        self.assertEqual(nav.iloc[-1]["nav"], 1.0)
        self.assertEqual(nav.iloc[-1]["gross_exposure"], 1.0)
        self.assertEqual(nav.iloc[-1]["carried_valuation_count"], 1)
        self.assertEqual(nav.iloc[-1]["blocked_sells"], "A")

    def test_rejects_bare_carry_boolean_without_suspension_evidence(self):
        prices = pd.DataFrame(
            [
                {"ts_code": "A", "trade_date": "2026-01-02", "adj_open": 10, "adj_close": 10, "is_buyable_at_open": True, "is_sellable_at_open": True, "is_valuation_carried": False},
                {"ts_code": "A", "trade_date": "2026-01-05", "adj_open": None, "adj_close": None, "is_buyable_at_open": False, "is_sellable_at_open": False, "is_valuation_carried": True},
            ]
        )
        targets = formal_targets([{"signal_date": "2026-01-02", "ts_code": "A", "target_weight": 1.0}])

        with self.assertRaisesRegex(ValueError, "full_day_suspension|沿用证据"):
            simulate_target_weights(prices, targets, trade_calendar=open_dates(prices))

    def test_missing_official_open_day_cannot_silently_delay_execution(self):
        prices = pd.DataFrame(
            [
                {"ts_code": "A", "trade_date": "2026-01-02", "adj_open": 10, "adj_close": 10, "is_buyable_at_open": True, "is_sellable_at_open": True},
                {"ts_code": "A", "trade_date": "2026-01-06", "adj_open": 10, "adj_close": 10, "is_buyable_at_open": True, "is_sellable_at_open": True},
            ]
        )
        targets = formal_targets([{"signal_date": "2026-01-02", "ts_code": "A", "target_weight": 1.0}])

        with self.assertRaisesRegex(ValueError, "2026-01-05|开市日"):
            simulate_target_weights(
                prices,
                targets,
                trade_calendar=calendar_for_dates(["2026-01-02", "2026-01-05", "2026-01-06"]),
            )

        with self.assertRaisesRegex(ValueError, "OpenTradeCalendar|裸交易日"):
            simulate_target_weights(prices, targets, trade_calendar=["2026-01-02", "2026-01-06"])

        full_records = [
            {"exchange": "SSE", "cal_date": value, "is_open": True}
            for value in ("2026-01-02", "2026-01-05", "2026-01-06")
        ]
        subset = [full_records[0], full_records[2]]
        source = Path(TEST_CALENDAR_DIR.name) / "complete-calendar.csv"
        source.write_bytes(canonical_trade_calendar_bytes(full_records))
        with self.assertRaisesRegex(ValueError, "source_artifact 实际内容"):
            build_open_trade_calendar(
                subset,
                source_artifact=str(source),
                source_artifact_sha256=trade_calendar_content_sha256(full_records),
            )

        with self.assertRaisesRegex(ValueError, "source_artifact 文件不存在"):
            build_open_trade_calendar(
                subset,
                source_artifact="/definitely/not/existing/official-calendar.csv",
                source_artifact_sha256=trade_calendar_content_sha256(subset),
            )

        verified = build_open_trade_calendar(
            full_records,
            source_artifact=str(source),
            source_artifact_sha256=trade_calendar_content_sha256(full_records),
        )
        source.write_bytes(canonical_trade_calendar_bytes(subset))
        with self.assertRaisesRegex(ValueError, "source_artifact 实际内容"):
            simulate_target_weights(prices, targets, trade_calendar=verified)

    def test_formal_targets_require_nonempty_available_date(self):
        prices = pd.DataFrame(
            [
                {"ts_code": "A", "trade_date": "2026-01-02", "adj_open": 10, "adj_close": 10, "is_buyable_at_open": True, "is_sellable_at_open": True},
                {"ts_code": "A", "trade_date": "2026-01-05", "adj_open": 10, "adj_close": 10, "is_buyable_at_open": True, "is_sellable_at_open": True},
            ]
        )
        for targets in (
            pd.DataFrame([{"signal_date": "2026-01-02", "ts_code": "A", "target_weight": 1.0}]),
            pd.DataFrame([{"signal_date": "2026-01-02", "available_date": None, "ts_code": "A", "target_weight": 1.0}]),
        ):
            with self.subTest(columns=targets.columns.tolist()):
                with self.assertRaisesRegex(ValueError, "available_date"):
                    simulate_target_weights(prices, targets, trade_calendar=open_dates(prices))

        numeric_date = pd.DataFrame(
            [{"signal_date": "2026-01-02", "available_date": 0, "ts_code": "A", "target_weight": 1.0}]
        )
        with self.assertRaisesRegex(ValueError, "available_date"):
            simulate_target_weights(prices, numeric_date, trade_calendar=open_dates(prices))

    def test_target_dates_are_normalized_before_duplicate_and_total_checks(self):
        prices = pd.DataFrame(
            [
                {"ts_code": code, "trade_date": trade_date, "adj_open": 10, "adj_close": 10, "is_buyable_at_open": True, "is_sellable_at_open": True}
                for trade_date in ("2026-01-02", "2026-01-05")
                for code in ("A", "B")
            ]
        )
        duplicate = pd.DataFrame(
            [
                {"signal_date": "2026-01-02", "available_date": "2026-01-02", "ts_code": "A", "target_weight": 0.2},
                {"signal_date": pd.Timestamp("2026-01-02"), "available_date": pd.Timestamp("2026-01-02"), "ts_code": "a", "target_weight": 0.8},
            ]
        )
        with self.assertRaisesRegex(ValueError, "重复"):
            simulate_target_weights(prices, duplicate, trade_calendar=open_dates(prices))

        overweight = pd.DataFrame(
            [
                {"signal_date": "2026-01-02", "available_date": "2026-01-02", "ts_code": "A", "target_weight": 0.7},
                {"signal_date": pd.Timestamp("2026-01-02"), "available_date": pd.Timestamp("2026-01-02"), "ts_code": "B", "target_weight": 0.7},
            ]
        )
        with self.assertRaisesRegex(ValueError, "不能超过 1"):
            simulate_target_weights(prices, overweight, trade_calendar=open_dates(prices))

    def test_string_false_cannot_forge_carry_evidence(self):
        prices = pd.DataFrame(
            [
                {"ts_code": "A", "trade_date": "2026-01-02", "adj_open": 10, "adj_close": 10, "is_buyable_at_open": True, "is_sellable_at_open": True, "is_valuation_carried": False, "valuation_carry_reason": "", "is_suspended": False, "is_suspended_at_open": False},
                {"ts_code": "A", "trade_date": "2026-01-05", "adj_open": None, "adj_close": None, "is_buyable_at_open": False, "is_sellable_at_open": False, "is_valuation_carried": True, "valuation_carry_reason": "full_day_suspension", "is_suspended": "False", "is_suspended_at_open": "False"},
            ]
        )
        targets = formal_targets([{"signal_date": "2026-01-02", "ts_code": "A", "target_weight": 1.0}])
        with self.assertRaisesRegex(ValueError, "禁止字符串真值|必须是 bool"):
            simulate_target_weights(prices, targets, trade_calendar=open_dates(prices))

    def test_rejects_feature_available_after_signal(self):
        prices = pd.DataFrame(
            [
                {"ts_code": "A", "trade_date": "2026-01-02", "adj_open": 10, "adj_close": 10, "is_buyable_at_open": True, "is_sellable_at_open": True},
                {"ts_code": "A", "trade_date": "2026-01-05", "adj_open": 10, "adj_close": 10, "is_buyable_at_open": True, "is_sellable_at_open": True},
            ]
        )
        targets = pd.DataFrame(
            [{"signal_date": "2026-01-02", "available_date": "2026-01-05", "ts_code": "A", "target_weight": 1.0}]
        )

        with self.assertRaisesRegex(ValueError, "available_date"):
            simulate_target_weights(prices, targets, trade_calendar=open_dates(prices))

    def test_transaction_cost_reaches_exact_post_cost_target_without_repeat_churn(self):
        prices = pd.DataFrame(
            [
                {"ts_code": "A", "trade_date": "2026-01-02", "adj_open": 10, "adj_close": 10, "is_buyable_at_open": True, "is_sellable_at_open": True},
                {"ts_code": "A", "trade_date": "2026-01-05", "adj_open": 10, "adj_close": 10, "is_buyable_at_open": True, "is_sellable_at_open": True},
                {"ts_code": "A", "trade_date": "2026-01-06", "adj_open": 10, "adj_close": 10, "is_buyable_at_open": True, "is_sellable_at_open": True},
            ]
        )
        targets = formal_targets(
            [
                {"signal_date": "2026-01-02", "ts_code": "A", "target_weight": 0.5},
                {"signal_date": "2026-01-05", "ts_code": "A", "target_weight": 0.5},
            ]
        )

        nav = simulate_target_weights(prices, targets, trade_calendar=open_dates(prices))

        self.assertLess(nav.iloc[1]["nav"], 1.0)
        self.assertAlmostEqual(nav.iloc[1]["gross_exposure"], 0.5, places=12)
        self.assertAlmostEqual(nav.iloc[1]["cash_weight"], 0.5, places=12)
        self.assertAlmostEqual(nav.iloc[-1]["traded_weight"], 0.0, places=12)
        self.assertAlmostEqual(nav.iloc[-1]["transaction_cost_rate"], 0.0, places=12)
        self.assertEqual(nav.iloc[-1]["unfilled_target_weight"], 0.0)


class QuantResearchEvaluationTest(unittest.TestCase):
    def test_builds_absolute_and_benchmark_metrics(self):
        nav = pd.DataFrame(
            [
                {"trade_date": "2026-01-02", "nav": 1.0},
                {"trade_date": "2026-01-05", "nav": 1.1},
                {"trade_date": "2026-01-06", "nav": 1.0},
            ]
        )
        benchmark = pd.DataFrame(
            [
                {"trade_date": "2026-01-02", "nav": 1.0},
                {"trade_date": "2026-01-05", "nav": 1.05},
                {"trade_date": "2026-01-06", "nav": 1.02},
            ]
        )

        summary = summarize_performance(nav, benchmark)

        self.assertAlmostEqual(summary["totalReturn"], 0.0)
        self.assertAlmostEqual(summary["maxDrawdown"], 1 / 1.1 - 1)
        self.assertIn("trackingError", summary)
        self.assertIn("informationRatio", summary)

    def test_excess_return_uses_only_overlapping_benchmark_dates(self):
        nav = pd.DataFrame(
            [
                {"trade_date": "2026-01-01", "nav": 0.5},
                {"trade_date": "2026-01-02", "nav": 1.0},
                {"trade_date": "2026-01-05", "nav": 1.1},
            ]
        )
        benchmark = pd.DataFrame(
            [
                {"trade_date": "2026-01-02", "nav": 1.0},
                {"trade_date": "2026-01-05", "nav": 1.05},
            ]
        )

        summary = summarize_performance(nav, benchmark)

        self.assertAlmostEqual(summary["excessTotalReturn"], 0.05)

    def test_walk_forward_windows_never_overlap_train_and_test(self):
        dates = pd.bdate_range("2025-01-01", periods=18)

        anchored = build_walk_forward_windows(dates, train_periods=8, test_periods=4, step_periods=4, anchored=True)
        rolling = build_walk_forward_windows(dates, train_periods=8, test_periods=4, step_periods=4, anchored=False)

        self.assertEqual(len(anchored), 2)
        self.assertEqual(len(rolling), 2)
        for window in anchored + rolling:
            self.assertLess(window.train_end, window.test_start)
        self.assertEqual(anchored[1].train_start, dates[0])
        self.assertGreater(rolling[1].train_start, dates[0])

    def test_manifest_is_reproducible_and_research_only(self):
        generated_at = datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc)
        first = build_run_manifest(
            strategy_id="teaching-baseline",
            config={"lookback": 20, "topN": 5},
            data_snapshot={"stockDailyBars": {"maxDate": "2026-07-09", "rows": 100}},
            git_commit="abc123",
            limitations=["no_real_broker"],
            generated_at=generated_at,
        )
        second = build_run_manifest(
            strategy_id="teaching-baseline",
            config={"topN": 5, "lookback": 20},
            data_snapshot={"stockDailyBars": {"maxDate": "2026-07-09", "rows": 100}},
            git_commit="abc123",
            limitations=["no_real_broker"],
            generated_at=generated_at,
        )

        self.assertEqual(first["configSha256"], second["configSha256"])
        self.assertTrue(first["boundaries"]["researchOnly"])
        self.assertFalse(first["boundaries"]["executionEnabled"])

    def test_readiness_separates_etf_and_stock_research(self):
        available = {
            "trade_calendars",
            "funds",
            "fund_daily_bars",
            "fund_adjust_factors",
            "indices",
            "index_daily_bars",
            "stocks",
            "stock_daily_bars",
            "stock_adjust_factors",
        }
        counts = {table: 1 for table in available}

        etf = evaluate_research_readiness("etf_time_series", available, counts)
        stocks = evaluate_research_readiness("a_share_cross_section", available, counts)

        self.assertEqual(etf["level"], "inventory")
        self.assertEqual(etf["status"], "inventory_available")
        self.assertFalse(etf["researchReady"])
        self.assertEqual(stocks["status"], "inventory_incomplete")
        self.assertIn("stock_listings", stocks["missingTables"])
        self.assertIn("stock_limit_prices", stocks["missingTables"])

    def test_strict_financial_research_is_blocked_without_revision_history(self):
        available = {
            "trade_calendars",
            "stocks",
            "stock_daily_bars",
            "stock_adjust_factors",
            "stock_financial_indicators",
            "indices",
            "index_daily_bars",
            "stock_listings",
            "stock_limit_prices",
            "stock_suspend_events",
        }
        result = evaluate_research_readiness(
            "a_share_cross_section",
            available,
            {table: 1 for table in available},
            uses_financials=True,
            strict_point_in_time=True,
            financial_revision_history_available=False,
        )

        self.assertEqual(result["status"], "inventory_incomplete")
        self.assertFalse(result["researchReady"])
        self.assertIn("financial_revision_history_unavailable", result["blockers"])
        self.assertIn("historical_financial_revisions_not_reconstructable", result["limitations"])


if __name__ == "__main__":
    unittest.main()
