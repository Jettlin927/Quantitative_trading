from __future__ import annotations

import unittest
from datetime import date

from scripts.ops import backfill_a_share_history as backfill


class FakeLimiter:
    def __init__(self) -> None:
        self.calls = 0

    def wait(self) -> None:
        self.calls += 1


class BackfillArgumentTests(unittest.TestCase):
    def test_default_range_starts_in_2012(self) -> None:
        args = backfill.parse_args([])

        self.assertEqual(date(2012, 1, 1), args.start_date)
        self.assertEqual(date.today(), args.end_date)
        self.assertFalse(args.resume)
        self.assertIn("fund_adjust_factors", args.datasets)

    def test_invalid_range_is_rejected(self) -> None:
        args = backfill.parse_args(["--start-date", "2015-01-02", "--end-date", "2015-01-01"])

        with self.assertRaisesRegex(ValueError, "start-date"):
            backfill.validate_args(args)

    def test_year_ranges_cover_requested_boundaries(self) -> None:
        ranges = backfill.iter_year_ranges(date(2012, 6, 1), date(2014, 2, 3))

        self.assertEqual(
            [
                (date(2012, 6, 1), date(2012, 12, 31)),
                (date(2013, 1, 1), date(2013, 12, 31)),
                (date(2014, 1, 1), date(2014, 2, 3)),
            ],
            ranges,
        )


class BackfillRetryTests(unittest.TestCase):
    def test_retry_eventually_returns_result(self) -> None:
        limiter = FakeLimiter()
        attempts = 0

        def operation() -> str:
            nonlocal attempts
            attempts += 1
            if attempts < 3:
                raise RuntimeError("temporary")
            return "ok"

        result = backfill.call_with_retry("smoke", operation, limiter, retries=3, retry_backoff=0)

        self.assertEqual("ok", result)
        self.assertEqual(3, attempts)
        self.assertEqual(3, limiter.calls)

    def test_retry_raises_last_error(self) -> None:
        limiter = FakeLimiter()

        with self.assertRaisesRegex(RuntimeError, "still failing"):
            backfill.call_with_retry(
                "smoke",
                lambda: (_ for _ in ()).throw(RuntimeError("still failing")),
                limiter,
                retries=2,
                retry_backoff=0,
            )

        self.assertEqual(2, limiter.calls)


class BackfillEmptyResultTests(unittest.TestCase):
    def make_dataset(self, allow_empty: bool) -> backfill.DailyDataset:
        return backfill.DailyDataset(
            name="suspend_events" if allow_empty else "daily",
            model=object,
            date_column=object(),
            conflicts=["trade_date"],
            minimum_ratio=0,
            allow_empty=allow_empty,
            fetch=lambda _pro, _day: None,
            convert=lambda row: row,
        )

    def test_empty_suspend_day_is_a_valid_checkpoint(self) -> None:
        backfill.validate_source_rows(self.make_dataset(allow_empty=True), [], expected=3000)

    def test_empty_market_day_is_not_marked_complete(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "no rows"):
            backfill.validate_source_rows(self.make_dataset(allow_empty=False), [], expected=3000)


class FundAdjustmentCoverageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.required = backfill.DateCoverage("510300.SH", date(2012, 1, 4), date(2026, 7, 10), 3500)

    def test_research_etf_scope_excludes_lof_and_feeder_funds(self) -> None:
        self.assertTrue(backfill.is_research_etf_name("沪深300ETF"))
        self.assertFalse(backfill.is_research_etf_name("广发中证医疗ETF联接(LOF)-A"))
        self.assertFalse(backfill.is_research_etf_name("红土创新科技创新股票(LOF)-A"))

    def test_matching_fund_adjustment_coverage_is_complete(self) -> None:
        actual = backfill.DateCoverage("510300.SH", date(2012, 1, 4), date(2026, 7, 10), 3500)

        self.assertTrue(backfill.coverage_is_complete(self.required, actual))

    def test_missing_boundary_or_rows_requires_resume(self) -> None:
        self.assertFalse(backfill.coverage_is_complete(self.required, None))
        self.assertFalse(
            backfill.coverage_is_complete(
                self.required,
                backfill.DateCoverage("159919.SZ", date(2012, 1, 4), date(2026, 7, 10), 3500),
            )
        )
        self.assertFalse(
            backfill.coverage_is_complete(
                self.required,
                backfill.DateCoverage("510300.SH", date(2012, 1, 5), date(2026, 7, 10), 3500),
            )
        )
        self.assertFalse(
            backfill.coverage_is_complete(
                self.required,
                backfill.DateCoverage("510300.SH", date(2012, 1, 4), date(2026, 7, 10), 3499),
            )
        )


if __name__ == "__main__":
    unittest.main()
