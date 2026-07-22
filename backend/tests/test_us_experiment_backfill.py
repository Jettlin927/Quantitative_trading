from __future__ import annotations

from datetime import date
from argparse import Namespace
import importlib.util
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest
from unittest.mock import patch


SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "ops" / "backfill_us_experiment.py"
SPEC = importlib.util.spec_from_file_location("backfill_us_experiment", SCRIPT_PATH)
assert SPEC and SPEC.loader
BACKFILL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BACKFILL)


class UsExperimentBackfillTest(unittest.TestCase):
    def test_cron_entry_scripts_are_executable(self):
        repository_root = SCRIPT_PATH.parents[2]
        for relative_path in (
            "scripts/ops/install_us_experiment_cron.sh",
            "scripts/ops/sync_us_experiment_daily.sh",
        ):
            mode = (repository_root / relative_path).stat().st_mode
            self.assertTrue(mode & stat.S_IXUSR, relative_path)

    def test_daily_and_cron_scripts_forward_private_symbol_file_without_embedding_symbols(self):
        repository_root = SCRIPT_PATH.parents[2]
        daily = (repository_root / "scripts/ops/sync_us_experiment_daily.sh").read_text(encoding="utf-8")
        installer = (repository_root / "scripts/ops/install_us_experiment_cron.sh").read_text(encoding="utf-8")

        self.assertIn('SOURCE_CODES_FILE="${SOURCE_CODES_FILE:-}"', daily)
        self.assertIn('--source-codes-file "$SOURCE_CODES_FILE"', daily)
        self.assertIn('SOURCE_CODES_FILE="${SOURCE_CODES_FILE:-}"', installer)
        self.assertIn('SOURCE_CODES_FILE=${SOURCE_CODES_FILE}', installer)
        for private_symbol in ("HIDDEN1", "HIDDEN2"):
            self.assertNotIn(private_symbol, daily)
            self.assertNotIn(private_symbol, installer)

    def test_cron_installer_rejects_shell_metacharacters_in_private_symbol_path(self):
        installer = SCRIPT_PATH.parents[2] / "scripts/ops/install_us_experiment_cron.sh"
        result = subprocess.run(
            [str(installer)],
            env={
                "HOME": "/tmp",
                "PATH": "/usr/bin:/bin",
                "SOURCE_CODES_FILE": "/tmp/symbols;touch-pwned",
            },
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 2)
        self.assertIn("只能使用安全的绝对路径字符", result.stderr)

    def test_validation_sample_is_deterministic_and_date_rotated(self):
        codes = [f"105.TEST{index:03d}" for index in range(100)]
        first = BACKFILL.deterministic_validation_sample(codes, date(2026, 7, 21), 10)
        repeated = BACKFILL.deterministic_validation_sample(list(reversed(codes)), date(2026, 7, 21), 10)
        next_day = BACKFILL.deterministic_validation_sample(codes, date(2026, 7, 22), 10)

        self.assertEqual(first, repeated)
        self.assertEqual(len(first), 10)
        self.assertNotEqual(first, next_day)

    def test_checkpoint_refuses_a_different_frozen_contract(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "checkpoint.json"
            BACKFILL.save_checkpoint(path, {"contractSha256": "a" * 64, "completedSourceCodes": ["105.AAPL"]})
            with self.assertRaises(RuntimeError):
                BACKFILL.load_checkpoint(path, "b" * 64)

    def test_chunks_keep_the_full_ordered_universe(self):
        codes = [f"105.TEST{index}" for index in range(5)]
        self.assertEqual(BACKFILL.chunked(codes, 2), [codes[:2], codes[2:4], codes[4:]])

    def test_private_symbol_file_is_normalized_without_account_metadata(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "symbols.txt"
            path.write_text("# local-only\naapl\nMETA\nAAPL\n\n", encoding="utf-8")
            self.assertEqual(BACKFILL.read_target_symbols(path), ["AAPL", "META"])

            path.write_text("12345\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                BACKFILL.read_target_symbols(path)

    def test_private_symbol_file_registers_only_targeted_source_codes(self):
        class FakeClient:
            def __init__(self):
                self.calls = []

            def submit_and_wait(self, action, payload):
                self.calls.append((action, payload))
                if action == "us_experiment_targeted_universe":
                    return {
                        "id": "targeted",
                        "status": "ok",
                        "result": {"sourceCodes": ["TGT.AAPL", "TGT.META"]},
                    }
                if action == "us_experiment_overview_refresh":
                    return {"id": "refresh", "status": "ok", "result": {}}
                return {
                    "id": "prices",
                    "status": "ok",
                    "result": {"successfulSourceCodes": payload["source_codes"], "failed": []},
                }

        fake_client = FakeClient()
        with tempfile.TemporaryDirectory() as temporary:
            symbol_file = Path(temporary) / "symbols.txt"
            symbol_file.write_text("AAPL\nMETA\n", encoding="utf-8")
            args = Namespace(
                api_base="http://test",
                start_date=date(2026, 7, 20),
                end_date=date(2026, 7, 21),
                batch_size=20,
                batch_delay_seconds=0,
                validation_sample_size=30,
                max_symbols=0,
                retry_attempts=2,
                retry_base_delay_seconds=1,
                poll_seconds=1,
                job_timeout_seconds=30,
                checkpoint=Path(temporary) / "checkpoint.json",
                skip_universe_refresh=False,
                source_codes_file=symbol_file,
            )
            with patch.object(BACKFILL, "parse_args", return_value=args), patch.object(
                BACKFILL,
                "ApiClient",
                return_value=fake_client,
            ):
                self.assertEqual(BACKFILL.main(), 0)

        self.assertEqual(
            fake_client.calls[0],
            ("us_experiment_targeted_universe", {"symbols": ["AAPL", "META"]}),
        )
        price_call = next(item for item in fake_client.calls if item[0] == "us_experiment_prices")
        self.assertEqual(price_call[1]["source_codes"], ["TGT.AAPL", "TGT.META"])
        self.assertEqual(price_call[1]["validation_source_codes"], [])

    def test_worker_level_failure_keeps_the_batch_pending_for_retry(self):
        class FakeClient:
            def __init__(self):
                self.price_calls = 0

            def current_source_codes(self):
                return ["105.AAPL"]

            def submit_and_wait(self, action, payload):
                if action == "us_experiment_overview_refresh":
                    self.refreshed = True
                    return {"id": "refresh", "status": "ok", "result": {}}
                self.price_calls += 1
                if self.price_calls == 1:
                    return {"id": "first", "status": "failed", "message": "temporary", "result": {"error": "temporary"}}
                return {
                    "id": "second",
                    "status": "ok",
                    "result": {"successfulSourceCodes": payload["source_codes"], "failed": []},
                }

        fake_client = FakeClient()
        with tempfile.TemporaryDirectory() as temporary:
            args = Namespace(
                api_base="http://test",
                start_date=date(2026, 7, 20),
                end_date=date(2026, 7, 21),
                batch_size=50,
                batch_delay_seconds=0,
                validation_sample_size=1,
                max_symbols=0,
                retry_attempts=2,
                retry_base_delay_seconds=1,
                poll_seconds=1,
                job_timeout_seconds=30,
                checkpoint=Path(temporary) / "checkpoint.json",
                skip_universe_refresh=True,
            )
            with patch.object(BACKFILL, "parse_args", return_value=args), patch.object(
                BACKFILL,
                "ApiClient",
                return_value=fake_client,
            ), patch.object(BACKFILL.time, "sleep") as sleep:
                self.assertEqual(BACKFILL.main(), 0)

        self.assertEqual(fake_client.price_calls, 2)
        self.assertTrue(fake_client.refreshed)
        sleep.assert_called_once_with(1)

    def test_remaining_failures_return_partial_exit_code_and_refresh_snapshot(self):
        class FakeClient:
            def __init__(self):
                self.refreshed = False

            def current_source_codes(self):
                return ["105.AAPL"]

            def submit_and_wait(self, action, payload):
                if action == "us_experiment_overview_refresh":
                    self.refreshed = True
                    return {"id": "refresh", "status": "ok", "result": {}}
                code = payload["source_codes"][0]
                return {
                    "id": "partial-job",
                    "status": "partial",
                    "result": {
                        "successfulSourceCodes": [],
                        "failed": [{"sourceCode": code, "error": "rate limited"}],
                    },
                }

        fake_client = FakeClient()
        with tempfile.TemporaryDirectory() as temporary:
            args = Namespace(
                api_base="http://test",
                start_date=date(2026, 7, 20),
                end_date=date(2026, 7, 21),
                batch_size=20,
                batch_delay_seconds=0,
                validation_sample_size=0,
                max_symbols=0,
                retry_attempts=2,
                retry_base_delay_seconds=1,
                poll_seconds=1,
                job_timeout_seconds=30,
                checkpoint=Path(temporary) / "checkpoint.json",
                skip_universe_refresh=True,
            )
            with patch.object(BACKFILL, "parse_args", return_value=args), patch.object(
                BACKFILL,
                "ApiClient",
                return_value=fake_client,
            ), patch.object(BACKFILL.time, "sleep"):
                self.assertEqual(BACKFILL.main(), 2)

        self.assertTrue(fake_client.refreshed)

    def test_validation_alert_returns_partial_even_when_all_symbols_succeed(self):
        class FakeClient:
            def current_source_codes(self):
                return ["105.AAPL"]

            def submit_and_wait(self, action, payload):
                if action == "us_experiment_overview_refresh":
                    return {"id": "refresh", "status": "ok", "result": {}}
                return {
                    "id": "validation-alert",
                    "status": "partial",
                    "result": {
                        "successfulSourceCodes": payload["source_codes"],
                        "failed": [],
                        "validationAlerts": [{"sourceCode": "105.AAPL", "status": "mismatch"}],
                    },
                }

        with tempfile.TemporaryDirectory() as temporary:
            args = Namespace(
                api_base="http://test",
                start_date=date(2026, 7, 20),
                end_date=date(2026, 7, 21),
                batch_size=20,
                batch_delay_seconds=0,
                validation_sample_size=1,
                max_symbols=0,
                retry_attempts=2,
                retry_base_delay_seconds=1,
                poll_seconds=1,
                job_timeout_seconds=30,
                checkpoint=Path(temporary) / "checkpoint.json",
                skip_universe_refresh=True,
            )
            with patch.object(BACKFILL, "parse_args", return_value=args), patch.object(
                BACKFILL,
                "ApiClient",
                return_value=FakeClient(),
            ):
                self.assertEqual(BACKFILL.main(), 2)

    def test_resumed_checkpoint_preserves_validation_alert_exit_code(self):
        class FakeClient:
            def __init__(self):
                self.price_calls = 0

            def current_source_codes(self):
                return ["105.AAPL"]

            def submit_and_wait(self, action, payload):
                if action == "us_experiment_overview_refresh":
                    return {"id": "refresh", "status": "ok", "result": {}}
                self.price_calls += 1
                return {
                    "id": "validation-alert",
                    "status": "partial",
                    "result": {
                        "successfulSourceCodes": payload["source_codes"],
                        "failed": [],
                        "validationAlerts": [{"sourceCode": "105.AAPL", "status": "mismatch"}],
                    },
                }

        fake_client = FakeClient()
        with tempfile.TemporaryDirectory() as temporary:
            args = Namespace(
                api_base="http://test",
                start_date=date(2026, 7, 20),
                end_date=date(2026, 7, 21),
                batch_size=20,
                batch_delay_seconds=0,
                validation_sample_size=1,
                max_symbols=0,
                retry_attempts=2,
                retry_base_delay_seconds=1,
                poll_seconds=1,
                job_timeout_seconds=30,
                checkpoint=Path(temporary) / "checkpoint.json",
                skip_universe_refresh=True,
            )
            with patch.object(BACKFILL, "parse_args", return_value=args), patch.object(
                BACKFILL,
                "ApiClient",
                return_value=fake_client,
            ):
                self.assertEqual(BACKFILL.main(), 2)
                self.assertEqual(BACKFILL.main(), 2)

        self.assertEqual(fake_client.price_calls, 1)


if __name__ == "__main__":
    unittest.main()
