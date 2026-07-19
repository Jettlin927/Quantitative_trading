from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from scripts.research.report_evidence import (
    canonical_report_timestamp,
    verify_reproduction_evidence,
)


class ReportEvidenceTest(unittest.TestCase):
    def setUp(self):
        self.runs = {
            "first": {
                "manifest": {
                    "runId": "run-1",
                    "codeCommit": "abc123",
                    "generatedAt": "2026-07-18T20:00:03+00:00",
                    "resultFingerprint": "fingerprint-1",
                }
            },
            "second": {
                "manifest": {
                    "runId": "run-2",
                    "codeCommit": "abc123",
                    "generatedAt": "2026-07-18T20:03:53+00:00",
                    "resultFingerprint": "fingerprint-2",
                }
            },
        }

    def test_timestamp_is_derived_from_latest_canonical_manifest(self):
        self.assertEqual(
            canonical_report_timestamp(self.runs),
            "2026-07-19T04:03:53+08:00",
        )

    def test_two_matching_offline_rounds_are_verified(self):
        with tempfile.TemporaryDirectory() as tmp:
            evidence_path = Path(tmp) / "evidence.json"
            evidence_path.write_text(
                json.dumps(
                    {
                        "schemaVersion": 1,
                        "verifiedAt": "2026-07-19",
                        "codeCommit": "abc123",
                        "imageDigest": "sha256:" + "a" * 64,
                        "networkMode": "none",
                        "rounds": [
                            {
                                "round": 1,
                                "resultFingerprints": {
                                    "run-1": "fingerprint-1",
                                    "run-2": "fingerprint-2",
                                    "run-extra": "fingerprint-extra",
                                },
                            },
                            {
                                "round": 2,
                                "resultFingerprints": {
                                    "run-1": "fingerprint-1",
                                    "run-2": "fingerprint-2",
                                    "run-extra": "fingerprint-extra",
                                },
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )

            result = verify_reproduction_evidence(evidence_path, self.runs)

        self.assertEqual(result["matchesPerRun"], 2)
        self.assertEqual(result["runCount"], 2)
        self.assertTrue(result["networkDisabled"])
        self.assertTrue(result["allMatched"])
        self.assertEqual(result["imageDigest"], "sha256:" + "a" * 64)

    def test_missing_or_mismatched_evidence_stops_publication(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            missing = root / "missing.json"
            with self.assertRaisesRegex(ValueError, "复现证据"):
                verify_reproduction_evidence(missing, self.runs)

            evidence_path = root / "evidence.json"
            evidence_path.write_text(
                json.dumps(
                    {
                        "schemaVersion": 1,
                        "verifiedAt": "2026-07-19",
                        "codeCommit": "abc123",
                        "imageDigest": "sha256:" + "a" * 64,
                        "networkMode": "none",
                        "rounds": [
                            {
                                "round": 1,
                                "resultFingerprints": {
                                    "run-1": "wrong",
                                    "run-2": "fingerprint-2",
                                },
                            },
                            {
                                "round": 2,
                                "resultFingerprints": {
                                    "run-1": "fingerprint-1",
                                    "run-2": "fingerprint-2",
                                },
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "指纹"):
                verify_reproduction_evidence(evidence_path, self.runs)


if __name__ == "__main__":
    unittest.main()
