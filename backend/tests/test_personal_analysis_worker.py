from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from backend.app.personal_analysis_worker import load_deepseek_credentials_file


class PersonalAnalysisWorkerConfigurationTest(unittest.TestCase):
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
