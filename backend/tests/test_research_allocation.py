from __future__ import annotations

import unittest

import pandas as pd

from backend.app.quant_research.allocation import (
    allocate_target_weights,
    validate_allocation_policy,
    validate_target_weights,
)


class ResearchAllocationTest(unittest.TestCase):
    def setUp(self):
        self.empty_current = pd.DataFrame(
            columns=["ts_code", "industry", "current_weight"]
        )

    def test_equal_weight_uses_deterministic_industry_clipping_and_redistribution(self):
        candidates = pd.DataFrame(
            [
                {"ts_code": "BBB.SH", "industry": "I1", "volatility": 0.2},
                {"ts_code": "CCC.SZ", "industry": "I2", "volatility": 0.3},
                {"ts_code": "AAA.SZ", "industry": "I1", "volatility": 0.1},
            ]
        )
        policy = self._policy(method="equal_weight")
        targets = allocate_target_weights(
            candidates,
            self.empty_current,
            policy=policy,
        )
        validate_target_weights(
            targets,
            self.empty_current,
            policy=policy,
        )

        self.assertEqual(
            list(targets.columns),
            ["ts_code", "industry", "target_weight"],
        )
        weights = targets.set_index("ts_code")["target_weight"]
        self.assertAlmostEqual(weights["AAA.SZ"], 0.25, places=12)
        self.assertAlmostEqual(weights["BBB.SH"], 0.25, places=12)
        self.assertAlmostEqual(weights["CCC.SZ"], 0.4, places=12)
        self.assertAlmostEqual(weights.sum(), 0.9, places=12)
        shuffled = allocate_target_weights(
            candidates.sample(frac=1, random_state=7),
            self.empty_current,
            policy=policy,
        )
        pd.testing.assert_frame_equal(targets, shuffled)

    def test_inverse_volatility_prefers_lower_risk_without_exceeding_caps(self):
        candidates = pd.DataFrame(
            [
                {"ts_code": "AAA.SZ", "industry": "I1", "volatility": 0.1},
                {"ts_code": "BBB.SH", "industry": "I2", "volatility": 0.2},
                {"ts_code": "CCC.SZ", "industry": "I3", "volatility": 0.4},
            ]
        )
        policy = {
            "method": "inverse_volatility",
            "singleNameCap": 0.6,
            "industryCap": 0.8,
            "minimumCashWeight": 0.1,
            "maxOneWayTurnover": 1.0,
        }
        targets = allocate_target_weights(
            candidates,
            self.empty_current,
            policy=policy,
        )
        weights = targets.set_index("ts_code")["target_weight"]
        self.assertGreater(weights["AAA.SZ"], weights["BBB.SH"])
        self.assertGreater(weights["BBB.SH"], weights["CCC.SZ"])
        self.assertAlmostEqual(weights["AAA.SZ"], 0.9 * 4 / 7, places=12)
        self.assertAlmostEqual(weights.sum(), 0.9, places=12)
        validate_target_weights(targets, self.empty_current, policy=policy)

    def test_turnover_cap_interpolates_targets_without_creating_orders(self):
        candidates = pd.DataFrame(
            [
                {"ts_code": "AAA.SZ", "industry": "I1", "volatility": 0.1},
                {"ts_code": "BBB.SH", "industry": "I1", "volatility": 0.2},
                {"ts_code": "CCC.SZ", "industry": "I2", "volatility": 0.3},
            ]
        )
        current = pd.DataFrame(
            [
                {"ts_code": "AAA.SZ", "industry": "I1", "current_weight": 0.5},
                {"ts_code": "BBB.SH", "industry": "I1", "current_weight": 0.2},
            ]
        )
        policy = {
            "method": "equal_weight",
            "singleNameCap": 0.6,
            "industryCap": 0.8,
            "minimumCashWeight": 0.1,
            "maxOneWayTurnover": 0.1,
        }
        targets = allocate_target_weights(candidates, current, policy=policy)
        weights = targets.set_index("ts_code")["target_weight"]
        self.assertAlmostEqual(weights["AAA.SZ"], 0.45, places=12)
        self.assertAlmostEqual(weights["BBB.SH"], 0.225, places=12)
        self.assertAlmostEqual(weights["CCC.SZ"], 0.075, places=12)
        self.assertFalse(
            {"order", "side", "execution_date", "broker"} & set(targets.columns)
        )
        validate_target_weights(targets, current, policy=policy)

    def test_infeasible_or_invalid_inputs_fail_without_relaxing_constraints(self):
        same_industry = pd.DataFrame(
            [
                {"ts_code": "AAA.SZ", "industry": "I1", "volatility": 0.1},
                {"ts_code": "BBB.SH", "industry": "I1", "volatility": 0.2},
            ]
        )
        with self.assertRaises(ValueError):
            allocate_target_weights(
                same_industry,
                self.empty_current,
                policy=self._policy(method="equal_weight"),
            )

        invalid_volatility = same_industry.copy()
        invalid_volatility.loc[0, "volatility"] = 0
        inverse_policy = {
            **self._policy(method="inverse_volatility"),
            "industryCap": 0.9,
        }
        with self.assertRaises(ValueError):
            allocate_target_weights(
                invalid_volatility,
                self.empty_current,
                policy=inverse_policy,
            )

        invalid_current = pd.DataFrame(
            [
                {"ts_code": "AAA.SZ", "industry": "I1", "current_weight": 0.7},
            ]
        )
        with self.assertRaises(ValueError):
            allocate_target_weights(
                same_industry,
                invalid_current,
                policy={**self._policy(method="equal_weight"), "industryCap": 0.9},
            )

        with self.assertRaises(ValueError):
            validate_allocation_policy(
                {**self._policy(method="equal_weight"), "optimizer": "qp"}
            )

    @staticmethod
    def _policy(*, method: str) -> dict[str, object]:
        return {
            "method": method,
            "singleNameCap": 0.4,
            "industryCap": 0.5,
            "minimumCashWeight": 0.1,
            "maxOneWayTurnover": 1.0,
        }


if __name__ == "__main__":
    unittest.main()
