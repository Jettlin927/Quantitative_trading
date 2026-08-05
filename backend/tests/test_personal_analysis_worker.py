from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from threading import Event
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from backend.app.personal_analysis_worker import (
    PersonalAnalysisWorker,
    load_deepseek_credentials_file,
)
from backend.app.personal_workspace.contracts import PersonalActor


class PersonalAnalysisWorkerConfigurationTest(unittest.TestCase):
    def test_partial_rule_failure_log_does_not_expose_private_symbols(self) -> None:
        stop_event = Event()

        class OneJobWorkspace:
            def run_next(self, *, worker_id):
                stop_event.set()
                return object()

        class PartialAutomation:
            def run_once(self, actor, *, as_of):
                return SimpleNamespace(failed_symbols=("PRIVATE",))

        worker = PersonalAnalysisWorker(
            workspace=OneJobWorkspace(),
            worker_id="personal-analysis-worker-test",
            rule_automation=PartialAutomation(),
        )

        with patch(
            "backend.app.personal_analysis_worker.LOGGER.warning"
        ) as log_warning:
            worker.run_forever(
                poll_seconds=5,
                stop_event=stop_event,
                monotonic=lambda: 0.0,
            )

        log_warning.assert_called_once_with(
            "personal_rule_automation_partial_failure count=%s", 1
        )
        self.assertNotIn("PRIVATE", repr(log_warning.call_args))

    def test_rule_automation_runs_on_schedule_even_while_analysis_queue_is_busy(self) -> None:
        stop_event = Event()

        class BusyWorkspace:
            calls = 0

            def run_next(self, *, worker_id):
                self.calls += 1
                if self.calls == 2:
                    stop_event.set()
                return object()

        class RecordingAutomation:
            calls = []

            def run_once(self, actor, *, as_of):
                self.calls.append((actor, as_of))

        workspace = BusyWorkspace()
        automation = RecordingAutomation()
        moments = iter((0.0, 901.0))
        now = datetime(2026, 8, 5, 22, 0, tzinfo=timezone.utc)
        worker = PersonalAnalysisWorker(
            workspace=workspace,
            worker_id="personal-analysis-worker-test",
            rule_automation=automation,
            rule_interval_seconds=900,
            actor=PersonalActor(actor_id="local-owner"),
        )

        worker.run_forever(
            poll_seconds=5,
            stop_event=stop_event,
            monotonic=lambda: next(moments),
            clock=lambda: now,
        )

        self.assertEqual(workspace.calls, 2)
        self.assertEqual(automation.calls, [(worker.actor, now), (worker.actor, now)])

    def test_rule_automation_failure_does_not_stop_analysis_queue(self) -> None:
        stop_event = Event()

        class OneJobWorkspace:
            calls = 0

            def run_next(self, *, worker_id):
                self.calls += 1
                stop_event.set()
                return object()

        class FailingAutomation:
            def run_once(self, actor, *, as_of):
                raise RuntimeError("synthetic_rule_failure")

        workspace = OneJobWorkspace()
        worker = PersonalAnalysisWorker(
            workspace=workspace,
            worker_id="personal-analysis-worker-test",
            rule_automation=FailingAutomation(),
            rule_interval_seconds=900,
        )

        with patch(
            "backend.app.personal_analysis_worker.LOGGER.exception"
        ) as log_exception:
            worker.run_forever(
                poll_seconds=5,
                stop_event=stop_event,
                monotonic=lambda: 0.0,
            )

        self.assertEqual(workspace.calls, 1)
        log_exception.assert_called_once_with("personal_rule_automation_failed")

    def test_deepseek_credentials_file_is_exact_and_redacted(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "deepseek-credentials.json"
            path.write_text(
                json.dumps({"api_key": "synthetic-deepseek-key-never-log"}),
                encoding="utf-8",
            )
            path.chmod(0o600)

            credentials = load_deepseek_credentials_file(path)

        self.assertEqual(credentials.api_key, "synthetic-deepseek-key-never-log")
        self.assertEqual(repr(credentials), "DeepSeekCredentials(api_key=<redacted>)")
        self.assertNotIn("synthetic-deepseek-key-never-log", repr(credentials))

    def test_deepseek_credentials_file_rejects_extra_fields_and_broad_mode(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            extra = Path(root) / "extra.json"
            extra.write_text(
                json.dumps({"api_key": "synthetic-key", "base_url": "https://attacker.invalid"}),
                encoding="utf-8",
            )
            extra.chmod(0o600)
            with self.assertRaisesRegex(ValueError, "deepseek_credentials_invalid"):
                load_deepseek_credentials_file(extra)

            broad = Path(root) / "broad.json"
            broad.write_text(json.dumps({"api_key": "synthetic-key"}), encoding="utf-8")
            broad.chmod(0o640)
            with self.assertRaisesRegex(ValueError, "deepseek_credentials_mode_invalid"):
                load_deepseek_credentials_file(broad)


if __name__ == "__main__":
    unittest.main()
