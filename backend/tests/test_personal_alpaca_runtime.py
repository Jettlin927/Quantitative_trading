from __future__ import annotations

import base64
from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from time import monotonic
import unittest
from unittest.mock import patch

from backend.app.market_observation.alpaca import ProviderRequest
from backend.app.personal_workspace.instrument import (
    TypedInstrumentObservationReader,
    UnavailableInstrumentObservationReader,
)
from backend.app.personal_workspace.market_runtime import load_personal_market_readers
from backend.app.personal_workspace.portfolio import (
    AlpacaPortfolioMarketReader,
    UnavailablePortfolioMarketReader,
)
from backend.app.personal_workspace.runtime import get_personal_runtime


DATASETS = (
    "alpaca_assets",
    "alpaca_delayed_sip_prices",
    "alpaca_daily_bars",
    "alpaca_corporate_actions",
)


class RecordingTransport:
    def __init__(self) -> None:
        self.requests: list[ProviderRequest] = []

    def send(self, request: ProviderRequest):
        self.requests.append(request)
        raise AssertionError("配置装配阶段不得发出 provider 请求")


class PersonalAlpacaRuntimeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.credentials_path = self.root / "alpaca-credentials.json"
        self.authorization_path = self.root / "alpaca-authorization.json"
        self.credentials = {
            "key_id": "test-key-id",
            "secret_key": "test-secret-key",
        }
        self.authorization = {
            "feed": "sip",
            "delay_seconds": 900,
            "snapshots": [
                {
                    "snapshot_id": f"alpaca-{dataset}-20260803",
                    "source": "alpaca",
                    "dataset": dataset,
                    "plan": "basic_delayed_sip_eod",
                    "display": True,
                    "internal_analysis": True,
                    "ai_context": False,
                    "persist": True,
                    "backfill": False,
                    "redistribute": False,
                    "formal_research": False,
                    "terms_url": "https://alpaca.markets/disclosures",
                    "checked_at": "2026-08-03T08:00:00+00:00",
                    "retention_policy": "personal_private_workspace_only",
                    "evidence_sha256": "a" * 64,
                }
                for dataset in DATASETS
            ],
        }

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_complete_files_build_both_real_readers_without_outbound_request(self) -> None:
        self._write_files()
        transport = RecordingTransport()

        readers = load_personal_market_readers(
            credentials_file=self.credentials_path,
            authorization_file=self.authorization_path,
            transport=transport,
        )

        self.assertIsInstance(readers.portfolio, AlpacaPortfolioMarketReader)
        self.assertIsInstance(readers.instrument, TypedInstrumentObservationReader)
        self.assertEqual(readers.portfolio._adapter._request_deadline_seconds, 3.2)
        self.assertEqual(readers.instrument._provider_wait_seconds, 4.5)
        self.assertEqual(transport.requests, [])
        self.assertEqual(
            readers.evidence_retention_by_authorization,
            {
                ("alpaca", f"alpaca-{dataset}-20260803"): "encrypted_payload"
                for dataset in DATASETS
            },
        )
        self.assertNotIn(self.credentials["key_id"], repr(readers))
        self.assertNotIn(self.credentials["secret_key"], repr(readers))

    def test_slow_instrument_sources_stop_blocking_at_the_aggregate_deadline(self) -> None:
        release = Event()

        class SlowMarket:
            def observe_asset(self, *args, **kwargs):
                release.wait(1)

            def observe_daily_bars(self, *args, **kwargs):
                release.wait(1)

            def observe_corporate_actions(self, *args, **kwargs):
                release.wait(1)

        def slow_official_events(*args, **kwargs):
            release.wait(1)

        reader = TypedInstrumentObservationReader(
            market=SlowMarket(),
            official_events=slow_official_events,
            provider_wait_seconds=0.02,
        )

        started = monotonic()
        observation = reader.open(
            "SYNTH",
            as_of=datetime(2026, 8, 4, tzinfo=timezone.utc),
            limit=10,
        )
        elapsed = monotonic() - started
        release.set()

        self.assertLess(elapsed, 0.2)
        self.assertEqual(observation.source_health, "unavailable")
        self.assertEqual(
            set(observation.issues),
            {
                "asset_identity_unavailable",
                "daily_bars_unavailable",
                "corporate_actions_unavailable",
                "official_events_unavailable",
            },
        )

    def test_incomplete_or_unauthorized_files_fail_closed_without_request(self) -> None:
        cases = {
            "missing_credentials": lambda credentials, authorization: None,
            "malformed_credentials": lambda credentials, authorization: credentials.update(
                {"secret_key": ""}
            ),
            "missing_dataset": lambda credentials, authorization: authorization[
                "snapshots"
            ].pop(),
            "display_denied": lambda credentials, authorization: authorization[
                "snapshots"
            ][0].update({"display": False}),
            "wrong_feed": lambda credentials, authorization: authorization.update(
                {"feed": "iex"}
            ),
            "wrong_delay": lambda credentials, authorization: authorization.update(
                {"delay_seconds": 0}
            ),
            "retention_invalid": lambda credentials, authorization: authorization[
                "snapshots"
            ][0].update({"retention_policy": "caller_selected"}),
        }
        for name, mutate in cases.items():
            with self.subTest(name=name):
                credentials = deepcopy(self.credentials)
                authorization = deepcopy(self.authorization)
                mutate(credentials, authorization)
                if name != "missing_credentials":
                    self._write_json(self.credentials_path, credentials)
                elif self.credentials_path.exists():
                    self.credentials_path.unlink()
                self._write_json(self.authorization_path, authorization)
                transport = RecordingTransport()

                readers = load_personal_market_readers(
                    credentials_file=self.credentials_path,
                    authorization_file=self.authorization_path,
                    transport=transport,
                )

                self.assertIsInstance(readers.portfolio, UnavailablePortfolioMarketReader)
                self.assertIsInstance(
                    readers.instrument, UnavailableInstrumentObservationReader
                )
                self.assertEqual(transport.requests, [])

    def test_ai_context_grant_loads_and_is_enforced_per_request(self) -> None:
        """授权文件允许授予 ai_context；执行门禁在 registry.require 按请求层。"""
        authorization = deepcopy(self.authorization)
        for snapshot in authorization["snapshots"]:
            snapshot["ai_context"] = True
        self._write_json(self.authorization_path, authorization)
        self._write_json(self.credentials_path, self.credentials)
        transport = RecordingTransport()

        readers = load_personal_market_readers(
            credentials_file=self.credentials_path,
            authorization_file=self.authorization_path,
            transport=transport,
        )

        self.assertIsInstance(readers.portfolio, AlpacaPortfolioMarketReader)
        adapter = readers.portfolio._adapter
        granted = adapter._require_authorization("alpaca_daily_bars", "ai_context")
        self.assertTrue(granted.ai_context)
        self.assertEqual(transport.requests, [])

    def test_history_snapshots_are_retained_while_adapter_uses_unique_latest(self) -> None:
        authorization = deepcopy(self.authorization)
        for snapshot in deepcopy(self.authorization["snapshots"]):
            snapshot["snapshot_id"] = snapshot["snapshot_id"].replace(
                "20260803", "20260810"
            )
            snapshot["checked_at"] = "2026-08-10T08:00:00+00:00"
            snapshot["evidence_sha256"] = "b" * 64
            authorization["snapshots"].append(snapshot)
        self._write_json(self.authorization_path, authorization)
        self._write_json(self.credentials_path, self.credentials)

        readers = load_personal_market_readers(
            credentials_file=self.credentials_path,
            authorization_file=self.authorization_path,
            transport=RecordingTransport(),
        )

        self.assertEqual(len(readers.evidence_retention_by_authorization), 8)
        latest = readers.market._require_authorization(
            "alpaca_daily_bars", "display"
        )
        self.assertEqual(latest.snapshot_id, "alpaca-alpaca_daily_bars-20260810")

    def test_missing_credentials_keeps_authorization_history_for_ledger_readback(self) -> None:
        authorization = deepcopy(self.authorization)
        for snapshot in deepcopy(self.authorization["snapshots"]):
            snapshot["snapshot_id"] = snapshot["snapshot_id"].replace(
                "20260803", "20260810"
            )
            snapshot["checked_at"] = "2026-08-10T08:00:00+00:00"
            snapshot["evidence_sha256"] = "b" * 64
            authorization["snapshots"].append(snapshot)
        self._write_json(self.authorization_path, authorization)

        readers = load_personal_market_readers(
            credentials_file=self.credentials_path,
            authorization_file=self.authorization_path,
            transport=RecordingTransport(),
        )

        self.assertIsNone(readers.market)
        self.assertEqual(
            readers.evidence_retention_by_authorization,
            {
                ("alpaca", f"alpaca-{dataset}-{date}"): "encrypted_payload"
                for dataset in DATASETS
                for date in ("20260803", "20260810")
            },
        )

    def test_ambiguous_latest_authorization_fails_closed(self) -> None:
        authorization = deepcopy(self.authorization)
        duplicate = deepcopy(authorization["snapshots"][0])
        duplicate["snapshot_id"] = "alpaca-assets-ambiguous"
        duplicate["evidence_sha256"] = "b" * 64
        authorization["snapshots"].append(duplicate)
        self._write_json(self.authorization_path, authorization)
        self._write_json(self.credentials_path, self.credentials)

        readers = load_personal_market_readers(
            credentials_file=self.credentials_path,
            authorization_file=self.authorization_path,
            transport=RecordingTransport(),
        )

        self.assertIsNone(readers.market)
        self.assertEqual(readers.evidence_retention_by_authorization, {})

    def test_missing_authorization_file_fails_closed_without_request(self) -> None:
        self._write_json(self.credentials_path, self.credentials)
        transport = RecordingTransport()

        readers = load_personal_market_readers(
            credentials_file=self.credentials_path,
            authorization_file=self.authorization_path,
            transport=transport,
        )

        self.assertIsInstance(readers.portfolio, UnavailablePortfolioMarketReader)
        self.assertIsInstance(readers.instrument, UnavailableInstrumentObservationReader)
        self.assertEqual(transport.requests, [])

    def test_personal_runtime_uses_file_backed_alpaca_readers(self) -> None:
        self._write_files()
        gateway_path = self.root / "personal-gateway-token"
        gateway_path.write_text("test-gateway-token", encoding="utf-8")
        keyring_path = self.root / "personal-keyring.json"
        encoded_key = base64.b64encode(b"k" * 32).decode("ascii")
        self._write_json(
            keyring_path,
            {
                "active_key_id": "key-1",
                "data_keys": {"key-1": encoded_key},
                "lookup_key": encoded_key,
            },
        )
        environment = {
            **os.environ,
            "PRIVATE_DATABASE_URL": f"sqlite+pysqlite:///{self.root / 'private.db'}",
            "PERSONAL_GATEWAY_TOKEN_FILE": str(gateway_path),
            "PERSONAL_DATA_KEYRING_FILE": str(keyring_path),
            "PERSONAL_ALLOWED_ORIGINS": "http://127.0.0.1:25173",
            "ALPACA_CREDENTIALS_FILE": str(self.credentials_path),
            "ALPACA_AUTHORIZATION_FILE": str(self.authorization_path),
        }
        with patch.dict(os.environ, environment, clear=True):
            get_personal_runtime.cache_clear()
            runtime = get_personal_runtime()
        get_personal_runtime.cache_clear()

        self.assertTrue(runtime.access.configured)
        self.assertIsInstance(runtime.portfolio._market, AlpacaPortfolioMarketReader)
        self.assertIsInstance(runtime.instruments._source, TypedInstrumentObservationReader)

    def _write_files(self) -> None:
        self._write_json(self.credentials_path, self.credentials)
        self._write_json(self.authorization_path, self.authorization)

    @staticmethod
    def _write_json(path: Path, payload: object) -> None:
        path.write_text(json.dumps(payload), encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
