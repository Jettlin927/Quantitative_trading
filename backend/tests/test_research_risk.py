from __future__ import annotations

import unittest

import pandas as pd

from backend.app.quant_research.risk import (
    calculate_risk_frames,
    validate_risk_artifacts,
    validate_risk_policy,
)


class ResearchRiskTest(unittest.TestCase):
    def setUp(self):
        self.dates = pd.bdate_range("2026-01-05", periods=8)
        a_returns = [0.01, -0.005, 0.012, 0.004, -0.002, 0.008, 0.006]
        b_returns = [0.002, 0.006, -0.003, 0.009, 0.005, -0.004, 0.007]
        self.asset_returns = pd.DataFrame(
            [
                {
                    "trade_date": trade_date,
                    "ts_code": symbol,
                    "asset_return": value,
                }
                for symbol, values in (("AAA.SZ", a_returns), ("BBB.SH", b_returns))
                for trade_date, value in zip(self.dates[1:], values, strict=True)
            ]
        ).sort_values(["trade_date", "ts_code"], kind="stable").reset_index(drop=True)
        portfolio_returns = [0.6 * left + 0.3 * right for left, right in zip(a_returns, b_returns, strict=True)]
        nav_values = [1.0]
        for value in portfolio_returns:
            nav_values.append(nav_values[-1] * (1.0 + value))
        self.nav = pd.DataFrame(
            {
                "trade_date": self.dates,
                "nav": nav_values,
                "cash_weight": 0.1,
            }
        )
        self.positions = pd.DataFrame(
            [
                {
                    "trade_date": trade_date,
                    "ts_code": symbol,
                    "close_weight": weight,
                }
                for trade_date in self.dates
                for symbol, weight in (("AAA.SZ", 0.6), ("BBB.SH", 0.3))
            ]
        )
        self.benchmark_returns = pd.DataFrame(
            {
                "trade_date": self.dates[1:],
                "benchmark_return": [0.005, 0.001, 0.006, 0.004, 0.002, 0.003, 0.005],
            }
        )
        self.membership = pd.DataFrame(
            [
                {"trade_date": trade_date, "ts_code": "AAA.SZ"}
                for trade_date in self.dates
            ]
        )
        self.policy = {
            "mode": "rolling_covariance",
            "lookbackPeriods": 3,
            "minPeriods": 3,
        }

    def _calculate(self):
        return calculate_risk_frames(
            self.asset_returns,
            self.positions,
            self.nav,
            self.benchmark_returns,
            membership=self.membership,
            industry_source_key="SYNIND.SI",
            policy=self.policy,
        )

    def test_policy_is_fixed_and_strict(self):
        self.assertEqual(validate_risk_policy(None), {"mode": "none"})
        self.assertEqual(validate_risk_policy({"mode": "none"}), {"mode": "none"})
        self.assertEqual(validate_risk_policy(self.policy), self.policy)
        for invalid in (
            {"mode": "rolling_covariance", "lookbackPeriods": 3},
            {**self.policy, "grid": [20, 60]},
            {**self.policy, "minPeriods": 4},
            {**self.policy, "lookbackPeriods": True},
        ):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    validate_risk_policy(invalid)

    def test_risk_contributions_reconcile_and_warmup_remains_null(self):
        result = self._calculate()
        validate_risk_artifacts(result.exposures, result.contributions, self.policy)

        first = result.contributions[
            result.contributions["trade_date"].eq(self.dates[0])
        ]
        self.assertTrue(first["portfolio_volatility"].isna().all())
        self.assertTrue(first["total_risk_contribution"].isna().all())

        ready = result.contributions.dropna(subset=["portfolio_volatility"])
        self.assertFalse(ready.empty)
        for _trade_date, group in ready.groupby("trade_date", sort=True):
            self.assertAlmostEqual(
                group["total_risk_contribution"].sum(),
                group["portfolio_volatility"].iloc[0],
                places=12,
            )

        exposures = result.exposures.set_index("trade_date")
        self.assertAlmostEqual(exposures.loc[self.dates[-1], "gross_exposure"], 0.9)
        self.assertAlmostEqual(exposures.loc[self.dates[-1], "net_exposure"], 0.9)
        self.assertAlmostEqual(exposures.loc[self.dates[-1], "cash_weight"], 0.1)
        self.assertAlmostEqual(exposures.loc[self.dates[-1], "max_weight"], 0.6)
        self.assertAlmostEqual(exposures.loc[self.dates[-1], "hhi"], 0.45)
        self.assertEqual(exposures.loc[self.dates[-1], "industry_source_key"], "SYNIND.SI")
        self.assertAlmostEqual(exposures.loc[self.dates[-1], "industry_weight"], 0.6)
        self.assertFalse(pd.isna(exposures.loc[self.dates[-1], "benchmark_beta"]))

    def test_appending_future_returns_does_not_change_risk_prefix(self):
        base = self._calculate()
        future_date = self.dates[-1] + pd.offsets.BDay(1)
        asset_returns = pd.concat(
            [
                self.asset_returns,
                pd.DataFrame(
                    [
                        {"trade_date": future_date, "ts_code": "AAA.SZ", "asset_return": 0.2},
                        {"trade_date": future_date, "ts_code": "BBB.SH", "asset_return": -0.1},
                    ]
                ),
            ],
            ignore_index=True,
        )
        positions = pd.concat(
            [
                self.positions,
                pd.DataFrame(
                    [
                        {"trade_date": future_date, "ts_code": "AAA.SZ", "close_weight": 0.6},
                        {"trade_date": future_date, "ts_code": "BBB.SH", "close_weight": 0.3},
                    ]
                ),
            ],
            ignore_index=True,
        )
        nav = pd.concat(
            [
                self.nav,
                pd.DataFrame(
                    [
                        {
                            "trade_date": future_date,
                            "nav": self.nav["nav"].iloc[-1] * 1.09,
                            "cash_weight": 0.1,
                        }
                    ]
                ),
            ],
            ignore_index=True,
        )
        benchmark = pd.concat(
            [
                self.benchmark_returns,
                pd.DataFrame(
                    [{"trade_date": future_date, "benchmark_return": 0.15}]
                ),
            ],
            ignore_index=True,
        )
        membership = pd.concat(
            [
                self.membership,
                pd.DataFrame(
                    [{"trade_date": future_date, "ts_code": "AAA.SZ"}]
                ),
            ],
            ignore_index=True,
        )
        extended = calculate_risk_frames(
            asset_returns,
            positions,
            nav,
            benchmark,
            membership=membership,
            industry_source_key="SYNIND.SI",
            policy=self.policy,
        )

        pd.testing.assert_frame_equal(
            base.exposures,
            extended.exposures[extended.exposures["trade_date"] <= self.dates[-1]].reset_index(drop=True),
        )
        pd.testing.assert_frame_equal(
            base.contributions,
            extended.contributions[
                extended.contributions["trade_date"] <= self.dates[-1]
            ].reset_index(drop=True),
        )

    def test_non_finite_inputs_and_tampered_contributions_fail(self):
        invalid = self.asset_returns.copy()
        invalid.loc[0, "asset_return"] = float("inf")
        with self.assertRaises(ValueError):
            calculate_risk_frames(
                invalid,
                self.positions,
                self.nav,
                self.benchmark_returns,
                membership=self.membership,
                industry_source_key="SYNIND.SI",
                policy=self.policy,
            )

        result = self._calculate()
        tampered = result.contributions.copy()
        index = tampered["total_risk_contribution"].first_valid_index()
        tampered.loc[index, "total_risk_contribution"] += 0.01
        with self.assertRaises(ValueError):
            validate_risk_artifacts(result.exposures, tampered, self.policy)


if __name__ == "__main__":
    unittest.main()
