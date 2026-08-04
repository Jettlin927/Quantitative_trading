from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import re
from time import monotonic
from typing import Any, Callable, Literal, Protocol

from .contracts import PersonalActor


TrackKind = Literal["corporate", "macro", "data_gap", "personal_rule", "formal_research"]


@dataclass(frozen=True)
class InstrumentQuery:
    symbol: str
    as_of: datetime
    selected_date: date | None = None
    limit: int = 120


@dataclass(frozen=True)
class InstrumentBar:
    trade_date: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
    evidence_id: str


@dataclass(frozen=True)
class InstrumentEvent:
    event_id: str
    track: TrackKind
    event_type: str
    label: str
    occurred_at: datetime
    evidence_ids: tuple[str, ...]
    confirmation_state: str


@dataclass(frozen=True)
class InstrumentObservation:
    symbol: str
    name: str
    raw_bars: tuple[InstrumentBar, ...]
    provider_adjusted_bars: tuple[InstrumentBar, ...]
    events: tuple[InstrumentEvent, ...]
    source_health: str
    authorization_snapshot_ids: tuple[str, ...]
    issues: tuple[str, ...]


class InstrumentObservationReader(Protocol):
    def open(self, symbol: str, *, as_of: datetime, limit: int) -> InstrumentObservation: ...


@dataclass(frozen=True)
class InstrumentIdentityView:
    symbol: str
    name: str
    asset_class: str = "us_equity"


@dataclass(frozen=True)
class InstrumentBarView:
    time: str
    open: str
    high: str
    low: str
    close: str
    volume: int
    evidence_id: str


@dataclass(frozen=True)
class CostReferenceView:
    availability: str
    value: str | None
    identity: str
    historical_position_track: bool


@dataclass(frozen=True)
class EventTrackView:
    track: TrackKind
    events: tuple[InstrumentEvent, ...]


@dataclass(frozen=True)
class EvidenceInspectorView:
    selected_date: str | None
    evidence_ids: tuple[str, ...]
    source_health: str
    authorization_snapshot_ids: tuple[str, ...]
    issues: tuple[str, ...]


@dataclass(frozen=True)
class FormalResearchOverlayView:
    research_eligible: bool
    label: str
    scale_identity: str
    events: tuple[InstrumentEvent, ...]


@dataclass(frozen=True)
class InstrumentWorkspace:
    identity: InstrumentIdentityView
    raw_bars: tuple[InstrumentBarView, ...]
    provider_adjusted_bars: tuple[InstrumentBarView, ...]
    cost_reference: CostReferenceView
    event_tracks: tuple[EventTrackView, ...]
    evidence_inspector: EvidenceInspectorView
    formal_research_overlay: FormalResearchOverlayView
    issues: tuple[str, ...]


class UnavailableInstrumentObservationReader:
    def open(self, symbol: str, *, as_of: datetime, limit: int) -> InstrumentObservation:
        return InstrumentObservation(
            symbol=symbol,
            name=symbol,
            raw_bars=(),
            provider_adjusted_bars=(),
            events=(),
            source_health="unavailable",
            authorization_snapshot_ids=(),
            issues=("provider_unavailable", "daily_bars_unavailable"),
        )


class TypedInstrumentObservationReader:
    """把 D1 typed market observation 和 D2 official events 投影为工作台模型。"""

    def __init__(
        self,
        *,
        market: Any,
        official_events: Callable[[str, datetime], tuple[Any, ...]],
        provider_wait_seconds: float = 1.8,
    ) -> None:
        if provider_wait_seconds <= 0:
            raise ValueError("provider_wait_seconds_must_be_positive")
        self._market = market
        self._official_events = official_events
        self._provider_wait_seconds = provider_wait_seconds

    def open(self, symbol: str, *, as_of: datetime, limit: int) -> InstrumentObservation:
        end_date = as_of.astimezone(timezone.utc).date()
        start_date = end_date - timedelta(days=max(limit * 2, 30))
        issues: list[str] = []
        authorization_ids: list[str] = []
        executor = ThreadPoolExecutor(max_workers=4)
        identity_future = None
        actions_future = None
        official_events_future = None
        deadline = monotonic() + self._provider_wait_seconds
        try:
            bars_future = executor.submit(
                self._market.observe_daily_bars,
                symbol,
                start_date=start_date,
                end_date=end_date,
                fetched_at=as_of,
                purpose="display",
            )
            _, unfinished = wait(
                (bars_future,), timeout=max(0.0, deadline - monotonic())
            )
            for future in unfinished:
                future.cancel()
            remaining = deadline - monotonic()
            if remaining > 0:
                identity_future = executor.submit(
                    self._market.observe_asset,
                    symbol,
                    purpose="display",
                    fetched_at=as_of,
                )
                actions_future = executor.submit(
                    self._market.observe_corporate_actions,
                    symbol,
                    start_date=start_date,
                    end_date=end_date,
                    fetched_at=as_of,
                    purpose="display",
                )
                official_events_future = executor.submit(
                    self._official_events, symbol, as_of
                )
                _, unfinished = wait(
                    (identity_future, actions_future, official_events_future),
                    timeout=max(0.0, deadline - monotonic()),
                )
                for future in unfinished:
                    future.cancel()
        finally:
            executor.shutdown(wait=False, cancel_futures=True)

        try:
            if identity_future is None or not identity_future.done():
                raise TimeoutError("provider_timeout")
            identity = identity_future.result()
            name = identity.value.name if identity.value is not None else symbol
            authorization_ids.append(identity.provenance.authorization_snapshot_id)
        except Exception:
            name = symbol
            issues.append("asset_identity_unavailable")
        try:
            if not bars_future.done():
                raise TimeoutError("provider_timeout")
            bars = bars_future.result()
            raw = _typed_bars(bars.raw.value or (), "raw", bars.raw.provenance.content_sha256)
            adjusted = _typed_bars(
                bars.provider_adjusted.value or (),
                "provider_adjusted",
                bars.provider_adjusted.provenance.content_sha256,
            )
            if (
                getattr(bars.provider_adjusted, "availability", "available")
                != "available"
            ):
                issues.append("provider_adjusted_bars_unavailable")
            authorization_ids.extend(
                (
                    bars.raw.provenance.authorization_snapshot_id,
                    bars.provider_adjusted.provenance.authorization_snapshot_id,
                )
            )
            source_health = bars.raw.source_health
        except Exception:
            raw = ()
            adjusted = ()
            source_health = "unavailable"
            issues.append("daily_bars_unavailable")
        events: list[InstrumentEvent] = []
        try:
            if actions_future is None or not actions_future.done():
                raise TimeoutError("provider_timeout")
            actions = actions_future.result()
            authorization_ids.append(actions.provenance.authorization_snapshot_id)
            for action in actions.value or ():
                identity_value = f"alpaca:corporate_action:{action.provider_record_id}"
                events.append(
                    InstrumentEvent(
                        event_id=identity_value,
                        track="corporate",
                        event_type=action.action_type,
                        label=action.action_type,
                        occurred_at=datetime.combine(
                            action.effective_date, datetime.min.time(), timezone.utc
                        ),
                        evidence_ids=(identity_value,),
                        confirmation_state="confirmed",
                    )
                )
        except Exception:
            issues.append("corporate_actions_unavailable")
        try:
            if official_events_future is None or not official_events_future.done():
                raise TimeoutError("provider_timeout")
            for event in official_events_future.result():
                authorization_ids.append(event.authorization.snapshot_id)
                events.append(
                    InstrumentEvent(
                        event_id=event.identity,
                        track="macro" if event.event_type == "macro_release" else "corporate",
                        event_type=event.event_type,
                        label=event.event_type,
                        occurred_at=event.occurred_at,
                        evidence_ids=(event.evidence_identity,),
                        confirmation_state="confirmed",
                    )
                )
        except Exception:
            issues.append("official_events_unavailable")
        return InstrumentObservation(
            symbol=symbol,
            name=name,
            raw_bars=raw[-limit:],
            provider_adjusted_bars=adjusted[-limit:],
            events=tuple(events),
            source_health=source_health,
            authorization_snapshot_ids=tuple(dict.fromkeys(authorization_ids)),
            issues=tuple(issues),
        )


class InstrumentWorkbench:
    def __init__(
        self,
        *,
        source: InstrumentObservationReader,
        cost_reader: Callable[[PersonalActor, str], Decimal | None],
        rule_attention_reader: Callable[[PersonalActor, str], tuple[InstrumentEvent, ...]],
        formal_overlay_reader: Callable[[str], tuple[InstrumentEvent, ...]],
    ) -> None:
        self._source = source
        self._cost_reader = cost_reader
        self._rule_attention_reader = rule_attention_reader
        self._formal_overlay_reader = formal_overlay_reader

    def open(self, actor: PersonalActor, query: InstrumentQuery) -> InstrumentWorkspace:
        symbol = _normalize_symbol(query.symbol)
        if query.as_of.tzinfo is None:
            raise ValueError("as_of_requires_timezone")
        limit = min(max(query.limit, 1), 1500)
        observed = self._source.open(symbol, as_of=query.as_of, limit=limit)
        personal_events = self._rule_attention_reader(actor, symbol)
        formal_events = self._formal_overlay_reader(symbol)
        all_events = (*observed.events, *personal_events)
        track_order: tuple[TrackKind, ...] = (
            "corporate",
            "macro",
            "data_gap",
            "personal_rule",
        )
        tracks = tuple(
            EventTrackView(
                track=track,
                events=tuple(
                    sorted(
                        (event for event in all_events if event.track == track),
                        key=lambda item: item.occurred_at,
                    )
                ),
            )
            for track in track_order
            if any(event.track == track for event in all_events)
        )
        selected = query.selected_date or (
            observed.raw_bars[-1].trade_date if observed.raw_bars else None
        )
        evidence_ids = tuple(
            dict.fromkeys(
                bar.evidence_id
                for bar in (*observed.raw_bars, *observed.provider_adjusted_bars)
                if selected is not None and bar.trade_date == selected
            )
        ) + tuple(
            dict.fromkeys(
                evidence_id
                for event in all_events
                if selected is not None and event.occurred_at.date() == selected
                for evidence_id in event.evidence_ids
            )
        )
        cost = self._cost_reader(actor, symbol)
        return InstrumentWorkspace(
            identity=InstrumentIdentityView(symbol=symbol, name=observed.name),
            raw_bars=tuple(_bar_view(bar) for bar in observed.raw_bars),
            provider_adjusted_bars=tuple(
                _bar_view(bar) for bar in observed.provider_adjusted_bars
            ),
            cost_reference=CostReferenceView(
                availability="available" if cost is not None else "not_available",
                value=_decimal(cost) if cost is not None else None,
                identity="current_manual_average_cost",
                historical_position_track=False,
            ),
            event_tracks=tracks,
            evidence_inspector=EvidenceInspectorView(
                selected_date=selected.isoformat() if selected else None,
                evidence_ids=tuple(dict.fromkeys(evidence_ids)),
                source_health=observed.source_health,
                authorization_snapshot_ids=observed.authorization_snapshot_ids,
                issues=observed.issues,
            ),
            formal_research_overlay=FormalResearchOverlayView(
                research_eligible=False,
                label="正式研究发布投影",
                scale_identity="normalized_readonly",
                events=formal_events,
            ),
            issues=observed.issues,
        )


def _bar_view(bar: InstrumentBar) -> InstrumentBarView:
    return InstrumentBarView(
        time=bar.trade_date.isoformat(),
        open=_decimal(bar.open),
        high=_decimal(bar.high),
        low=_decimal(bar.low),
        close=_decimal(bar.close),
        volume=bar.volume,
        evidence_id=bar.evidence_id,
    )


def _typed_bars(values: tuple[Any, ...], adjustment: str, content_sha256: str) -> tuple[InstrumentBar, ...]:
    return tuple(
        InstrumentBar(
            trade_date=bar.trade_date,
            open=bar.open,
            high=bar.high,
            low=bar.low,
            close=bar.close,
            volume=bar.volume,
            evidence_id=(
                f"alpaca:daily_bar:{bar.symbol}:{bar.trade_date.isoformat()}:{adjustment}:"
                f"{content_sha256[:12]}"
            ),
        )
        for bar in values
    )


def _decimal(value: Decimal) -> str:
    return format(value.quantize(Decimal("0.0001")), "f")


def _normalize_symbol(value: str) -> str:
    normalized = value.strip().upper()
    if not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,14}", normalized):
        raise ValueError("unsupported_instrument")
    return normalized
