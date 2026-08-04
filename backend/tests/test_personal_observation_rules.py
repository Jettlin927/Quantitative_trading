from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from threading import Lock
import time
import unittest
from types import SimpleNamespace

from backend.app.personal_workspace.contracts import (
    CreateObservationRuleCommand,
    PersonalActor,
    SetObservationRuleStateCommand,
)
from backend.app.personal_workspace.instrument import (
    InstrumentBar,
    InstrumentEvent,
    InstrumentObservation,
    InstrumentQuery,
    InstrumentWorkbench,
    TypedInstrumentObservationReader,
)
from backend.app.personal_workspace.rules import (
    InMemoryObservationRuleStore,
    ObservationRuleBook,
    RuleEvaluationRequest,
    RuleInput,
)


NOW = datetime(2026, 8, 3, 3, 0, tzinfo=timezone.utc)


def bars(count: int = 30, *, latest: str = "120", volume: int = 200) -> tuple[InstrumentBar, ...]:
    values = []
    for index in range(count):
        close = Decimal(latest) if index == count - 1 else Decimal("100") + index
        values.append(
            InstrumentBar(
                trade_date=date(2026, 6, 1) + timedelta(days=index),
                open=close - 1,
                high=close + 1,
                low=close - 2,
                close=close,
                volume=volume if index == count - 1 else 100,
                evidence_id=f"bar-{index}",
            )
        )
    return tuple(values)


class ScriptedInstrumentSource:
    def __init__(self, observation: InstrumentObservation):
        self.observation = observation

    def open(self, symbol: str, *, as_of: datetime, limit: int) -> InstrumentObservation:
        self.call = (symbol, as_of, limit)
        return self.observation


class ScriptedRuleInputReader:
    def __init__(self, value: RuleInput):
        self.value = value

    def read(self, symbol: str, *, as_of: datetime) -> RuleInput:
        self.call = (symbol, as_of)
        return self.value


class InstrumentWorkbenchTest(unittest.TestCase):
    def test_typed_reader_fetches_independent_sources_concurrently(self) -> None:
        class ConcurrentMarket:
            def __init__(self) -> None:
                self.active = 0
                self.maximum = 0
                self.lock = Lock()

            def enter(self):
                with self.lock:
                    self.active += 1
                    self.maximum = max(self.maximum, self.active)
                time.sleep(0.03)
                with self.lock:
                    self.active -= 1

            def observe_asset(self, symbol, **kwargs):
                self.enter()
                return SimpleNamespace(
                    value=SimpleNamespace(name=symbol),
                    provenance=SimpleNamespace(authorization_snapshot_id="auth-asset"),
                )

            def observe_daily_bars(self, symbol, **kwargs):
                self.enter()
                raise RuntimeError("bars unavailable")

            def observe_corporate_actions(self, symbol, **kwargs):
                self.enter()
                raise RuntimeError("actions unavailable")

        market = ConcurrentMarket()

        def official_events(symbol, as_of):
            market.enter()
            return ()

        TypedInstrumentObservationReader(
            market=market, official_events=official_events
        ).open("ACME", as_of=NOW, limit=1500)

        self.assertGreater(market.maximum, 1)

    def test_typed_reader_fetches_daily_bars_before_optional_sources(self) -> None:
        calls: list[str] = []

        class PriorityMarket:
            def observe_asset(self, symbol, **kwargs):
                calls.append("asset")
                return SimpleNamespace(
                    value=SimpleNamespace(name=symbol),
                    provenance=SimpleNamespace(authorization_snapshot_id="auth-asset"),
                )

            def observe_daily_bars(self, symbol, **kwargs):
                calls.append("bars")
                value = tuple(
                    SimpleNamespace(
                        symbol="ACME",
                        trade_date=item.trade_date,
                        open=item.open,
                        high=item.high,
                        low=item.low,
                        close=item.close,
                        volume=item.volume,
                    )
                    for item in bars(2)
                )
                observed = SimpleNamespace(
                    value=value,
                    source_health="fresh",
                    provenance=SimpleNamespace(
                        content_sha256="a" * 64,
                        authorization_snapshot_id="auth-bars",
                        adjustment_policy="raw",
                    ),
                )
                return SimpleNamespace(raw=observed, provider_adjusted=observed)

            def observe_corporate_actions(self, symbol, **kwargs):
                calls.append("actions")
                raise RuntimeError("actions unavailable")

        def official_events(symbol, as_of):
            calls.append("official")
            return ()

        observation = TypedInstrumentObservationReader(
            market=PriorityMarket(), official_events=official_events
        ).open("ACME", as_of=NOW, limit=1500)

        self.assertEqual(calls[0], "bars")
        self.assertEqual(len(observation.raw_bars), 2)

    def test_typed_d1_d2_adapter_preserves_raw_adjusted_and_authorization_identity(self) -> None:
        raw = bars(2)
        adjusted = bars(2, latest="60")

        def observed(value, adjustment, snapshot):
            return SimpleNamespace(
                value=tuple(
                    SimpleNamespace(
                        symbol="ACME",
                        trade_date=item.trade_date,
                        open=item.open,
                        high=item.high,
                        low=item.low,
                        close=item.close,
                        volume=item.volume,
                    )
                    for item in value
                ),
                source_health="fresh",
                provenance=SimpleNamespace(
                    content_sha256="a" * 64,
                    authorization_snapshot_id=snapshot,
                    adjustment_policy=adjustment,
                ),
            )

        class TypedMarket:
            def observe_asset(self, symbol, **kwargs):
                return SimpleNamespace(
                    value=SimpleNamespace(name="Acme Holdings"),
                    provenance=SimpleNamespace(authorization_snapshot_id="auth-asset"),
                )

            def observe_daily_bars(self, symbol, **kwargs):
                return SimpleNamespace(
                    raw=observed(raw, "raw", "auth-bars"),
                    provider_adjusted=observed(adjusted, "all", "auth-bars"),
                )

            def observe_corporate_actions(self, symbol, **kwargs):
                return observed((), None, "auth-actions")

        official = SimpleNamespace(
            identity="bls:cpi:2026-07:event",
            event_type="macro_release",
            evidence_identity="bls:cpi:2026-07",
            occurred_at=NOW,
            authorization=SimpleNamespace(snapshot_id="auth-bls"),
        )
        projection = TypedInstrumentObservationReader(
            market=TypedMarket(), official_events=lambda symbol, as_of: (official,)
        ).open("ACME", as_of=NOW, limit=1500)

        self.assertEqual(projection.name, "Acme Holdings")
        self.assertEqual(projection.raw_bars[-1].close, Decimal("120"))
        self.assertEqual(projection.provider_adjusted_bars[-1].close, Decimal("60"))
        self.assertEqual(projection.events[0].track, "macro")
        self.assertEqual(
            projection.authorization_snapshot_ids,
            ("auth-asset", "auth-bars", "auth-actions", "auth-bls"),
        )

    def test_open_separates_raw_adjusted_events_cost_and_evidence_identity(self) -> None:
        raw = bars()
        adjusted = tuple(
            InstrumentBar(**{**bar.__dict__, "close": bar.close * Decimal("0.5")})
            for bar in raw
        )
        event = InstrumentEvent(
            event_id="sec:accession:0001",
            track="corporate",
            event_type="sec_filing",
            label="10-Q 已确认",
            occurred_at=NOW - timedelta(days=2),
            evidence_ids=("sec:accession:0001",),
            confirmation_state="confirmed",
        )
        source = ScriptedInstrumentSource(
            InstrumentObservation(
                symbol="ACME",
                name="Acme Holdings",
                raw_bars=raw,
                provider_adjusted_bars=adjusted,
                events=(event,),
                source_health="fresh",
                authorization_snapshot_ids=("auth-market-display", "auth-sec-display"),
                issues=(),
            )
        )
        workbench = InstrumentWorkbench(
            source=source,
            cost_reader=lambda actor, symbol: Decimal("100.25"),
            rule_attention_reader=lambda actor, symbol: (),
            formal_overlay_reader=lambda symbol: (),
        )

        workspace = workbench.open(
            PersonalActor(actor_id="owner"),
            InstrumentQuery(symbol="acme", as_of=NOW, selected_date=date(2026, 6, 30)),
        )

        self.assertEqual(source.call, ("ACME", NOW, 1500))
        self.assertEqual(workspace.cost_reference.value, "100.2500")
        self.assertEqual(workspace.cost_reference.identity, "current_manual_average_cost")
        self.assertFalse(workspace.cost_reference.historical_position_track)
        self.assertEqual(workspace.raw_bars[-1].close, "120.0000")
        self.assertEqual(workspace.provider_adjusted_bars[-1].close, "60.0000")
        self.assertEqual(workspace.event_tracks[0].track, "corporate")
        self.assertIn("bar-29", workspace.evidence_inspector.evidence_ids)
        self.assertFalse(workspace.formal_research_overlay.research_eligible)


class ObservationRuleBookTest(unittest.TestCase):
    def setUp(self) -> None:
        self.actor = PersonalActor(actor_id="owner")
        self.input_reader = ScriptedRuleInputReader(
            RuleInput(
                symbol="ACME",
                raw_bars=bars(),
                adjusted_bars=bars(),
                events=(
                    InstrumentEvent(
                        event_id="sec:accession:0001",
                        track="corporate",
                        event_type="sec_filing",
                        label="10-Q 已确认",
                        occurred_at=NOW - timedelta(days=2),
                        evidence_ids=("sec:accession:0001",),
                        confirmation_state="confirmed",
                    ),
                ),
                source_health="fresh",
                evidence_ids=tuple(f"bar-{index}" for index in range(30)),
                corporate_actions_available=True,
            )
        )
        self.book = ObservationRuleBook(
            store=InMemoryObservationRuleStore(),
            inputs=self.input_reader,
            clock=lambda: NOW,
        )

    def test_registry_has_exactly_eight_versioned_non_research_templates(self) -> None:
        templates = self.book.list_templates(self.actor)

        self.assertEqual(
            tuple(template.template_id for template in templates),
            (
                "price_threshold",
                "return_window",
                "moving_average_state",
                "realized_volatility",
                "rolling_drawdown",
                "volume_ratio",
                "confirmed_event_window",
                "macro_release_window",
            ),
        )
        self.assertTrue(all(template.version == 1 for template in templates))
        self.assertTrue(all(not template.research_eligible for template in templates))

    def test_rule_requires_user_enable_and_writes_immutable_four_state_evaluation(self) -> None:
        draft = self.book.revise(
            self.actor,
            CreateObservationRuleCommand(
                type="create_rule",
                template_id="price_threshold",
                symbol="ACME",
                parameters={"direction": "gte", "price": "110"},
            ),
            idempotency_key="create-price",
        )
        enabled = self.book.revise(
            self.actor,
            SetObservationRuleStateCommand(
                type="set_rule_state",
                rule_id=draft.rule_id,
                expected_revision=1,
                state="enabled",
            ),
            idempotency_key="enable-price",
        )
        batch = self.book.evaluate(
            self.actor,
            RuleEvaluationRequest(symbol="ACME", as_of=NOW),
            idempotency_key="evaluate-price",
        )

        self.assertEqual(draft.state, "draft")
        self.assertEqual(enabled.revision, 2)
        self.assertEqual(batch.evaluations[0].result, "hit")
        self.assertEqual(batch.evaluations[0].observed_value, "120.0000")
        self.assertEqual(batch.evaluations[0].threshold, "110.0000")
        self.assertEqual(len(batch.evaluations[0].fingerprint), 64)
        self.assertEqual(self.book.attention(self.actor, symbol="ACME")[0].kind, "rule_hit")

    def test_missing_adjustment_and_source_failure_are_visible_not_false_not_hit(self) -> None:
        self.input_reader.value = RuleInput(
            symbol="ACME",
            raw_bars=bars(3),
            adjusted_bars=(),
            events=(),
            source_health="unavailable",
            evidence_ids=("bar-0", "bar-1", "bar-2"),
            corporate_actions_available=False,
        )
        draft = self.book.revise(
            self.actor,
            CreateObservationRuleCommand(
                type="create_rule",
                template_id="return_window",
                symbol="ACME",
                parameters={"window": 5, "direction": "gte", "threshold": "0.1"},
            ),
            idempotency_key="create-return",
        )
        self.book.revise(
            self.actor,
            SetObservationRuleStateCommand(
                type="set_rule_state",
                rule_id=draft.rule_id,
                expected_revision=1,
                state="enabled",
            ),
            idempotency_key="enable-return",
        )

        evaluation = self.book.evaluate(
            self.actor,
            RuleEvaluationRequest(symbol="ACME", as_of=NOW),
            idempotency_key="evaluate-return",
        ).evaluations[0]

        self.assertEqual(evaluation.result, "insufficient_data")
        self.assertEqual(evaluation.reason_code, "adjusted_series_unavailable")
        self.assertEqual(evaluation.source_health, "unavailable")


if __name__ == "__main__":
    unittest.main()
