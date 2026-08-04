from __future__ import annotations

import unittest
from datetime import date, datetime, timedelta, timezone
import json
from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

from backend.app.market_observation.alpaca import (
    AlpacaCredentials,
    AlpacaMarketObservationAdapter,
    AlpacaRequestPolicy,
    EodFallbackPrice,
    MarketObservationError,
    ProviderRequest,
    ProviderRequestRejected,
    ProviderResponse,
    UrllibProviderTransport,
)
from backend.app.market_observation.contracts import (
    AppendOnlyAuthorizationRegistry,
    AuthorizationDenied,
    SourceAuthorizationSnapshot,
)
from backend.app.market_observation.testing import DenyRecordingTransport


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "alpaca_synthetic"


class AlpacaRequestPolicyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = AlpacaRequestPolicy()

    def test_only_fixed_market_data_and_paper_asset_gets_are_allowed(self) -> None:
        allowed = (
            "https://data.alpaca.markets/v2/stocks/SYNTH/bars?timeframe=1Day&feed=sip",
            "https://data.alpaca.markets/v2/stocks/SYNTH/snapshot?feed=delayed_sip",
            "https://data.alpaca.markets/v1/corporate-actions?symbols=SYNTH",
            "https://paper-api.alpaca.markets/v2/assets",
            "https://paper-api.alpaca.markets/v2/assets/SYNTH",
        )
        for url in allowed:
            with self.subTest(url=url):
                self.policy.require_allowed(ProviderRequest(method="GET", url=url))

    def test_trading_account_stream_redirect_and_malformed_targets_are_rejected(self) -> None:
        denied = (
            ("POST", "https://paper-api.alpaca.markets/v2/orders"),
            ("GET", "https://paper-api.alpaca.markets/v2/account"),
            ("GET", "https://paper-api.alpaca.markets/v2/positions"),
            ("GET", "https://paper-api.alpaca.markets/v2/portfolio/history"),
            ("GET", "https://paper-api.alpaca.markets/v2/account/activities"),
            ("GET", "https://stream.data.alpaca.markets/v2/sip"),
            ("GET", "wss://stream.data.alpaca.markets/v2/sip"),
            ("GET", "https://data.alpaca.markets.evil.example/v2/stocks/SYNTH/bars"),
            ("GET", "https://data.alpaca.markets:444/v2/stocks/SYNTH/bars"),
            ("GET", "https://user@data.alpaca.markets/v2/stocks/SYNTH/bars"),
            ("GET", "http://data.alpaca.markets/v2/stocks/SYNTH/bars"),
        )
        for method, url in denied:
            with self.subTest(method=method, url=url):
                with self.assertRaisesRegex(ProviderRequestRejected, "request_target_denied"):
                    self.policy.require_allowed(ProviderRequest(method=method, url=url))

    def test_cross_origin_redirect_is_rejected_before_following(self) -> None:
        with self.assertRaisesRegex(ProviderRequestRejected, "redirect_target_denied"):
            self.policy.require_redirect_allowed(
                "https://data.alpaca.markets/v2/stocks/SYNTH/bars",
                "https://api.alpaca.markets/v2/account",
            )


class UrllibProviderTransportTest(unittest.TestCase):
    def test_json_response_is_normalized_without_following_redirects(self) -> None:
        class RedirectingOpener:
            def __init__(self) -> None:
                self.calls = 0

            def open(self, _request, timeout):
                self.calls += 1
                self.timeout = timeout
                raise HTTPError(
                    "https://data.alpaca.markets/v2/stocks/SYNTH/bars",
                    302,
                    "Found",
                    {"Location": "https://api.alpaca.markets/v2/account"},
                    BytesIO(b"{}"),
                )

        opener = RedirectingOpener()
        transport = UrllibProviderTransport(opener=opener)
        response = transport.send(
            ProviderRequest(
                method="GET",
                url="https://data.alpaca.markets/v2/stocks/SYNTH/bars",
                headers={"APCA-API-KEY-ID": "synthetic"},
                connect_timeout_seconds=5.0,
                total_timeout_seconds=15.0,
            )
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "https://api.alpaca.markets/v2/account")
        self.assertEqual(response.body, {})
        self.assertEqual(opener.calls, 1)
        self.assertEqual(opener.timeout, 5.0)


class SourceAuthorizationSnapshotTest(unittest.TestCase):
    def snapshot(self, *, snapshot_id: str, checked_at: datetime, display: bool = True):
        return SourceAuthorizationSnapshot(
            snapshot_id=snapshot_id,
            source="alpaca",
            dataset="us_stock_bars",
            plan="basic_delayed_sip_eod",
            display=display,
            internal_analysis=True,
            ai_context=False,
            persist=True,
            backfill=True,
            redistribute=False,
            formal_research=False,
            terms_url="https://alpaca.markets/disclosures",
            checked_at=checked_at,
            retention_policy="按来源条款保留",
            evidence_sha256="a" * 64,
        )

    def test_registry_is_append_only_and_latest_snapshot_controls_each_purpose(self) -> None:
        registry = AppendOnlyAuthorizationRegistry()
        older = self.snapshot(
            snapshot_id="auth-old",
            checked_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
        )
        latest = self.snapshot(
            snapshot_id="auth-latest",
            checked_at=datetime(2026, 8, 2, tzinfo=timezone.utc),
            display=False,
        )
        registry.append(older)
        registry.append(latest)

        self.assertEqual(registry.snapshots, (older, latest))
        self.assertEqual(
            registry.require("alpaca", "us_stock_bars", "basic_delayed_sip_eod", "persist"),
            latest,
        )
        with self.assertRaisesRegex(AuthorizationDenied, "entitlement_denied"):
            registry.require("alpaca", "us_stock_bars", "basic_delayed_sip_eod", "display")
        with self.assertRaisesRegex(AuthorizationDenied, "entitlement_denied"):
            registry.require("alpaca", "us_stock_bars", "basic_delayed_sip_eod", "ai_context")

    def test_missing_or_ambiguous_authorization_fails_closed(self) -> None:
        registry = AppendOnlyAuthorizationRegistry()
        with self.assertRaisesRegex(AuthorizationDenied, "authorization_snapshot_missing"):
            registry.require("alpaca", "us_stock_bars", "basic_delayed_sip_eod", "display")

        snapshot = self.snapshot(
            snapshot_id="same-id",
            checked_at=datetime(2026, 8, 2, tzinfo=timezone.utc),
        )
        registry.append(snapshot)
        with self.assertRaisesRegex(ValueError, "authorization_snapshot_not_append_only"):
            registry.append(snapshot)

        registry.append(
            self.snapshot(
                snapshot_id="same-time-different-id",
                checked_at=datetime(2026, 8, 2, tzinfo=timezone.utc),
                display=False,
            )
        )
        with self.assertRaisesRegex(AuthorizationDenied, "authorization_snapshot_ambiguous"):
            registry.require(
                "alpaca", "us_stock_bars", "basic_delayed_sip_eod", "display"
            )


class AlpacaMarketObservationAdapterTest(unittest.TestCase):
    def authorization_registry(self, dataset: str) -> AppendOnlyAuthorizationRegistry:
        registry = AppendOnlyAuthorizationRegistry()
        registry.append(
            SourceAuthorizationSnapshot(
                snapshot_id=f"auth-{dataset}",
                source="alpaca",
                dataset=dataset,
                plan="basic_delayed_sip_eod",
                display=True,
                internal_analysis=True,
                ai_context=False,
                persist=True,
                backfill=True,
                redistribute=False,
                formal_research=False,
                terms_url="https://alpaca.markets/disclosures",
                checked_at=datetime(2026, 8, 2, tzinfo=timezone.utc),
                retention_policy="按来源条款保留",
                evidence_sha256="b" * 64,
            )
        )
        return registry

    def test_asset_identity_is_typed_and_request_capture_never_contains_credentials(self) -> None:
        body = json.loads((FIXTURE_ROOT / "asset.json").read_text(encoding="utf-8"))
        transport = DenyRecordingTransport(
            [ProviderResponse(status_code=200, headers={}, body=body)]
        )
        adapter = AlpacaMarketObservationAdapter(
            transport=transport,
            authorizations=self.authorization_registry("alpaca_assets"),
            credentials=AlpacaCredentials(
                key_id="synthetic-key-id-not-a-secret",
                secret_key="synthetic-secret-not-a-secret",
            ),
        )

        observed = adapter.observe_asset("synth")

        self.assertEqual(observed.availability, "available")
        self.assertEqual(observed.value.symbol, "SYNTH")
        self.assertEqual(observed.value.name, "Synthetic Observation Corp")
        self.assertEqual(observed.value.asset_class, "us_equity")
        self.assertEqual(observed.provenance.authorization_snapshot_id, "auth-alpaca_assets")
        self.assertEqual(observed.provenance.qualification, "online_observation")
        self.assertFalse(observed.provenance.ai_context)
        self.assertFalse(observed.provenance.formal_research)
        self.assertEqual(len(observed.provenance.content_sha256), 64)
        self.assertEqual(
            transport.requests[0].url,
            "https://paper-api.alpaca.markets/v2/assets/SYNTH",
        )
        captured = repr(transport.requests)
        self.assertNotIn("synthetic-key-id-not-a-secret", captured)
        self.assertNotIn("synthetic-secret-not-a-secret", captured)
        self.assertEqual(
            transport.requests[0].header_names,
            ("APCA-API-KEY-ID", "APCA-API-SECRET-KEY"),
        )

    def test_asset_identity_rejects_provider_symbol_mismatch(self) -> None:
        body = json.loads((FIXTURE_ROOT / "asset.json").read_text(encoding="utf-8"))
        body["symbol"] = "OTHER"
        adapter = AlpacaMarketObservationAdapter(
            transport=DenyRecordingTransport(
                [ProviderResponse(status_code=200, headers={}, body=body)]
            ),
            authorizations=self.authorization_registry("alpaca_assets"),
            credentials=AlpacaCredentials("synthetic-id", "synthetic-secret"),
        )

        with self.assertRaises(MarketObservationError) as raised:
            adapter.observe_asset("SYNTH")

        self.assertEqual(raised.exception.code, "provider_symbol_mismatch")

    def test_delayed_price_uses_basic_plan_delayed_sip_snapshot(self) -> None:
        body = json.loads(
            (FIXTURE_ROOT / "delayed_snapshot.json").read_text(encoding="utf-8")
        )
        transport = DenyRecordingTransport(
            [ProviderResponse(status_code=200, headers={}, body=body)]
        )
        adapter = AlpacaMarketObservationAdapter(
            transport=transport,
            authorizations=self.authorization_registry("alpaca_delayed_sip_prices"),
            credentials=AlpacaCredentials("synthetic-id", "synthetic-secret"),
        )
        observed_at = datetime(2026, 8, 3, 14, 0, tzinfo=timezone.utc)

        observed = adapter.observe_delayed_price("SYNTH", observed_at=observed_at)

        self.assertEqual(observed.availability, "available")
        self.assertEqual(str(observed.value.price), "101.25")
        self.assertEqual(observed.value.currency, "USD")
        self.assertEqual(observed.value.feed, "delayed_sip")
        self.assertEqual(observed.value.delay_seconds, 960)
        self.assertEqual(observed.as_of.isoformat(), "2026-08-03T13:44:00+00:00")
        query = parse_qs(urlsplit(transport.requests[0].url).query)
        self.assertEqual(query["feed"], ["delayed_sip"])
        self.assertEqual(urlsplit(transport.requests[0].url).path, "/v2/stocks/SYNTH/snapshot")
        self.assertFalse(observed.provenance.ai_context)

    def test_recent_sip_payload_is_rejected_instead_of_being_silently_used(self) -> None:
        body = json.loads(
            (FIXTURE_ROOT / "delayed_snapshot.json").read_text(encoding="utf-8")
        )
        body["minuteBar"]["t"] = "2026-08-03T13:59:00Z"
        transport = DenyRecordingTransport(
            [ProviderResponse(status_code=200, headers={}, body=body)]
        )
        adapter = AlpacaMarketObservationAdapter(
            transport=transport,
            authorizations=self.authorization_registry("alpaca_delayed_sip_prices"),
            credentials=AlpacaCredentials("synthetic-id", "synthetic-secret"),
        )

        observed = adapter.observe_delayed_price(
            "SYNTH", observed_at=datetime(2026, 8, 3, 14, 0, tzinfo=timezone.utc)
        )

        self.assertEqual(observed.availability, "not_available")
        self.assertEqual(observed.reason_code, "sip_delay_not_proven")
        self.assertIsNone(observed.value)

    def test_closed_market_snapshot_falls_back_to_latest_daily_close(self) -> None:
        body = json.loads(
            (FIXTURE_ROOT / "delayed_snapshot.json").read_text(encoding="utf-8")
        )
        body["minuteBar"] = None
        transport = DenyRecordingTransport(
            [ProviderResponse(status_code=200, headers={}, body=body)]
        )
        adapter = AlpacaMarketObservationAdapter(
            transport=transport,
            authorizations=self.authorization_registry("alpaca_delayed_sip_prices"),
            credentials=AlpacaCredentials("synthetic-id", "synthetic-secret"),
        )

        observed = adapter.observe_delayed_price(
            "SYNTH", observed_at=datetime(2026, 8, 4, 3, 0, tzinfo=timezone.utc)
        )

        self.assertEqual(observed.availability, "available")
        self.assertEqual(str(observed.value.price), "101.25")
        self.assertEqual(observed.value.feed, "eod")
        self.assertEqual(observed.source_health, "stale")
        self.assertEqual(observed.reason_code, "latest_close_fallback")

    def test_daily_bars_keep_raw_and_provider_adjusted_series_separate(self) -> None:
        raw = json.loads(
            (FIXTURE_ROOT / "daily_bars_raw.json").read_text(encoding="utf-8")
        )
        adjusted = json.loads(
            (FIXTURE_ROOT / "daily_bars_adjusted.json").read_text(encoding="utf-8")
        )
        transport = DenyRecordingTransport(
            [
                ProviderResponse(status_code=200, headers={}, body=raw),
                ProviderResponse(status_code=200, headers={}, body=adjusted),
            ]
        )
        adapter = AlpacaMarketObservationAdapter(
            transport=transport,
            authorizations=self.authorization_registry("alpaca_daily_bars"),
            credentials=AlpacaCredentials("synthetic-id", "synthetic-secret"),
        )

        observed = adapter.observe_daily_bars(
            "SYNTH",
            start_date=date(2026, 7, 30),
            end_date=date(2026, 7, 31),
            fetched_at=datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc),
        )

        self.assertEqual([str(bar.close) for bar in observed.raw.value], ["101.0", "103.0"])
        self.assertEqual(
            [str(bar.close) for bar in observed.provider_adjusted.value],
            ["50.5", "51.5"],
        )
        self.assertEqual([bar.volume for bar in observed.raw.value], [250000, 275000])
        self.assertEqual(observed.raw.provenance.adjustment_policy, "raw")
        self.assertEqual(observed.provider_adjusted.provenance.adjustment_policy, "all")
        self.assertEqual(observed.raw.provenance.qualification, "traceable_history")
        queries = [parse_qs(urlsplit(request.url).query) for request in transport.requests]
        self.assertEqual([query["adjustment"] for query in queries], [["raw"], ["all"]])
        self.assertTrue(all(query["feed"] == ["iex"] for query in queries))

    def test_daily_bars_share_one_total_provider_deadline(self) -> None:
        raw = json.loads(
            (FIXTURE_ROOT / "daily_bars_raw.json").read_text(encoding="utf-8")
        )

        class Clock:
            value = 100.0

            def __call__(self) -> float:
                return self.value

        class SlowFirstResponseTransport:
            def __init__(self, clock: Clock) -> None:
                self.clock = clock
                self.requests: list[ProviderRequest] = []

            def send(self, request: ProviderRequest) -> ProviderResponse:
                self.requests.append(request)
                self.clock.value += 1.9
                return ProviderResponse(status_code=200, headers={}, body=raw)

        clock = Clock()
        transport = SlowFirstResponseTransport(clock)
        adapter = AlpacaMarketObservationAdapter(
            transport=transport,
            authorizations=self.authorization_registry("alpaca_daily_bars"),
            credentials=AlpacaCredentials("synthetic-id", "synthetic-secret"),
            monotonic=clock,
            request_deadline_seconds=1.8,
        )

        observed = adapter.observe_daily_bars(
            "SYNTH",
            start_date=date(2026, 7, 30),
            end_date=date(2026, 7, 31),
            fetched_at=datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc),
        )

        self.assertEqual(observed.raw.availability, "available")
        self.assertEqual(len(observed.raw.value), 2)
        self.assertEqual(observed.provider_adjusted.availability, "not_available")
        self.assertIsNone(observed.provider_adjusted.value)
        self.assertEqual(observed.provider_adjusted.reason_code, "provider_timeout")
        self.assertEqual(
            observed.provider_adjusted.provenance.missing_reason,
            "provider_timeout",
        )
        self.assertEqual(len(transport.requests), 1)

    def test_corporate_actions_are_typed_without_exposing_provider_dicts(self) -> None:
        body = json.loads(
            (FIXTURE_ROOT / "corporate_actions.json").read_text(encoding="utf-8")
        )
        transport = DenyRecordingTransport(
            [ProviderResponse(status_code=200, headers={}, body=body)]
        )
        adapter = AlpacaMarketObservationAdapter(
            transport=transport,
            authorizations=self.authorization_registry("alpaca_corporate_actions"),
            credentials=AlpacaCredentials("synthetic-id", "synthetic-secret"),
        )

        observed = adapter.observe_corporate_actions(
            "SYNTH",
            start_date=date(2026, 7, 1),
            end_date=date(2026, 8, 1),
            fetched_at=datetime(2026, 8, 2, 6, 30, tzinfo=timezone.utc),
        )

        actions = observed.value
        self.assertEqual([action.action_type for action in actions], ["cash_dividend", "forward_split"])
        self.assertEqual(actions[0].symbol, "SYNTH")
        self.assertEqual(str(actions[0].cash_amount), "0.125")
        self.assertEqual(actions[0].currency, "USD")
        self.assertEqual(actions[1].ratio_numerator, 2)
        self.assertEqual(actions[1].ratio_denominator, 1)
        self.assertFalse(hasattr(actions[0], "provider_payload"))
        self.assertEqual(observed.provenance.dataset, "alpaca_corporate_actions")
        self.assertFalse(observed.provenance.ai_context)
        query = parse_qs(urlsplit(transport.requests[0].url).query)
        self.assertEqual(query["symbols"], ["SYNTH"])
        self.assertEqual(query["start"], ["2026-07-01"])
        self.assertEqual(query["end"], ["2026-08-01"])

    def test_corporate_actions_reject_any_provider_symbol_mismatch(self) -> None:
        body = json.loads(
            (FIXTURE_ROOT / "corporate_actions.json").read_text(encoding="utf-8")
        )
        body["corporate_actions"]["cash_dividends"][0]["symbol"] = "OTHER"
        adapter = AlpacaMarketObservationAdapter(
            transport=DenyRecordingTransport(
                [ProviderResponse(status_code=200, headers={}, body=body)]
            ),
            authorizations=self.authorization_registry("alpaca_corporate_actions"),
            credentials=AlpacaCredentials("synthetic-id", "synthetic-secret"),
        )

        with self.assertRaises(MarketObservationError) as raised:
            adapter.observe_corporate_actions(
                "SYNTH",
                start_date=date(2026, 7, 1),
                end_date=date(2026, 8, 1),
                fetched_at=datetime(2026, 8, 2, 6, 30, tzinfo=timezone.utc),
            )

        self.assertEqual(raised.exception.code, "provider_symbol_mismatch")

    def test_timeout_uses_explicit_eod_fallback_and_marks_it_stale(self) -> None:
        transport = DenyRecordingTransport([TimeoutError("synthetic timeout")])
        adapter = AlpacaMarketObservationAdapter(
            transport=transport,
            authorizations=self.authorization_registry("alpaca_delayed_sip_prices"),
            credentials=AlpacaCredentials("synthetic-id", "synthetic-secret"),
            eod_fallback=lambda symbol: EodFallbackPrice(
                symbol=symbol,
                price="99.50",
                as_of=datetime(2026, 7, 31, 20, 0, tzinfo=timezone.utc),
                identity="public-eod:SYNTH:2026-07-31",
            ),
        )

        observed = adapter.observe_delayed_price(
            "SYNTH", observed_at=datetime(2026, 8, 3, 14, 0, tzinfo=timezone.utc)
        )

        self.assertEqual(observed.availability, "available")
        self.assertEqual(str(observed.value.price), "99.50")
        self.assertEqual(observed.source_health, "stale")
        self.assertEqual(observed.reason_code, "provider_timeout_eod_fallback")
        self.assertEqual(observed.provenance.fallback_identity, "public-eod:SYNTH:2026-07-31")
        self.assertEqual(transport.requests[0].connect_timeout_seconds, 5.0)
        self.assertLessEqual(transport.requests[0].total_timeout_seconds, 15.0)

    def test_timeout_rejects_eod_fallback_for_another_symbol(self) -> None:
        adapter = AlpacaMarketObservationAdapter(
            transport=DenyRecordingTransport([TimeoutError("synthetic timeout")]),
            authorizations=self.authorization_registry("alpaca_delayed_sip_prices"),
            credentials=AlpacaCredentials("synthetic-id", "synthetic-secret"),
            eod_fallback=lambda _symbol: EodFallbackPrice(
                symbol="OTHER",
                price="99.50",
                as_of=datetime(2026, 7, 31, 20, 0, tzinfo=timezone.utc),
                identity="public-eod:OTHER:2026-07-31",
            ),
        )

        with self.assertRaises(MarketObservationError) as raised:
            adapter.observe_delayed_price(
                "SYNTH", observed_at=datetime(2026, 8, 3, 14, 0, tzinfo=timezone.utc)
            )

        self.assertEqual(raised.exception.code, "fallback_symbol_mismatch")

    def test_timeout_rejects_eod_fallback_without_identity(self) -> None:
        adapter = AlpacaMarketObservationAdapter(
            transport=DenyRecordingTransport([TimeoutError("synthetic timeout")]),
            authorizations=self.authorization_registry("alpaca_delayed_sip_prices"),
            credentials=AlpacaCredentials("synthetic-id", "synthetic-secret"),
            eod_fallback=lambda symbol: EodFallbackPrice(
                symbol=symbol,
                price="99.50",
                as_of=datetime(2026, 7, 31, 20, 0, tzinfo=timezone.utc),
                identity="  ",
            ),
        )

        with self.assertRaises(MarketObservationError) as raised:
            adapter.observe_delayed_price(
                "SYNTH", observed_at=datetime(2026, 8, 3, 14, 0, tzinfo=timezone.utc)
            )

        self.assertEqual(raised.exception.code, "fallback_identity_missing")

    def test_429_respects_retry_after_and_stops_after_three_attempts(self) -> None:
        transport = DenyRecordingTransport(
            [
                ProviderResponse(status_code=429, headers={"Retry-After": "2"}, body={}),
                ProviderResponse(status_code=429, headers={"Retry-After": "2"}, body={}),
                ProviderResponse(status_code=429, headers={"Retry-After": "2"}, body={}),
            ]
        )
        sleeps: list[float] = []
        adapter = AlpacaMarketObservationAdapter(
            transport=transport,
            authorizations=self.authorization_registry("alpaca_assets"),
            credentials=AlpacaCredentials("synthetic-id", "synthetic-secret"),
            sleeper=sleeps.append,
        )

        with self.assertRaises(MarketObservationError) as raised:
            adapter.observe_asset("SYNTH")

        self.assertEqual(raised.exception.code, "provider_rate_limited")
        self.assertEqual(raised.exception.retry_after_seconds, 2.0)
        self.assertEqual(len(transport.requests), 3)
        self.assertEqual(sleeps, [2.0, 2.0])

    def test_missing_sip_entitlement_bad_schema_and_redirect_have_stable_codes(self) -> None:
        cases = (
            (
                ProviderResponse(
                    status_code=403,
                    headers={},
                    body={"message": "subscription does not permit querying SIP data"},
                ),
                "entitlement_denied",
            ),
            (ProviderResponse(status_code=200, headers={}, body={"bars": [{}]}), "provider_schema_invalid"),
            (
                ProviderResponse(
                    status_code=302,
                    headers={"Location": "https://api.alpaca.markets/v2/account"},
                    body={},
                ),
                "redirect_target_denied",
            ),
        )
        for response, expected_code in cases:
            with self.subTest(expected_code=expected_code):
                transport = DenyRecordingTransport([response])
                adapter = AlpacaMarketObservationAdapter(
                    transport=transport,
                    authorizations=self.authorization_registry("alpaca_delayed_sip_prices"),
                    credentials=AlpacaCredentials("synthetic-id", "synthetic-secret"),
                )
                with self.assertRaises((MarketObservationError, ProviderRequestRejected)) as raised:
                    adapter.observe_delayed_price(
                        "SYNTH",
                        observed_at=datetime(2026, 8, 3, 14, 0, tzinfo=timezone.utc),
                    )
                self.assertIn(expected_code, str(raised.exception))

    def test_authorization_and_current_ai_policy_fail_before_transport(self) -> None:
        missing = AppendOnlyAuthorizationRegistry()
        transport = DenyRecordingTransport([])
        adapter = AlpacaMarketObservationAdapter(
            transport=transport,
            authorizations=missing,
            credentials=AlpacaCredentials("synthetic-id", "synthetic-secret"),
        )
        with self.assertRaisesRegex(AuthorizationDenied, "authorization_snapshot_missing"):
            adapter.observe_asset("SYNTH")
        self.assertEqual(transport.requests, [])

        # ai_context 是逐请求用途：未授予时 entitlement_denied，授予后由用途门禁放行
        registry = self.authorization_registry("alpaca_assets")  # ai_context=False
        with self.assertRaisesRegex(AuthorizationDenied, "entitlement_denied"):
            registry.require(
                "alpaca", "alpaca_assets", "basic_delayed_sip_eod", "ai_context"
            )
        granted = AppendOnlyAuthorizationRegistry()
        granted.append(
            SourceAuthorizationSnapshot(
                **{
                    **registry.snapshots[0].__dict__,
                    "snapshot_id": "granted-ai-context",
                    "ai_context": True,
                }
            )
        )
        granted_snapshot = granted.require(
            "alpaca", "alpaca_assets", "basic_delayed_sip_eod", "ai_context"
        )
        self.assertTrue(granted_snapshot.ai_context)
        self.assertEqual(transport.requests, [])

    def test_local_limit_rejects_the_121st_request_without_touching_transport(self) -> None:
        asset = json.loads((FIXTURE_ROOT / "asset.json").read_text(encoding="utf-8"))
        transport = DenyRecordingTransport(
            [ProviderResponse(status_code=200, headers={}, body=asset) for _ in range(120)]
        )
        adapter = AlpacaMarketObservationAdapter(
            transport=transport,
            authorizations=self.authorization_registry("alpaca_assets"),
            credentials=AlpacaCredentials("synthetic-id", "synthetic-secret"),
            monotonic=lambda: 1000.0,
        )
        for _ in range(120):
            adapter.observe_asset("SYNTH")

        with self.assertRaises(MarketObservationError) as raised:
            adapter.observe_asset("SYNTH")

        self.assertEqual(raised.exception.code, "provider_rate_limited")
        self.assertEqual(raised.exception.retry_after_seconds, 60.0)
        self.assertEqual(len(transport.requests), 120)

    def test_timeout_without_eod_fallback_is_explicitly_unavailable(self) -> None:
        adapter = AlpacaMarketObservationAdapter(
            transport=DenyRecordingTransport([TimeoutError("synthetic timeout")]),
            authorizations=self.authorization_registry("alpaca_delayed_sip_prices"),
            credentials=AlpacaCredentials("synthetic-id", "synthetic-secret"),
        )
        observed = adapter.observe_delayed_price(
            "SYNTH", observed_at=datetime(2026, 8, 3, 14, 0, tzinfo=timezone.utc)
        )
        self.assertEqual(observed.availability, "not_available")
        self.assertEqual(observed.source_health, "unavailable")
        self.assertEqual(observed.reason_code, "provider_timeout")
        self.assertIsNone(observed.value)


if __name__ == "__main__":
    unittest.main()
