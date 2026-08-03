from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from urllib.error import HTTPError

from backend.app.official_evidence.contracts import TransportRequest, TransportResponse
from backend.app.personal_workspace.analysis import AnalysisIntent
from backend.app.personal_workspace.contracts import PersonalActor
from backend.app.personal_workspace.official_evidence_runtime import (
    UrllibOfficialEvidenceTransport,
    load_official_analysis_evidence_reader,
)


NOW = datetime(2026, 8, 3, 4, 0, tzinfo=timezone.utc)


class ScriptedOfficialTransport:
    def __init__(self, responses: dict[str, bytes]) -> None:
        self.responses = responses
        self.requests = []

    def send(self, request):
        self.requests.append(request)
        body = self.responses[request.url]
        return TransportResponse(
            status_code=200,
            final_url=request.url,
            content_type="application/json",
            body=body,
        )


class PersonalOfficialEvidenceRuntimeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.query_path = self.root / "official-analysis-queries.json"
        self.authorization_path = self.root / "official-analysis-authorization.json"
        self.query_config = {
            "schema_version": 1,
            "revision": "official-analysis-fixture-v1",
            "checked_at": "2026-08-03T03:00:00+00:00",
            "expires_at": "2026-08-04T03:00:00+00:00",
            "queries": [
                {
                    "query_id": "acme-revenue",
                    "subject_id": "ACME",
                    "kind": "sec_companyfacts",
                    "cik": "123",
                    "taxonomy": "us-gaap",
                    "tag": "RevenueFromContractWithCustomerExcludingAssessedTax",
                    "unit": "USD",
                    "authorization_snapshot_id": "auth-sec-companyfacts-v1",
                },
                {
                    "query_id": "macro-cpi",
                    "subject_id": "*",
                    "kind": "bls_series",
                    "series_id": "SYNTH-CPI-U",
                    "start_year": 2026,
                    "end_year": 2026,
                    "release": {
                        "published_at": "2026-07-15T12:30:00+00:00",
                        "effective_at": "2026-06-30T00:00:00+00:00",
                        "available_from": "2026-07-15T12:30:00+00:00",
                        "vintage": "2026-07-15",
                        "revision": "initial",
                    },
                    "authorization_snapshot_id": "auth-bls-series-v1",
                },
            ],
        }
        self.authorization_config = {
            "schema_version": 1,
            "checked_at": "2026-08-03T03:00:00+00:00",
            "expires_at": "2026-08-04T03:00:00+00:00",
            "snapshots": [
                self._authorization(
                    snapshot_id="auth-sec-companyfacts-v1",
                    source="sec",
                    dataset="companyfacts:us-gaap",
                ),
                self._authorization(
                    snapshot_id="auth-bls-series-v1",
                    source="bls",
                    dataset="public_api_v2_timeseries",
                ),
            ],
        }

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_valid_configs_fetch_minimal_company_and_macro_facts(self) -> None:
        self._write_configs()
        transport = ScriptedOfficialTransport(self._responses())
        reader = load_official_analysis_evidence_reader(
            query_file=self.query_path,
            authorization_file=self.authorization_path,
            sec_user_agent="QuantitativeTrading tests@example.invalid",
            clock=lambda: NOW,
            transport=transport,
        )
        self.assertEqual(transport.requests, [])

        result = reader(
            PersonalActor(actor_id="local-owner"),
            AnalysisIntent(question="官方事实如何影响公司？", subject_ids=("ACME",)),
        )

        self.assertEqual(result.gaps, ())
        self.assertEqual(len(result.candidates), 2)
        self.assertEqual(
            [(item.source, item.field, item.as_of) for item in result.candidates],
            [
                ("sec", "official_facts", datetime(2026, 7, 30, 20, 15, 30, tzinfo=timezone.utc)),
                ("bls", "macro_facts", datetime(2026, 7, 15, 12, 30, tzinfo=timezone.utc)),
            ],
        )
        self.assertTrue(all(item.authorized_for_ai for item in result.candidates))
        self.assertEqual(len(transport.requests), 3)
        self.assertNotIn("quantity", str(result).lower())
        self.assertNotIn("average_cost", str(result).lower())

    def test_valid_frozen_official_fact_is_available_without_network_fetch(self) -> None:
        excerpt = (
            "SEC companyfacts us-gaap Revenue reference_period=2026-Q2 "
            "value=512000000 unit=USD context=CY2026Q2"
        )
        self.query_config["queries"] = [self._frozen_query()]
        self._write_configs()
        transport = ScriptedOfficialTransport({})

        reader = load_official_analysis_evidence_reader(
            query_file=self.query_path,
            authorization_file=self.authorization_path,
            sec_user_agent="QuantitativeTrading tests@example.invalid",
            clock=lambda: NOW,
            transport=transport,
        )
        result = reader(
            PersonalActor(actor_id="local-owner"),
            AnalysisIntent(question="冻结官方事实是什么？", subject_ids=("NET",)),
        )

        self.assertEqual(result.gaps, ())
        self.assertEqual(transport.requests, [])
        self.assertEqual(len(result.candidates), 1)
        self.assertEqual(result.candidates[0].evidence_id, "sec:net:revenue:2026q2")
        self.assertEqual(result.candidates[0].excerpt, excerpt)
        self.assertEqual(result.candidates[0].as_of, datetime(2026, 7, 30, 20, 15, 30, tzinfo=timezone.utc))

    def test_frozen_official_fact_with_wrong_content_hash_fails_closed(self) -> None:
        self.query_config["queries"] = [
            self._frozen_query(excerpt="tampered excerpt")
        ]
        self._write_configs()

        reader = load_official_analysis_evidence_reader(
            query_file=self.query_path,
            authorization_file=self.authorization_path,
            sec_user_agent="QuantitativeTrading tests@example.invalid",
            clock=lambda: NOW,
            transport=ScriptedOfficialTransport({}),
        )
        result = reader(
            PersonalActor(actor_id="local-owner"),
            AnalysisIntent(question="必须失败关闭", subject_ids=("NET",)),
        )

        self.assertEqual(result.candidates, ())
        self.assertEqual(result.gaps, ("official_evidence_config_unavailable",))

    def test_frozen_official_fact_must_match_its_authorization_source(self) -> None:
        self.query_config["queries"] = [self._frozen_query(source="bls")]
        self._write_configs()

        reader = load_official_analysis_evidence_reader(
            query_file=self.query_path,
            authorization_file=self.authorization_path,
            sec_user_agent="QuantitativeTrading tests@example.invalid",
            clock=lambda: NOW,
            transport=ScriptedOfficialTransport({}),
        )
        result = reader(
            PersonalActor(actor_id="local-owner"),
            AnalysisIntent(question="必须失败关闭", subject_ids=("NET",)),
        )

        self.assertEqual(result.candidates, ())
        self.assertEqual(result.gaps, ("official_evidence_config_unavailable",))

    def test_future_frozen_official_fact_is_not_available(self) -> None:
        self.query_config["queries"] = [
            self._frozen_query(as_of="2026-08-03T05:00:00+00:00")
        ]
        self._write_configs()

        reader = load_official_analysis_evidence_reader(
            query_file=self.query_path,
            authorization_file=self.authorization_path,
            sec_user_agent="QuantitativeTrading tests@example.invalid",
            clock=lambda: NOW,
            transport=ScriptedOfficialTransport({}),
        )
        result = reader(
            PersonalActor(actor_id="local-owner"),
            AnalysisIntent(question="必须失败关闭", subject_ids=("NET",)),
        )

        self.assertEqual(result.candidates, ())
        self.assertEqual(
            result.gaps,
            ("official_evidence_not_available:net-frozen-revenue",),
        )

    def test_frozen_official_fact_rejects_non_official_fields(self) -> None:
        self.query_config["queries"] = [self._frozen_query(field="market_prices")]
        self._write_configs()

        reader = load_official_analysis_evidence_reader(
            query_file=self.query_path,
            authorization_file=self.authorization_path,
            sec_user_agent="QuantitativeTrading tests@example.invalid",
            clock=lambda: NOW,
            transport=ScriptedOfficialTransport({}),
        )
        result = reader(
            PersonalActor(actor_id="local-owner"),
            AnalysisIntent(question="必须失败关闭", subject_ids=("NET",)),
        )

        self.assertEqual(result.candidates, ())
        self.assertEqual(result.gaps, ("official_evidence_config_unavailable",))

    def test_missing_stale_or_hash_invalid_config_fails_closed_without_fetch(self) -> None:
        cases = ("missing", "stale", "hash_invalid")
        for case in cases:
            with self.subTest(case=case):
                self._write_configs()
                if case == "missing":
                    self.query_path.unlink()
                elif case == "stale":
                    query = dict(self.query_config)
                    query["expires_at"] = (NOW - timedelta(seconds=1)).isoformat()
                    self._write_hashed(self.query_path, query)
                else:
                    payload = json.loads(self.authorization_path.read_text(encoding="utf-8"))
                    payload["snapshots"][0]["ai_context"] = False
                    self.authorization_path.write_text(json.dumps(payload), encoding="utf-8")
                transport = ScriptedOfficialTransport(self._responses())
                reader = load_official_analysis_evidence_reader(
                    query_file=self.query_path,
                    authorization_file=self.authorization_path,
                    sec_user_agent="QuantitativeTrading tests@example.invalid",
                    clock=lambda: NOW,
                    transport=transport,
                )

                result = reader(
                    PersonalActor(actor_id="local-owner"),
                    AnalysisIntent(question="必须失败关闭", subject_ids=("ACME",)),
                )

                self.assertEqual(result.candidates, ())
                self.assertEqual(len(result.gaps), 1)
                self.assertIn(
                    result.gaps[0],
                    {
                        "official_evidence_config_unavailable",
                        "official_evidence_config_stale",
                        "official_evidence_config_hash_invalid",
                    },
                )
                self.assertEqual(transport.requests, [])

    def test_production_transport_does_not_follow_official_source_redirects(self) -> None:
        class RedirectingOpener:
            def __init__(self) -> None:
                self.requests = []

            def open(self, request, timeout):
                self.requests.append((request.full_url, timeout))
                raise HTTPError(
                    request.full_url,
                    302,
                    "redirect",
                    {"Location": "https://attacker.invalid/collect"},
                    BytesIO(b"redirect rejected"),
                )

        opener = RedirectingOpener()
        transport = UrllibOfficialEvidenceTransport(
            timeout_seconds=3,
            opener=opener,
        )

        response = transport.send(
            TransportRequest(
                method="GET",
                url="https://data.sec.gov/submissions/CIK0000000123.json",
            )
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.final_url, opener.requests[0][0])
        self.assertEqual(len(opener.requests), 1)

    @staticmethod
    def _frozen_query(**overrides) -> dict:
        query = {
            "query_id": "net-frozen-revenue",
            "subject_id": "NET",
            "kind": "frozen_official_fact",
            "source": "sec",
            "dataset": "companyfacts:us-gaap",
            "evidence_id": "sec:net:revenue:2026q2",
            "field": "official_facts",
            "excerpt": (
                "SEC companyfacts us-gaap Revenue reference_period=2026-Q2 "
                "value=512000000 unit=USD context=CY2026Q2"
            ),
            "content_sha256": "f5c404844a5fb4636efd7a4c82bf1a42137956d7780f986d199181de5700abaa",
            "as_of": "2026-07-30T20:15:30+00:00",
            "authorization_snapshot_id": "auth-sec-companyfacts-v1",
        }
        return {**query, **overrides}

    def _write_configs(self) -> None:
        self._write_hashed(self.query_path, self.query_config)
        self._write_hashed(self.authorization_path, self.authorization_config)

    @staticmethod
    def _write_hashed(path: Path, payload: dict) -> None:
        canonical = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        path.write_text(
            json.dumps({**payload, "content_sha256": sha256(canonical).hexdigest()}),
            encoding="utf-8",
        )

    @staticmethod
    def _authorization(*, snapshot_id: str, source: str, dataset: str) -> dict:
        return {
            "snapshot_id": snapshot_id,
            "source": source,
            "dataset": dataset,
            "plan": "official_public_source",
            "display": True,
            "internal_analysis": True,
            "ai_context": True,
            "persist": True,
            "backfill": False,
            "redistribute": False,
            "formal_research": False,
            "terms_url": "https://example.invalid/terms",
            "checked_at": "2026-08-03T03:00:00+00:00",
            "retention_policy": "personal_private_workspace_only",
            "evidence_sha256": "a" * 64,
        }

    @staticmethod
    def _responses() -> dict[str, bytes]:
        sec_submissions = {
            "filings": {
                "recent": {
                    "accessionNumber": ["0000000123-26-000001"],
                    "filingDate": ["2026-07-30"],
                    "reportDate": ["2026-06-30"],
                    "acceptanceDateTime": ["2026-07-30T20:15:30.000Z"],
                    "form": ["10-Q"],
                    "primaryDocument": ["synthetic-20260630.htm"],
                }
            }
        }
        sec_companyfacts = {
            "facts": {
                "us-gaap": {
                    "RevenueFromContractWithCustomerExcludingAssessedTax": {
                        "units": {
                            "USD": [
                                {
                                    "end": "2026-06-30",
                                    "val": 1000000,
                                    "accn": "0000000123-26-000001",
                                    "fy": 2026,
                                    "fp": "Q2",
                                    "form": "10-Q",
                                    "frame": "CY2026Q2",
                                },
                                {
                                    "end": "2020-12-31",
                                    "val": 500000,
                                    "accn": "0000000123-21-000099",
                                    "fy": 2020,
                                    "fp": "FY",
                                    "form": "10-K",
                                    "frame": "CY2020",
                                },
                            ]
                        }
                    }
                }
            }
        }
        bls = {
            "status": "REQUEST_SUCCEEDED",
            "Results": {
                "series": [
                    {
                        "seriesID": "SYNTH-CPI-U",
                        "data": [
                            {"year": "2026", "period": "M06", "value": "321.500"}
                        ],
                    }
                ]
            },
        }
        return {
            "https://data.sec.gov/submissions/CIK0000000123.json": json.dumps(
                sec_submissions
            ).encode(),
            "https://data.sec.gov/api/xbrl/companyfacts/CIK0000000123.json": json.dumps(
                sec_companyfacts
            ).encode(),
            "https://api.bls.gov/publicAPI/v2/timeseries/data/": json.dumps(bls).encode(),
        }


if __name__ == "__main__":
    unittest.main()
