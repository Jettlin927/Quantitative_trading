from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from backend.app.personal_workspace.agent.hosted_search_experiment_runner import (
    ExperimentBudgetPolicy,
    ExperimentPricingSnapshot,
    HostedSearchExperimentRunner,
    SafeHttpsWebEvidenceVerifier,
    VerificationResponse,
    _plan_from_dict,
    main,
)


NOW = datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc)
GIT_SHA = "a" * 40


def pricing(
    *, hosted_unit: Decimal | None = Decimal("0")
) -> ExperimentPricingSnapshot:
    return ExperimentPricingSnapshot(
        revision="deepseek-v4-flash-2026-04-24",
        source_url="https://api-docs.deepseek.com/quick_start/pricing/",
        cache_hit_input_usd_per_million=Decimal("0.0028"),
        cache_miss_input_usd_per_million=Decimal("0.14"),
        output_usd_per_million=Decimal("0.28"),
        hosted_search_usd_per_request=hosted_unit,
        hosted_search_source_url=(
            "https://api-docs.deepseek.com/guides/anthropic_api/"
            if hosted_unit is not None
            else None
        ),
    )


def budget(root: Path) -> ExperimentBudgetPolicy:
    return ExperimentBudgetPolicy(
        market_date=date(2026, 8, 10),
        fx_cny_per_usd=Decimal("7.20"),
        fx_snapshot="fixed-2026-08-10",
        per_run_reserve_cny=Decimal("4.00"),
        daily_limit_cny=Decimal("5.00"),
        ledger_path=str(root / "daily-budget.json"),
        revision="hosted-search-budget-v1",
    )


class HostedSearchExperimentRunnerTest(unittest.TestCase):
    def runner(self, root: Path) -> HostedSearchExperimentRunner:
        return HostedSearchExperimentRunner(
            git_reader=lambda: (GIT_SHA, True),
            budget_ledger_path=str(root / "daily-budget.json"),
            clock=lambda: NOW,
        )

    def test_plan_is_offline_canonical_and_declares_cost_bound_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plan = self.runner(root).plan(
                git_sha=GIT_SHA,
                credential_path=str(root / "missing-secret.json"),
                artifact_dir=str(root / "artifact"),
                expires_at=NOW + timedelta(hours=1),
                max_tool_calls=5,
                max_queries=10,
                pricing=pricing(),
                budget=budget(root),
            )

            payload = plan.to_dict()
            self.assertEqual(payload["budget"]["daily_limit_cny"], "5.00")
            self.assertEqual(payload["provider"]["max_provider_requests"], 1)
            self.assertIsNone(
                payload["provider"]["cost_bound"]["maximum_cost_cny"]
            )
            self.assertIn(
                "not treated as an internal generation cost bound",
                payload["provider"]["cost_bound"]["basis"],
            )
            encoded = {**payload, "sha256": plan.sha256}
            self.assertEqual(_plan_from_dict(encoded).sha256, plan.sha256)
            encoded["unexpected"] = True
            with self.assertRaisesRegex(ValueError, "plan_contract_invalid"):
                _plan_from_dict(encoded)

    def test_plan_rejects_nonfinite_split_ledger_and_wrong_market_date(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runner = self.runner(root)
            cases = (
                ExperimentBudgetPolicy(
                    **{
                        **budget(root).__dict__,
                        "fx_cny_per_usd": Decimal("NaN"),
                    }
                ),
                ExperimentBudgetPolicy(
                    **{
                        **budget(root).__dict__,
                        "ledger_path": str(root / "split.json"),
                    }
                ),
                ExperimentBudgetPolicy(
                    **{
                        **budget(root).__dict__,
                        "market_date": date(2026, 8, 9),
                    }
                ),
            )
            for policy in cases:
                with self.subTest(policy=policy), self.assertRaisesRegex(
                    ValueError, "hosted_search_plan_invalid"
                ):
                    runner.plan(
                        git_sha=GIT_SHA,
                        credential_path=str(root / "secret.json"),
                        artifact_dir=str(root / "artifact"),
                        expires_at=NOW + timedelta(hours=1),
                        max_tool_calls=5,
                        max_queries=10,
                        pricing=pricing(),
                        budget=policy,
                    )

    def test_run_is_unavailable_before_artifact_budget_secret_or_network(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runner = self.runner(root)
            plan = runner.plan(
                git_sha=GIT_SHA,
                credential_path=str(root / "secret.json"),
                artifact_dir=str(root / "artifact"),
                expires_at=NOW + timedelta(hours=1),
                max_tool_calls=5,
                max_queries=10,
                pricing=pricing(),
                budget=budget(root),
            )

            report = runner.run(plan, approved_plan_sha256=plan.sha256)

            self.assertEqual(report.status, "unavailable")
            self.assertEqual(
                report.failure_code, "provider_cost_bound_unavailable"
            )
            self.assertEqual(report.provider_requests, 0)
            self.assertFalse((root / "artifact").exists())
            self.assertFalse((root / "daily-budget.json").exists())
            self.assertFalse((root / "secret.json").exists())

    def test_authorization_expiry_and_git_drift_precede_capability_decision(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runner = self.runner(root)
            plan = runner.plan(
                git_sha=GIT_SHA,
                credential_path=str(root / "secret.json"),
                artifact_dir=str(root / "artifact"),
                expires_at=NOW + timedelta(minutes=5),
                max_tool_calls=1,
                max_queries=1,
                pricing=pricing(),
                budget=budget(root),
            )
            self.assertEqual(
                runner.run(plan, approved_plan_sha256="0" * 64).failure_code,
                "authorization_mismatch",
            )
            drifted = HostedSearchExperimentRunner(
                git_reader=lambda: ("b" * 40, True),
                budget_ledger_path=str(root / "daily-budget.json"),
                clock=lambda: NOW,
            )
            self.assertEqual(
                drifted.run(plan, approved_plan_sha256=plan.sha256).failure_code,
                "git_state_mismatch",
            )

    def test_verifier_pins_public_ip_and_rechecks_redirect_and_excerpt(self) -> None:
        requests: list[tuple[str, str, str]] = []

        def get(url: str, **request: object) -> VerificationResponse:
            requests.append(
                (
                    url,
                    str(request["resolved_ip"]),
                    str(request["tls_hostname"]),
                )
            )
            if url.endswith("/start"):
                return VerificationResponse(
                    302,
                    {"location": "https://www.sec.gov/final"},
                    b"",
                    url,
                )
            return VerificationResponse(
                200,
                {"content-type": "text/html"},
                b"<p>verified public excerpt</p>",
                url,
            )

        verifier = SafeHttpsWebEvidenceVerifier(
            transport=get,
            resolver=lambda _: ("23.55.12.1",),
            clock=lambda: NOW,
        )
        evidence = verifier.verify(
            url="https://www.sec.gov/start",
            title="SEC",
            provider_excerpt="verified public excerpt",
        )
        self.assertEqual(evidence.source_url, "https://www.sec.gov/final")
        self.assertEqual(
            requests,
            [
                ("https://www.sec.gov/start", "23.55.12.1", "www.sec.gov"),
                ("https://www.sec.gov/final", "23.55.12.1", "www.sec.gov"),
            ],
        )

    def test_cli_plan_is_offline_and_emits_safe_run_argv(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plan_path = root / "approval files" / "plan.json"
            result = main(
                [
                    "plan",
                    "--plan-path",
                    str(plan_path),
                    "--artifact-dir",
                    str(root / "artifact"),
                    "--credential-path",
                    str(root / "missing-secret.json"),
                    "--git-sha",
                    GIT_SHA,
                    "--expires-at",
                    "2099-08-10T13:00:00+00:00",
                    "--fx-snapshot",
                    "fixed-2026-08-10",
                    "--max-tool-calls",
                    "5",
                    "--max-queries",
                    "10",
                    "--fx-cny-per-usd",
                    "7.20",
                    "--per-run-reserve-cny",
                    "4.00",
                ]
            )
            self.assertEqual(result, 0)
            manifest = json.loads(plan_path.read_text("utf-8"))
            self.assertIsNone(
                manifest["provider"]["cost_bound"]["maximum_cost_usd"]
            )
            self.assertFalse((root / "missing-secret.json").exists())

            repository = Path(__file__).resolve().parents[2]
            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "backend.app.personal_workspace.agent.hosted_search_experiment_runner",
                    "plan",
                    "--plan-path",
                    str(root / "subprocess.json"),
                    "--artifact-dir",
                    str(root / "subprocess-artifact"),
                    "--credential-path",
                    str(root / "still-missing.json"),
                    "--git-sha",
                    GIT_SHA,
                    "--expires-at",
                    "2099-08-10T13:00:00+00:00",
                    "--fx-snapshot",
                    "fixed-2026-08-10",
                    "--max-tool-calls",
                    "5",
                    "--max-queries",
                    "10",
                    "--fx-cny-per-usd",
                    "7.20",
                    "--per-run-reserve-cny",
                    "4.00",
                ],
                cwd=repository,
                env=os.environ.copy(),
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            output = json.loads(completed.stdout)
            self.assertEqual(output["run_argv"][0], sys.executable)


if __name__ == "__main__":
    unittest.main()
