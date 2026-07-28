from __future__ import annotations

from copy import deepcopy
import unittest

from backend.app.quant_research import (
    CONDITIONAL_CANDIDATE_GATE_IDS,
    EVALUATION_SCHEMA_VERSION,
    EVIDENCE_SCHEMA_VERSION,
    REQUIRED_GATE_IDS,
    EvaluationContractError,
    StrategyEvidenceBundle,
    evaluate_research,
)
from backend.app.quant_research.run_config import build_reproducibility_key


class CanonicalResearchEvaluationTests(unittest.TestCase):
    def test_public_golden_seam_is_deterministic_and_uses_canonical_metrics(self) -> None:
        payload = _complete_bundle()
        with self.assertRaises(TypeError):
            StrategyEvidenceBundle(b"{}")  # type: ignore[call-arg]
        frozen_bundle = StrategyEvidenceBundle.from_dict(payload)
        payload["strategyFacts"]["strategyName"] = "调用方改写"
        first = evaluate_research(frozen_bundle)
        second = evaluate_research(StrategyEvidenceBundle.from_dict(_complete_bundle()))

        self.assertEqual(first.schema_version, EVALUATION_SCHEMA_VERSION)
        self.assertEqual(first.evaluation_sha256, second.evaluation_sha256)
        self.assertEqual(first.to_dict(), second.to_dict())
        result = first.to_dict()
        self.assertEqual(result["conclusion"], "研究通过")
        self.assertAlmostEqual(result["metrics"]["returns"]["value"]["totalReturn"], 0.10)
        self.assertAlmostEqual(result["metrics"]["risk"]["value"]["maxDrawdown"], -0.01)
        self.assertAlmostEqual(
            result["metrics"]["benchmark"]["value"]["benchmarkTotalReturn"], 0.02
        )
        self.assertAlmostEqual(
            result["metrics"]["benchmark"]["value"]["relativeWealth"],
            1.10 / 1.02 - 1.0,
        )
        self.assertNotAlmostEqual(
            result["metrics"]["benchmark"]["value"]["relativeWealth"],
            result["metrics"]["benchmark"]["value"]["excessTotalReturn"],
        )
        self.assertAlmostEqual(
            result["metrics"]["trading"]["value"]["cumulativeTransactionCostRate"],
            0.012,
        )
        self.assertEqual(result["strategyFacts"]["strategyName"], "黄金策略")

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

        failed = _complete_bundle()
        failed["robustnessEvidence"]["walkForward"] = {
            "status": "failed",
            "reason": "评价计算异常",
        }
        with self.assertRaisesRegex(EvaluationContractError, "不能冻结"):
            evaluate_research(StrategyEvidenceBundle.from_dict(failed))

        non_finite = _complete_bundle()
        non_finite["strategyFacts"]["riskControls"]["score"] = float("nan")
        with self.assertRaisesRegex(ValueError, "NaN|有限数"):
            StrategyEvidenceBundle.from_dict(non_finite)

    def test_all_required_gates_and_declared_evidence_bindings_are_mandatory(self) -> None:
        self.assertEqual(
            REQUIRED_GATE_IDS,
            {
                "identity_and_hypothesis",
                "point_in_time_universe",
                "execution_semantics",
                "net_cost_and_liquidity",
                "matched_benchmark",
                "test_oos",
                "market_regime",
                "trial_history",
                "risk_and_capacity",
                "capacity",
                "reproducibility",
            },
        )
        self.assertEqual(
            CONDITIONAL_CANDIDATE_GATE_IDS,
            {"market_regime", "capacity", "soft_threshold"},
        )
        missing_gate = _complete_bundle()
        missing_gate["hardGates"] = missing_gate["hardGates"][:-1]
        with self.assertRaisesRegex(EvaluationContractError, "缺少冻结必需 gateId"):
            StrategyEvidenceBundle.from_dict(missing_gate)

        missing_gate_evidence = _complete_bundle()
        _gate(missing_gate_evidence, "reproducibility")["evidenceRefIds"] = ["input"]
        with self.assertRaisesRegex(EvaluationContractError, "缺少证据类型"):
            StrategyEvidenceBundle.from_dict(missing_gate_evidence)

        unknown_ref = _complete_bundle()
        _gate(unknown_ref, "test_oos")["evidenceRefIds"] = ["unknown"]
        with self.assertRaisesRegex(EvaluationContractError, "未知或重复证据"):
            StrategyEvidenceBundle.from_dict(unknown_ref)

        unbound_run = _complete_bundle()
        unbound_run["evidenceRefs"][0]["runId"] = "other-run"
        with self.assertRaisesRegex(EvaluationContractError, "未绑定评价运行"):
            StrategyEvidenceBundle.from_dict(unbound_run)

    def test_artifact_uri_authority_and_path_are_bound_to_declared_run(self) -> None:
        for uri in (
            "artifacts://run-other/manifest.json",
            "artifacts://run-golden",
            "artifacts://run-golden/manifest.json?version=other",
        ):
            payload = _complete_bundle()
            payload["evidenceRefs"][0]["uri"] = uri
            with self.subTest(uri=uri), self.assertRaisesRegex(
                EvaluationContractError, "authority 绑定 runId.*工件路径"
            ):
                StrategyEvidenceBundle.from_dict(payload)

    def test_only_frozen_market_capacity_and_soft_threshold_gates_can_be_conditional(self) -> None:
        forbidden = _complete_bundle()
        _gate(forbidden, "test_oos")["passed"] = False
        forbidden["robustnessEvidence"]["walkForward"]["value"]["passed"] = False
        forbidden["conditionalCandidateRule"] = {
            "status": "complete",
            "allowedFailedGateIds": ["test_oos"],
        }
        with self.assertRaisesRegex(EvaluationContractError, "不得豁免核心硬门禁"):
            StrategyEvidenceBundle.from_dict(forbidden)

        for gate_id, robustness_name in (
            ("market_regime", "marketRegimes"),
            ("capacity", "capacity"),
        ):
            payload = _complete_bundle()
            _gate(payload, gate_id)["passed"] = False
            payload["robustnessEvidence"][robustness_name]["value"]["passed"] = False
            payload["conditionalCandidateRule"] = {
                "status": "complete",
                "allowedFailedGateIds": [gate_id],
            }
            with self.subTest(gate_id=gate_id):
                self.assertEqual(
                    evaluate_research(StrategyEvidenceBundle.from_dict(payload)).conclusion,
                    "有条件候选",
                )

    def test_successful_run_and_all_decisive_evidence_must_be_bound(self) -> None:
        failed_only = _complete_bundle()
        failed_only["runIdentities"][0]["status"] = "failed"
        self.assertEqual(
            evaluate_research(StrategyEvidenceBundle.from_dict(failed_only)).conclusion,
            "受阻",
        )

        mixed = _complete_bundle()
        failed_run = deepcopy(mixed["runIdentities"][0])
        failed_run["runId"] = "run-failed"
        failed_run["status"] = "failed"
        mixed["runIdentities"].append(failed_run)
        mixed["evidenceRefs"][0]["runId"] = "run-failed"
        mixed["evidenceRefs"][0]["uri"] = "artifacts://run-failed/manifest.json"
        self.assertEqual(
            evaluate_research(StrategyEvidenceBundle.from_dict(mixed)).conclusion,
            "受阻",
        )

    def test_false_robustness_cannot_be_relabelled_as_research_passed(self) -> None:
        payload = _complete_bundle()
        _gate(payload, "net_cost_and_liquidity")["passed"] = False
        payload["robustnessEvidence"]["costStress"]["value"]["passed"] = False
        self.assertEqual(
            evaluate_research(StrategyEvidenceBundle.from_dict(payload)).conclusion,
            "不通过",
        )

        inconsistent = _complete_bundle()
        inconsistent["robustnessEvidence"]["costStress"]["value"]["passed"] = False
        with self.assertRaisesRegex(EvaluationContractError, "对应冻结门禁不一致"):
            StrategyEvidenceBundle.from_dict(inconsistent)

    def test_missing_oos_is_insufficient_and_untrusted_protocol_is_blocked(self) -> None:
        oos = _complete_bundle()
        _gate(oos, "test_oos")["passed"] = False
        oos["robustnessEvidence"]["walkForward"]["value"]["passed"] = False
        self.assertEqual(
            evaluate_research(StrategyEvidenceBundle.from_dict(oos)).conclusion,
            "证据不足",
        )

        for gate_id in (
            "identity_and_hypothesis",
            "point_in_time_universe",
            "reproducibility",
        ):
            payload = _complete_bundle()
            _gate(payload, gate_id)["passed"] = False
            with self.subTest(gate_id=gate_id):
                self.assertEqual(
                    evaluate_research(StrategyEvidenceBundle.from_dict(payload)).conclusion,
                    "受阻",
                )

    def test_strategy_profile_and_robustness_have_domain_structure(self) -> None:
        missing_hypothesis = _complete_bundle()
        del missing_hypothesis["strategyFacts"]["economicHypothesis"]
        with self.assertRaisesRegex(EvaluationContractError, "完整冻结策略画像"):
            StrategyEvidenceBundle.from_dict(missing_hypothesis)

        missing_failure = _complete_bundle()
        missing_failure["strategyFacts"]["failureMechanisms"] = []
        with self.assertRaisesRegex(EvaluationContractError, "failureMechanisms"):
            StrategyEvidenceBundle.from_dict(missing_failure)

        weak_regimes = _complete_bundle()
        del weak_regimes["robustnessEvidence"]["marketRegimes"]["value"][
            "stressPeriodCount"
        ]
        with self.assertRaisesRegex(EvaluationContractError, "领域字段无效"):
            StrategyEvidenceBundle.from_dict(weak_regimes)

        weak_capacity = _complete_bundle()
        weak_capacity["robustnessEvidence"]["capacity"]["value"] = {"passed": True}
        with self.assertRaisesRegex(EvaluationContractError, "领域字段无效"):
            StrategyEvidenceBundle.from_dict(weak_capacity)

    def test_single_direction_or_volatility_regime_cannot_pass_research(self) -> None:
        for field in ("directionRegimeCount", "volatilityRegimeCount"):
            payload = _complete_bundle()
            payload["robustnessEvidence"]["marketRegimes"]["value"][field] = 1
            with self.subTest(field=field), self.assertRaisesRegex(
                EvaluationContractError, rf"marketRegimes.{field} 必须至少为 2"
            ):
                StrategyEvidenceBundle.from_dict(payload)

    def test_run_identity_is_closed_to_strategy_research_and_plan(self) -> None:
        for target, field, value in (
            ("bundle", "formalResearchId", "formal-other"),
            ("run", "strategyId", "other_strategy"),
            ("run", "planSha256", "9" * 64),
        ):
            payload = _complete_bundle()
            if target == "bundle":
                payload[field] = value
            else:
                payload["runIdentities"][0][field] = value
            with self.subTest(target=target, field=field), self.assertRaisesRegex(
                EvaluationContractError, "正式研究及冻结计划身份不闭合"
            ):
                StrategyEvidenceBundle.from_dict(payload)

    def test_multiple_trials_require_registered_dsr_and_pbo(self) -> None:
        missing = _complete_bundle()
        missing["strategyFacts"]["validationDesign"]["trialCount"] = 2
        missing["robustnessEvidence"]["multipleTesting"]["value"]["trialCount"] = 2
        with self.assertRaisesRegex(EvaluationContractError, "multipleTesting.dsr.status 无效"):
            StrategyEvidenceBundle.from_dict(missing)

        below_threshold = _complete_bundle()
        below_threshold["strategyFacts"]["validationDesign"]["trialCount"] = 2
        _gate(below_threshold, "trial_history")["passed"] = False
        below_threshold["robustnessEvidence"]["parameterNeighborhood"]["value"][
            "passed"
        ] = False
        below_threshold["robustnessEvidence"]["multipleTesting"]["value"] = {
            "passed": False,
            "trialCount": 2,
            "dsr": {
                "status": "complete",
                "value": {"probability": 0.80, "trialCount": 2, "observations": 24},
            },
            "pbo": {
                "status": "complete",
                "value": {
                    "probability": 0.40,
                    "monthlyObservations": 24,
                    "combinations": 2,
                    "trainingWinnerCounts": {"candidate-a": 1, "candidate-b": 1},
                },
            },
        }
        self.assertEqual(
            evaluate_research(StrategyEvidenceBundle.from_dict(below_threshold)).conclusion,
            "不通过",
        )

    def test_limitations_are_required_for_every_evaluation(self) -> None:
        payload = _complete_bundle()
        payload["limitations"] = []
        with self.assertRaisesRegex(EvaluationContractError, "limitations 必须是非空"):
            StrategyEvidenceBundle.from_dict(payload)

    def test_run_identity_formats_and_reproducibility_derivation_are_verified(self) -> None:
        for field, value in (
            ("codeCommit", "commit"),
            ("dataSnapshotId", "snapshot"),
            ("reproducibilityKey", "key"),
        ):
            payload = _complete_bundle()
            payload["runIdentities"][0][field] = value
            with self.subTest(field=field), self.assertRaises(EvaluationContractError):
                StrategyEvidenceBundle.from_dict(payload)

        mismatch = _complete_bundle()
        mismatch["runIdentities"][0]["randomSeed"] = 2
        with self.assertRaisesRegex(EvaluationContractError, "不闭合"):
            StrategyEvidenceBundle.from_dict(mismatch)

    def test_zero_request_denominator_is_not_reported_as_zero(self) -> None:
        payload = _complete_bundle()
        for row in payload["tradingEvidence"]["observations"]:
            row.update(
                requestCount=0,
                executionCount=0,
                blockedCount=0,
                blockedRequestCount=0,
            )
        result = evaluate_research(StrategyEvidenceBundle.from_dict(payload)).to_dict()
        self.assertEqual(result["conclusion"], "证据不足")
        self.assertEqual(
            result["metrics"]["trading"]["value"]["blockedRequestRate"],
            {
                "status": "not_available",
                "reason": "冻结交易请求数为零，blockedRequestRate 分母不存在",
            },
        )


def _complete_bundle() -> dict:
    run = {
        "runId": "run-golden",
        "status": "succeeded",
        "strategyId": "golden_strategy",
        "strategyVersion": "1.0.0",
        "formalResearchId": "formal-golden",
        "planSha256": "a" * 64,
        "codeCommit": "b" * 40,
        "dataSnapshotId": "c" * 64,
        "configSha256": "d" * 64,
        "environmentSha256": "e" * 64,
        "randomSeed": 1,
        "resultFingerprint": "f" * 64,
    }
    run["reproducibilityKey"] = build_reproducibility_key(
        config_sha256=run["configSha256"],
        data_snapshot_id=run["dataSnapshotId"],
        code_commit=run["codeCommit"],
        environment_sha256=run["environmentSha256"],
        random_seed=run["randomSeed"],
    )
    evidence_refs = [
        _ref("input", "input_snapshot", "manifest.json", "1"),
        _ref("code", "code", "manifest.json", "2"),
        _ref("environment", "environment", "manifest.json", "3"),
        _ref("parameters", "parameters", "manifest.json", "4"),
        _ref("ledger", "ledger", "rebalance_executions.csv.gz", "5"),
        _ref("statistics", "statistics", "metrics.json", "6"),
    ]
    gate_refs = {
        "identity_and_hypothesis": ["code", "parameters"],
        "point_in_time_universe": ["input"],
        "execution_semantics": ["ledger"],
        "net_cost_and_liquidity": ["ledger", "statistics"],
        "matched_benchmark": ["statistics"],
        "test_oos": ["statistics"],
        "market_regime": ["statistics"],
        "trial_history": ["parameters", "statistics"],
        "risk_and_capacity": ["ledger", "statistics"],
        "capacity": ["ledger", "statistics"],
        "reproducibility": ["input", "code", "environment", "parameters"],
    }
    robustness = {
        "walkForward": _robustness({"passed": True, "testWindowCount": 3}, ["statistics"]),
        "parameterNeighborhood": _robustness(
            {"passed": True, "parameterSetCount": 3}, ["parameters", "statistics"]
        ),
        "costStress": _robustness(
            {"passed": True, "scenarioCount": 2}, ["ledger", "statistics"]
        ),
        "marketRegimes": _robustness(
            {
                "passed": True,
                "directionRegimeCount": 3,
                "volatilityRegimeCount": 2,
                "calendarYearCount": 3,
                "stressPeriodCount": 1,
            },
            ["statistics"],
        ),
        "capacity": _robustness(
            {"passed": True, "expectedCapital": 10_000_000, "advParticipationP95": 0.03},
            ["ledger", "statistics"],
        ),
        "multipleTesting": _robustness(
            {
                "passed": True,
                "trialCount": 1,
                "dsr": {
                    "status": "not_applicable",
                    "reason": "冻结试验次数为 1，无多重筛选",
                },
                "pbo": {
                    "status": "not_applicable",
                    "reason": "冻结试验次数为 1，无多重筛选",
                },
            },
            ["parameters", "statistics"],
        ),
    }
    return {
        "schemaVersion": EVIDENCE_SCHEMA_VERSION,
        "strategyId": "golden_strategy",
        "strategyVersion": "1.0.0",
        "formalResearchId": "formal-golden",
        "planSha256": "a" * 64,
        "runIdentities": [run],
        "evidenceRefs": evidence_refs,
        "navEvidence": {
            "status": "complete",
            "initialNav": 1.0,
            "initialBenchmarkNav": 1.0,
            "evidenceRefIds": ["statistics"],
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
            "evidenceRefIds": ["ledger"],
            "observations": [
                _trade("2025-01-02", 1, 1, 0, 0, 0.4, 0.01),
                _trade("2025-01-03", 2, 1, 1, 1, 0.2, 0.002),
                _trade("2025-01-06", 0, 0, 0, 0, 0.0, 0.0),
                _trade("2025-01-07", 0, 0, 0, 0, 0.0, 0.0),
                _trade("2025-01-08", 0, 0, 0, 0, 0.0, 0.0),
            ],
        },
        "robustnessEvidence": robustness,
        "hardGates": [
            {
                "gateId": gate_id,
                "status": "complete",
                "passed": True,
                "evidenceRefIds": refs,
            }
            for gate_id, refs in gate_refs.items()
        ],
        "conditionalCandidateRule": {
            "status": "not_applicable",
            "reason": "冻结计划未定义有条件候选自动规则",
        },
        "strategyFacts": _strategy_facts(),
        "limitations": ["合成黄金夹具不代表实际市场研究结论"],
    }


def _strategy_facts() -> dict:
    return {
        "strategyName": "黄金策略",
        "researchDate": "2025-01-08",
        "economicHypothesis": {
            "returnSource": "风险补偿",
            "riskTaken": "承担短期价格波动",
            "counterparties": "流动性需求方",
            "persistenceRationale": "行为与约束持续存在",
        },
        "applicableConditions": ["流动性正常"],
        "assetsAndUniverse": {"market": "合成股票", "history": "point-in-time"},
        "dataAndTiming": {"frequency": "日频", "availability": "收盘后"},
        "signal": {"formula": "冻结公式", "warmup": 20},
        "portfolioConstruction": {"weighting": "等权", "cashPolicy": "允许现金"},
        "execution": {"signalTiming": "收盘", "executionTiming": "下一开盘"},
        "costs": {"returnBasis": "净收益", "stressMultipliers": [1.0, 2.0]},
        "riskControls": {"positionLimit": 0.1, "score": 1.0},
        "validationDesign": {
            "sampleSplit": "IS/OOS",
            "trialCount": 1,
            "minimumDsrProbability": 0.95,
            "maximumPboProbability": 0.20,
        },
        "capacityAssumptions": ["预期资金一千万元"],
        "failureMechanisms": ["流动性枯竭", "交易拥挤"],
        "evidenceRefIds": ["code", "parameters", "statistics"],
    }


def _ref(name: str, kind: str, path: str, digit: str) -> dict:
    return {
        "artifactName": name,
        "kind": kind,
        "uri": f"artifacts://run-golden/{path}",
        "runId": "run-golden",
        "sha256": digit * 64,
    }


def _robustness(value: dict, refs: list[str]) -> dict:
    return {"status": "complete", "value": value, "evidenceRefIds": refs}


def _gate(payload: dict, gate_id: str) -> dict:
    return next(item for item in payload["hardGates"] if item["gateId"] == gate_id)


def _trade(
    date: str,
    requests: int,
    executions: int,
    blocked: int,
    fully_blocked: int,
    turnover: float,
    cost: float,
) -> dict:
    return {
        "tradeDate": date,
        "requestCount": requests,
        "executionCount": executions,
        "blockedCount": blocked,
        "blockedRequestCount": fully_blocked,
        "oneWayTurnover": turnover,
        "transactionCostRate": cost,
    }


if __name__ == "__main__":
    unittest.main()
