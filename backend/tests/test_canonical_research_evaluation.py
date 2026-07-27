from __future__ import annotations

import unittest

from backend.app.quant_research import (
    EVALUATION_SCHEMA_VERSION,
    EVIDENCE_SCHEMA_VERSION,
    EvaluationContractError,
    StrategyEvidenceBundle,
    evaluate_research,
)


class CanonicalResearchEvaluationTests(unittest.TestCase):
    def test_golden_bundle_uses_initial_nav_and_keeps_strategy_facts_separate(self) -> None:
        payload = _complete_bundle()
        with self.assertRaises(TypeError):
            StrategyEvidenceBundle(b"{}")  # type: ignore[call-arg]
        frozen_bundle = StrategyEvidenceBundle.from_dict(payload)
        payload["strategyFacts"]["totalReturn"] = -1
        first = evaluate_research(frozen_bundle)
        payload["strategyFacts"]["totalReturn"] = 999
        second = evaluate_research(StrategyEvidenceBundle.from_dict(payload))

        self.assertEqual(first.schema_version, EVALUATION_SCHEMA_VERSION)
        self.assertEqual(first.evaluation_sha256, second.evaluation_sha256)
        self.assertEqual(first.to_dict(), second.to_dict())
        result = first.to_dict()
        self.assertEqual(result["conclusion"], "研究通过")
        self.assertAlmostEqual(
            result["metrics"]["returns"]["value"]["totalReturn"], 0.10
        )
        self.assertAlmostEqual(
            result["metrics"]["risk"]["value"]["maxDrawdown"], -0.01
        )
        self.assertIsInstance(result["metrics"]["risk"]["value"]["skew"], float)
        self.assertAlmostEqual(
            result["metrics"]["benchmark"]["value"]["benchmarkTotalReturn"],
            0.02,
        )
        self.assertAlmostEqual(
            result["metrics"]["trading"]["value"]["cumulativeTransactionCostRate"],
            0.012,
        )
        self.assertEqual(result["strategyFacts"]["totalReturn"], 999)
        self.assertEqual(result["evidenceRefs"][0]["artifactName"], "canonical-nav")
        self.assertNotEqual(
            result["strategyFacts"]["totalReturn"],
            result["metrics"]["returns"]["value"]["totalReturn"],
        )

    def test_structured_missing_states_never_become_numeric_placeholders(self) -> None:
        payload = _complete_bundle()
        payload["navEvidence"] = {
            "status": "not_available",
            "reason": "冻结 test/OOS 样本不足",
        }
        payload["tradingEvidence"] = {
            "status": "blocked",
            "reason": "缺少 canonical 执行账本",
        }
        payload["robustnessEvidence"]["capacity"] = {
            "status": "not_applicable",
            "capability": "stable_trade_boundary",
            "reason": "冻结能力声明为不适用",
        }

        result = evaluate_research(StrategyEvidenceBundle.from_dict(payload)).to_dict()

        self.assertEqual(result["conclusion"], "受阻")
        self.assertEqual(
            result["metrics"]["returns"],
            {"status": "not_available", "reason": "冻结 test/OOS 样本不足"},
        )
        self.assertEqual(
            result["metrics"]["trading"],
            {"status": "blocked", "reason": "缺少 canonical 执行账本"},
        )
        self.assertEqual(
            result["robustness"]["capacity"],
            {
                "status": "not_applicable",
                "capability": "stable_trade_boundary",
                "reason": "冻结能力声明为不适用",
            },
        )

        failed = _complete_bundle()
        failed["robustnessEvidence"]["walkForward"] = {
            "status": "failed",
            "reason": "评价计算异常",
        }
        with self.assertRaisesRegex(EvaluationContractError, "不能冻结"):
            evaluate_research(StrategyEvidenceBundle.from_dict(failed))

        non_finite = _complete_bundle()
        non_finite["strategyFacts"]["score"] = float("nan")
        with self.assertRaisesRegex(ValueError, "NaN"):
            StrategyEvidenceBundle.from_dict(non_finite)

        invalid_core_absence = _complete_bundle()
        invalid_core_absence["navEvidence"] = {
            "status": "not_applicable",
            "capability": "nav",
            "reason": "核心 NAV 不能声明不适用",
        }
        with self.assertRaisesRegex(EvaluationContractError, "navEvidence.status"):
            StrategyEvidenceBundle.from_dict(invalid_core_absence)

        misaligned_trading = _complete_bundle()
        misaligned_trading["tradingEvidence"]["observations"].pop()
        with self.assertRaisesRegex(EvaluationContractError, "交易日"):
            StrategyEvidenceBundle.from_dict(misaligned_trading)

        empty_complete = _complete_bundle()
        empty_complete["robustnessEvidence"]["walkForward"] = {
            "status": "complete",
            "value": {},
        }
        with self.assertRaisesRegex(EvaluationContractError, "complete value"):
            StrategyEvidenceBundle.from_dict(empty_complete)

        inapplicable_hard_gate = _complete_bundle()
        inapplicable_hard_gate["hardGates"][0] = {
            "gateId": "identity",
            "status": "not_applicable",
            "reason": "硬门禁不能声明不适用",
        }
        with self.assertRaisesRegex(EvaluationContractError, "hardGate.status"):
            StrategyEvidenceBundle.from_dict(inapplicable_hard_gate)

    def test_five_conclusions_are_constrained_by_frozen_evidence_and_rules(self) -> None:
        cases = []

        passed = _complete_bundle()
        cases.append(("研究通过", passed))

        conditional = _complete_bundle()
        conditional["hardGates"][1]["passed"] = False
        conditional["conditionalCandidateRule"] = {
            "status": "complete",
            "allowedFailedGateIds": ["oos"],
        }
        cases.append(("有条件候选", conditional))

        insufficient = _complete_bundle()
        insufficient["navEvidence"] = {
            "status": "not_available",
            "reason": "样本不足",
        }
        cases.append(("证据不足", insufficient))

        blocked = _complete_bundle()
        blocked["tradingEvidence"] = {
            "status": "blocked",
            "reason": "执行账本缺失",
        }
        cases.append(("受阻", blocked))

        rejected = _complete_bundle()
        rejected["hardGates"][1]["passed"] = False
        cases.append(("不通过", rejected))

        for expected, payload in cases:
            with self.subTest(expected=expected):
                self.assertEqual(
                    evaluate_research(StrategyEvidenceBundle.from_dict(payload)).conclusion,
                    expected,
                )

        succeeded_without_evidence = _complete_bundle()
        succeeded_without_evidence["navEvidence"] = {
            "status": "not_available",
            "reason": "成功运行仍缺少冻结 OOS 证据",
        }
        self.assertEqual(
            evaluate_research(
                StrategyEvidenceBundle.from_dict(succeeded_without_evidence)
            ).conclusion,
            "证据不足",
        )

        no_frozen_conditional_rule = _complete_bundle()
        no_frozen_conditional_rule["hardGates"][1]["passed"] = False
        self.assertEqual(
            evaluate_research(
                StrategyEvidenceBundle.from_dict(no_frozen_conditional_rule)
            ).conclusion,
            "不通过",
        )

        short_sample = _complete_bundle()
        short_sample["navEvidence"]["observations"] = short_sample["navEvidence"][
            "observations"
        ][:2]
        short_sample["tradingEvidence"]["observations"] = short_sample[
            "tradingEvidence"
        ]["observations"][:2]
        short_result = evaluate_research(
            StrategyEvidenceBundle.from_dict(short_sample)
        ).to_dict()
        self.assertEqual(short_result["conclusion"], "证据不足")
        self.assertEqual(
            short_result["metrics"]["risk"]["value"]["skew"],
            {
                "status": "not_available",
                "reason": "冻结样本不足以计算 skew",
            },
        )
        self.assertIn(
            {
                "evidence": "metrics.risk.value.skew",
                "status": "not_available",
                "reason": "冻结样本不足以计算 skew",
            },
            short_result["missingEvidence"],
        )


def _complete_bundle() -> dict:
    robustness = {
        name: {"status": "complete", "value": {"passed": True}}
        for name in (
            "walkForward",
            "parameterNeighborhood",
            "costStress",
            "marketRegimes",
            "capacity",
        )
    }
    return {
        "schemaVersion": EVIDENCE_SCHEMA_VERSION,
        "strategyId": "golden_strategy",
        "strategyVersion": "1.0.0",
        "formalResearchId": "formal-golden",
        "planSha256": "a" * 64,
        "runIdentities": [
            {
                "runId": "run-golden",
                "status": "succeeded",
                "codeCommit": "b" * 40,
                "dataSnapshotId": "snapshot-golden",
                "configSha256": "c" * 64,
                "environmentSha256": "d" * 64,
                "reproducibilityKey": "reproduce-golden",
                "resultFingerprint": "e" * 64,
            }
        ],
        "evidenceRefs": [
            {
                "artifactName": "canonical-nav",
                "uri": "artifact://run-golden/nav.csv",
                "sha256": "f" * 64,
            }
        ],
        "navEvidence": {
            "status": "complete",
            "initialNav": 1.0,
            "initialBenchmarkNav": 1.0,
            "observations": [
                {"tradeDate": "2025-01-02", "nav": 0.99, "benchmarkNav": 1.005},
                {"tradeDate": "2025-01-03", "nav": 1.00, "benchmarkNav": 1.00},
                {"tradeDate": "2025-01-06", "nav": 1.02, "benchmarkNav": 1.01},
                {"tradeDate": "2025-01-07", "nav": 1.05, "benchmarkNav": 1.015},
                {"tradeDate": "2025-01-08", "nav": 1.10, "benchmarkNav": 1.02},
            ],
        },
        "tradingEvidence": {
            "status": "complete",
            "observations": [
                {
                    "tradeDate": "2025-01-02",
                    "requestCount": 1,
                    "executionCount": 1,
                    "blockedCount": 0,
                    "blockedRequestCount": 0,
                    "oneWayTurnover": 0.4,
                    "transactionCostRate": 0.01,
                },
                {
                    "tradeDate": "2025-01-03",
                    "requestCount": 2,
                    "executionCount": 1,
                    "blockedCount": 1,
                    "blockedRequestCount": 1,
                    "oneWayTurnover": 0.2,
                    "transactionCostRate": 0.002,
                },
                {
                    "tradeDate": "2025-01-06",
                    "requestCount": 0,
                    "executionCount": 0,
                    "blockedCount": 0,
                    "blockedRequestCount": 0,
                    "oneWayTurnover": 0.0,
                    "transactionCostRate": 0.0,
                },
                {
                    "tradeDate": "2025-01-07",
                    "requestCount": 0,
                    "executionCount": 0,
                    "blockedCount": 0,
                    "blockedRequestCount": 0,
                    "oneWayTurnover": 0.0,
                    "transactionCostRate": 0.0,
                },
                {
                    "tradeDate": "2025-01-08",
                    "requestCount": 0,
                    "executionCount": 0,
                    "blockedCount": 0,
                    "blockedRequestCount": 0,
                    "oneWayTurnover": 0.0,
                    "transactionCostRate": 0.0,
                },
            ],
        },
        "robustnessEvidence": robustness,
        "hardGates": [
            {"gateId": "identity", "status": "complete", "passed": True},
            {"gateId": "oos", "status": "complete", "passed": True},
        ],
        "conditionalCandidateRule": {
            "status": "not_applicable",
            "reason": "冻结计划未定义有条件候选自动规则",
        },
        "strategyFacts": {"signalCoverage": 0.8, "totalReturn": 999},
        "limitations": [],
    }


if __name__ == "__main__":
    unittest.main()
