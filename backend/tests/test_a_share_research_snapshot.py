from __future__ import annotations

from datetime import date
from pathlib import Path
import tempfile
import unittest

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from backend.app.data_quality.contracts import QualityCheckContract
from backend.app.data_quality.runner import run_data_quality_check
from backend.app.database import Base
from backend.app.models import (
    IndustryClassification,
    IndustryMember,
    StockAdjustFactor,
    StockDailyBar,
    StockLimitPrice,
    StockListing,
    TradeCalendar,
)
from backend.app.quant_research.artifacts import read_canonical_csv_gz
from backend.app.quant_research.snapshot import (
    SnapshotCapacityPolicy,
    SnapshotCapacityError,
    SnapshotError,
    freeze_input_snapshot,
)
from backend.app.quant_research.universe import (
    build_industry_level_membership_universe,
    build_industry_membership_universe,
)
from backend.tests.research_test_support import seed_a_share_snapshot_database


class AShareResearchSnapshotTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.engine = create_engine(
            f"sqlite+pysqlite:///{Path(self.tmp.name) / 'a-share.sqlite'}"
        )
        Base.metadata.create_all(self.engine)
        self._seed_complete_slice()

    def tearDown(self):
        self.engine.dispose()
        self.tmp.cleanup()

    def test_industry_quality_resolves_daily_members_without_inline_current_list(self):
        contract = self._contract()
        self.assertEqual(contract.universe, ())
        with Session(self.engine) as db:
            report = run_data_quality_check(db, contract, code_commit="a-share-test")

        self.assertEqual(report["status"], "ready")
        self.assertEqual(report["config"]["universeSource"], "industry_members")
        self.assertEqual(report["config"]["universeSourceKey"], "SYNIND.SI")
        self.assertEqual(report["config"]["universeMemberCount"], 4)
        self.assertEqual(report["config"]["universeUniqueMemberCount"], 2)
        self.assertRegex(report["config"]["universeMemberSha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(report["universeHash"], report["config"]["universeHash"])

    def test_quality_uses_membership_dates_and_excludes_administrative_delist_date(self):
        with Session(self.engine) as db:
            second_member = db.scalar(
                select(IndustryMember).where(IndustryMember.con_code == "SYN002.SH")
            )
            second_member.in_date = date(2026, 1, 5)
            first_listing = db.get(StockListing, "SYN001.SZ")
            first_listing.list_status = "D"
            first_listing.delist_date = date(2026, 1, 5)
            for model, code, trade_date in (
                (StockDailyBar, "SYN002.SH", date(2026, 1, 2)),
                (StockAdjustFactor, "SYN002.SH", date(2026, 1, 2)),
                (StockLimitPrice, "SYN002.SH", date(2026, 1, 2)),
                (StockDailyBar, "SYN001.SZ", date(2026, 1, 5)),
                (StockAdjustFactor, "SYN001.SZ", date(2026, 1, 5)),
                (StockLimitPrice, "SYN001.SZ", date(2026, 1, 5)),
            ):
                row = db.scalar(
                    select(model).where(
                        model.ts_code == code,
                        model.trade_date == trade_date,
                    )
                )
                db.delete(row)
            db.commit()

            report = run_data_quality_check(
                db,
                self._contract(),
                code_commit="a-share-test",
            )

        self.assertEqual(report["status"], "ready")
        coverage = next(
            item
            for item in report["results"]
            if item["ruleId"] == "calendar.daily_bar_coverage"
        )
        self.assertEqual(coverage["failedRows"], 0)

    def test_quality_does_not_require_limit_price_after_industry_membership_ends(self):
        with Session(self.engine) as db:
            member = db.scalar(
                select(IndustryMember).where(
                    IndustryMember.con_code == "SYN001.SZ"
                )
            )
            member.out_date = date(2026, 1, 2)
            limit_price = db.scalar(
                select(StockLimitPrice).where(
                    StockLimitPrice.ts_code == "SYN001.SZ",
                    StockLimitPrice.trade_date == date(2026, 1, 5),
                )
            )
            db.delete(limit_price)
            db.commit()

            report = run_data_quality_check(
                db,
                self._contract(),
                code_commit="a-share-test",
            )

        self.assertEqual(report["status"], "ready")
        coverage = next(
            item
            for item in report["results"]
            if item["ruleId"] == "calendar.limit_price_coverage"
        )
        self.assertEqual(coverage["failedRows"], 0)

    def test_snapshot_re_resolves_membership_and_rejects_old_quality_after_change(self):
        with Session(self.engine) as db:
            report = run_data_quality_check(db, self._contract(), code_commit="a-share-test")
            config = self._config(report["qualityRunId"])
            snapshot = freeze_input_snapshot(
                db,
                config,
                Path(self.tmp.name) / "snapshots",
                capacity_policy=SnapshotCapacityPolicy(min_remaining_bytes=0),
            )
            self.assertEqual(snapshot.manifest["scope"], "a_share_cross_section")
            self.assertEqual(
                set(snapshot.manifest["tableArtifacts"]),
                {
                    "trade_calendars",
                    "stock_listings",
                    "stock_daily_bars",
                    "stock_adjust_factors",
                    "stock_limit_prices",
                    "stock_suspend_events",
                    "industry_members",
                    "indices",
                    "index_daily_bars",
                    "universe",
                },
            )

            member = db.scalar(
                select(IndustryMember).where(IndustryMember.con_code == "SYN002.SH")
            )
            member.out_date = date(2026, 1, 2)
            db.commit()
            with self.assertRaisesRegex(SnapshotError, "成员|universe|质量"):
                freeze_input_snapshot(
                    db,
                    config,
                    Path(self.tmp.name) / "changed-snapshots",
                    capacity_policy=SnapshotCapacityPolicy(min_remaining_bytes=0),
                )

            fresh_report = run_data_quality_check(
                db,
                self._contract(),
                code_commit="a-share-test",
            )
            self.assertEqual(fresh_report["status"], "ready")
            self.assertNotEqual(fresh_report["universeHash"], report["universeHash"])
            fresh_snapshot = freeze_input_snapshot(
                db,
                self._config(fresh_report["qualityRunId"]),
                Path(self.tmp.name) / "fresh-snapshots",
                capacity_policy=SnapshotCapacityPolicy(min_remaining_bytes=0),
            )
            self.assertEqual(
                fresh_snapshot.manifest["universeHash"],
                fresh_report["universeHash"],
            )

    def test_snapshot_freezes_industry_classification_and_daily_industry_identity(self):
        with Session(self.engine) as db:
            classification = db.get(IndustryClassification, "SYNIND.SI")
            classification.src = "SW2021"
            db.commit()
            contract = QualityCheckContract.create(
                scope="a_share_cross_section",
                start_date=date(2026, 1, 2),
                end_date=date(2026, 1, 5),
                universe=[],
                benchmark="SYNIDX.SH",
                universe_type="industry_level_membership",
                universe_source="industry_classifications+industry_members",
                universe_classification_src="SW2021",
                universe_classification_level="L1",
            )
            report = run_data_quality_check(db, contract, code_commit="a-share-test")
            self.assertEqual(report["status"], "ready")
            config = self._config(report["qualityRunId"])
            config["universe"] = build_industry_level_membership_universe(
                "SW2021",
                "L1",
            )
            snapshot = freeze_input_snapshot(
                db,
                config,
                Path(self.tmp.name) / "industry-level-snapshots",
                capacity_policy=SnapshotCapacityPolicy(min_remaining_bytes=0),
            )

        self.assertIn("industry_classifications", snapshot.manifest["tableArtifacts"])
        self.assertEqual(
            snapshot.manifest["universeSourceArtifact"]["format"],
            "database_industry_level_membership_v1",
        )
        universe = read_canonical_csv_gz(snapshot.path / "inputs" / "universe.csv.gz")
        self.assertEqual(
            list(universe.columns),
            ["trade_date", "ts_code", "industry_index_code"],
        )
        self.assertEqual(set(universe["industry_index_code"]), {"SYNIND.SI"})

    def test_snapshot_excludes_raw_member_delisted_before_window(self):
        with Session(self.engine) as db:
            db.add_all(
                [
                    IndustryMember(
                        index_code="SYNIND.SI",
                        con_code="OLD001.SZ",
                        con_name="窗口前退市成员",
                        in_date=date(2020, 1, 1),
                    ),
                    StockListing(
                        ts_code="OLD001.SZ",
                        symbol="OLD001",
                        name="窗口前退市成员",
                        exchange="SZSE",
                        list_status="D",
                        list_date=date(2020, 1, 1),
                        delist_date=date(2025, 12, 31),
                    ),
                ]
            )
            db.commit()
            report = run_data_quality_check(
                db,
                self._contract(),
                code_commit="a-share-test",
            )
            self.assertEqual(report["status"], "ready")

            snapshot = freeze_input_snapshot(
                db,
                self._config(report["qualityRunId"]),
                Path(self.tmp.name) / "delisted-snapshots",
                capacity_policy=SnapshotCapacityPolicy(min_remaining_bytes=0),
            )

        memberships = read_canonical_csv_gz(
            snapshot.path / "inputs" / "industry_members.csv.gz"
        )
        self.assertNotIn("OLD001.SZ", set(memberships["con_code"]))

    def test_membership_resolution_blocks_empty_gap_overlap_and_missing_listing(self):
        scenarios = (
            "empty_industry",
            "daily_gap",
            "overlap",
            "missing_listing",
        )
        for scenario in scenarios:
            with self.subTest(scenario=scenario):
                with Session(self.engine) as db:
                    if scenario == "empty_industry":
                        contract = self._contract(universe_source_key="EMPTY.SI")
                    elif scenario == "daily_gap":
                        db.add(
                            TradeCalendar(
                                exchange="SSE",
                                cal_date=date(2026, 1, 3),
                                is_open=True,
                            )
                        )
                        members = list(
                            db.scalars(
                                select(IndustryMember).order_by(IndustryMember.con_code)
                            )
                        )
                        members[0].out_date = date(2026, 1, 2)
                        members[1].in_date = date(2026, 1, 5)
                        db.commit()
                        contract = self._contract()
                    elif scenario == "overlap":
                        db.add(
                            IndustryMember(
                                index_code="SYNIND.SI",
                                con_code="SYN001.SZ",
                                con_name="合成一号",
                                in_date=date(2025, 1, 1),
                                out_date=date(2026, 1, 2),
                            )
                        )
                        db.commit()
                        contract = self._contract()
                    else:
                        listing = db.get(StockListing, "SYN002.SH")
                        db.delete(listing)
                        db.commit()
                        contract = self._contract()

                    report = run_data_quality_check(
                        db,
                        contract,
                        code_commit="a-share-test",
                    )
                    self.assertEqual(report["status"], "blocked")
                    self.assertEqual(
                        report["results"][0]["ruleId"],
                        "universe.membership_resolution",
                    )
                self.engine.dispose()
                self.tmp.cleanup()
                self.tmp = tempfile.TemporaryDirectory()
                self.engine = create_engine(
                    f"sqlite+pysqlite:///{Path(self.tmp.name) / 'a-share.sqlite'}"
                )
                Base.metadata.create_all(self.engine)
                self._seed_complete_slice()

    def test_snapshot_capacity_blocks_before_writing_inputs(self):
        root = Path(self.tmp.name) / "capacity-snapshots"
        with Session(self.engine) as db:
            report = run_data_quality_check(db, self._contract(), code_commit="a-share-test")
            with self.assertRaisesRegex(SnapshotCapacityError, "universe_date_pairs=4"):
                freeze_input_snapshot(
                    db,
                    self._config(report["qualityRunId"]),
                    root,
                    capacity_policy=SnapshotCapacityPolicy(
                        min_remaining_bytes=0,
                        max_universe_date_pairs=3,
                    ),
                )
        self.assertEqual(list(root.iterdir()), [])

    def test_industry_contract_rejects_path_inline_members_and_as_of_date(self):
        invalid = (
            {"universe": ["SYN001.SZ"]},
            {"universe_source": "backend/tests/fixtures/quality-universe.txt"},
            {"universe_as_of_date": date(2026, 1, 2)},
            {"universe_source_key": None},
        )
        for override in invalid:
            with self.subTest(override=override), self.assertRaises(ValueError):
                self._contract(**override)

    @staticmethod
    def _contract(**overrides: object) -> QualityCheckContract:
        values: dict[str, object] = {
            "scope": "a_share_cross_section",
            "start_date": date(2026, 1, 2),
            "end_date": date(2026, 1, 5),
            "universe": [],
            "required_datasets": [],
            "benchmark": "SYNIDX.SH",
            "universe_type": "industry_membership",
            "universe_source": "industry_members",
            "universe_source_key": "SYNIND.SI",
            "universe_as_of_date": None,
            "statement_timeout_ms": 5000,
        }
        values.update(overrides)
        return QualityCheckContract.create(**values)

    @staticmethod
    def _config(quality_run_id: str) -> dict[str, object]:
        return {
            "strategyId": "a_share_price_baseline",
            "strategyVersion": "1",
            "scope": "a_share_cross_section",
            "universe": build_industry_membership_universe("SYNIND.SI"),
            "warmupStart": "2026-01-02",
            "startDate": "2026-01-02",
            "endDate": "2026-01-05",
            "benchmark": "SYNIDX.SH",
            "featureParameters": {
                "momentumLongWindow": 120,
                "momentumSkipWindow": 20,
                "volatilityWindow": 60,
            },
            "targetWeightParameters": {
                "rebalanceFrequency": "month_end",
                "topN": 2,
                "maxWeight": "0.5",
            },
            "executionPolicy": {
                "signalPrice": "close",
                "executionPrice": "next_trade_open",
            },
            "costModel": {
                "buyRate": "0",
                "sellRate": "0",
                "slippageRate": "0",
            },
            "randomSeed": 7,
            "timezone": "Asia/Shanghai",
            "qualityRunId": quality_run_id,
            "allowedWarnings": [],
        }

    def _seed_complete_slice(self) -> None:
        with Session(self.engine) as db:
            seed_a_share_snapshot_database(db)


if __name__ == "__main__":
    unittest.main()
