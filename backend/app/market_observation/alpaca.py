from __future__ import annotations

from dataclasses import dataclass, field
from collections import deque
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
import re
import time
from threading import Lock
from typing import Any, Callable, Mapping, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .contracts import (
    AppendOnlyAuthorizationRegistry,
    AssetIdentity,
    AuthorizationDenied,
    AuthorizationPurpose,
    CorporateAction,
    DailyBar,
    DailyBarsObservation,
    DelayedPrice,
    ObservedValue,
    ProvenanceEnvelope,
)


_STOCK_BARS_PATH = re.compile(r"/v2/stocks/[A-Z][A-Z0-9.-]{0,14}/bars")
_STOCK_SNAPSHOT_PATH = re.compile(r"/v2/stocks/[A-Z][A-Z0-9.-]{0,14}/snapshot")
_ASSET_PATH = re.compile(r"/v2/assets(?:/[A-Z0-9.-]{1,40})?")
_CORPORATE_ACTION_TYPES = frozenset(
    {
        "forward_splits",
        "reverse_splits",
        "unit_splits",
        "stock_dividends",
        "cash_dividends",
        "spin_offs",
        "cash_mergers",
        "stock_mergers",
        "stock_and_cash_mergers",
        "redemptions",
        "name_changes",
        "worthless_removals",
        "rights_distributions",
    }
)


class ProviderRequestRejected(ValueError):
    """请求没有通过固定的 provider allowlist。"""


class MarketObservationError(RuntimeError):
    def __init__(
        self, code: str, *, retry_after_seconds: float | None = None
    ) -> None:
        super().__init__(code)
        self.code = code
        self.retry_after_seconds = retry_after_seconds


@dataclass(frozen=True)
class ProviderRequest:
    method: str
    url: str
    headers: dict[str, str] = field(default_factory=dict, repr=False)
    connect_timeout_seconds: float = 5.0
    total_timeout_seconds: float = 15.0


@dataclass(frozen=True)
class ProviderResponse:
    status_code: int
    headers: Mapping[str, str]
    body: Any
    final_url: str | None = None


class ProviderTransport(Protocol):
    def send(self, request: ProviderRequest) -> ProviderResponse: ...


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        del req, fp, code, msg, headers, newurl
        return None


class UrllibProviderTransport:
    """不自动跟随重定向的最小 HTTPS transport。"""

    def __init__(self, *, opener: Any | None = None) -> None:
        self._opener = opener or build_opener(_NoRedirectHandler())

    def send(self, request: ProviderRequest) -> ProviderResponse:
        outbound = Request(
            request.url,
            headers=dict(request.headers),
            method=request.method,
        )
        try:
            with self._opener.open(
                outbound, timeout=request.connect_timeout_seconds
            ) as response:
                return ProviderResponse(
                    status_code=int(response.status),
                    headers=dict(response.headers.items()),
                    body=_decode_json(response.read()),
                    final_url=response.geturl(),
                )
        except HTTPError as exc:
            return ProviderResponse(
                status_code=int(exc.code),
                headers=dict(exc.headers.items()),
                body=_decode_json(exc.read()),
                final_url=exc.geturl(),
            )
        except (TimeoutError, URLError) as exc:
            reason = getattr(exc, "reason", exc)
            if isinstance(reason, TimeoutError):
                raise TimeoutError("provider_timeout") from exc
            raise MarketObservationError("provider_unavailable") from exc


@dataclass(frozen=True)
class AlpacaCredentials:
    key_id: str = field(repr=False)
    secret_key: str = field(repr=False)


@dataclass(frozen=True)
class EodFallbackPrice:
    symbol: str
    price: str
    as_of: datetime
    identity: str


class AlpacaRequestPolicy:
    """只允许首期市场观察所需的 Alpaca GET 请求。"""

    def require_allowed(self, request: ProviderRequest) -> None:
        parsed = urlsplit(request.url)
        if (
            request.method != "GET"
            or parsed.scheme != "https"
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port is not None
            or parsed.fragment
        ):
            raise ProviderRequestRejected("request_target_denied")

        path = parsed.path
        if parsed.hostname == "data.alpaca.markets":
            allowed = (
                bool(_STOCK_BARS_PATH.fullmatch(path))
                or bool(_STOCK_SNAPSHOT_PATH.fullmatch(path))
                or path == "/v1/corporate-actions"
            )
        elif parsed.hostname == "paper-api.alpaca.markets":
            allowed = bool(_ASSET_PATH.fullmatch(path))
        else:
            allowed = False
        if not allowed:
            raise ProviderRequestRejected("request_target_denied")

    def require_redirect_allowed(self, source_url: str, location: str) -> str:
        target = urljoin(source_url, location)
        try:
            self.require_allowed(ProviderRequest(method="GET", url=target))
        except ProviderRequestRejected as exc:
            raise ProviderRequestRejected("redirect_target_denied") from exc
        if urlsplit(source_url).hostname != urlsplit(target).hostname:
            raise ProviderRequestRejected("redirect_target_denied")
        return target


class AlpacaMarketObservationAdapter:
    PLAN = "basic_delayed_sip_eod"

    def __init__(
        self,
        *,
        transport: ProviderTransport,
        authorizations: AppendOnlyAuthorizationRegistry,
        credentials: AlpacaCredentials,
        request_policy: AlpacaRequestPolicy | None = None,
        eod_fallback: Callable[[str], EodFallbackPrice | None] | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        request_deadline_seconds: float = 15.0,
    ) -> None:
        if request_deadline_seconds <= 0:
            raise ValueError("request_deadline_seconds_must_be_positive")
        self._transport = transport
        self._authorizations = authorizations
        self._credentials = credentials
        self._request_policy = request_policy or AlpacaRequestPolicy()
        self._eod_fallback = eod_fallback
        self._sleeper = sleeper
        self._monotonic = monotonic
        self._request_deadline_seconds = request_deadline_seconds
        self._request_times: deque[float] = deque()
        self._request_budget_lock = Lock()

    def observe_asset(
        self,
        symbol: str,
        *,
        purpose: AuthorizationPurpose = "display",
        fetched_at: datetime | None = None,
    ) -> ObservedValue[AssetIdentity]:
        normalized_symbol = _normalize_symbol(symbol)
        dataset = "alpaca_assets"
        authorization = self._require_authorization(dataset, purpose)
        url = f"https://paper-api.alpaca.markets/v2/assets/{quote(normalized_symbol)}"
        response = self._send(url)
        if response.status_code != 200 or not isinstance(response.body, Mapping):
            raise MarketObservationError("provider_schema_invalid")
        raw = response.body
        try:
            provider_symbol = _normalize_symbol(_required_text(raw, "symbol"))
            identity = AssetIdentity(
                provider_asset_id=_required_text(raw, "id"),
                symbol=provider_symbol,
                name=_required_text(raw, "name"),
                asset_class=_required_text(raw, "class"),
                exchange=_required_text(raw, "exchange"),
                status=_required_text(raw, "status"),
                tradable=_required_bool(raw, "tradable"),
                fractionable=_required_bool(raw, "fractionable"),
            )
        except (TypeError, ValueError) as exc:
            raise MarketObservationError("provider_schema_invalid") from exc
        if identity.symbol != normalized_symbol:
            raise MarketObservationError("provider_symbol_mismatch")
        observed_at = fetched_at or datetime.now(timezone.utc)
        provenance = ProvenanceEnvelope(
            source="alpaca",
            dataset=dataset,
            provider_record_id=identity.provider_asset_id,
            source_url=url,
            fetched_at=observed_at,
            content_sha256=_content_sha256(raw),
            authorization_snapshot_id=authorization.snapshot_id,
            qualification="online_observation",
            source_health="fresh",
            ai_context=authorization.ai_context,
            formal_research=authorization.formal_research,
        )
        return ObservedValue(
            availability="available",
            value=identity,
            reason_code=None,
            source_health="fresh",
            as_of=observed_at,
            provenance=provenance,
        )

    def observe_delayed_price(
        self,
        symbol: str,
        *,
        observed_at: datetime,
        purpose: AuthorizationPurpose = "display",
    ) -> ObservedValue[DelayedPrice]:
        if observed_at.tzinfo is None:
            raise ValueError("observed_at_requires_timezone")
        normalized_symbol = _normalize_symbol(symbol)
        dataset = "alpaca_delayed_sip_prices"
        authorization = self._require_authorization(dataset, purpose)
        cutoff = observed_at.astimezone(timezone.utc).replace(second=0, microsecond=0) - timedelta(
            minutes=15
        )
        query = urlencode({"feed": "delayed_sip"})
        url = f"https://data.alpaca.markets/v2/stocks/{quote(normalized_symbol)}/snapshot?{query}"
        try:
            response = self._send(url)
        except MarketObservationError as exc:
            if exc.code != "provider_timeout":
                raise
            return self._delayed_price_fallback(
                normalized_symbol,
                observed_at=observed_at,
                authorization_snapshot_id=authorization.snapshot_id,
                url=url,
            )
        if response.status_code != 200 or not isinstance(response.body, Mapping):
            raise MarketObservationError("provider_schema_invalid")
        raw = response.body
        try:
            provider_symbol = _normalize_symbol(_required_text(raw, "symbol"))
        except (TypeError, ValueError) as exc:
            raise MarketObservationError("provider_schema_invalid") from exc
        if provider_symbol != normalized_symbol:
            raise MarketObservationError("provider_symbol_mismatch")
        record = raw.get("minuteBar")
        feed = "delayed_sip"
        reason = None
        health: str = "fresh"
        if not isinstance(record, Mapping):
            record = raw.get("dailyBar")
            if not isinstance(record, Mapping):
                record = raw.get("prevDailyBar")
            feed = "eod"
            reason = "latest_close_fallback"
            health = "stale"
        if not isinstance(record, Mapping):
            provenance = ProvenanceEnvelope(
                source="alpaca",
                dataset=dataset,
                provider_record_id=None,
                source_url=url,
                fetched_at=observed_at,
                content_sha256=_content_sha256(raw),
                authorization_snapshot_id=authorization.snapshot_id,
                qualification="online_observation",
                source_health="unavailable",
                ai_context=authorization.ai_context,
                formal_research=authorization.formal_research,
                adjustment_policy="raw",
                missing_reason="price_unavailable",
            )
            return ObservedValue(
                availability="not_available",
                value=None,
                reason_code="price_unavailable",
                source_health="unavailable",
                as_of=None,
                provenance=provenance,
            )
        try:
            as_of = _required_datetime(record, "t")
            price_value = _required_decimal(record, "c")
        except (TypeError, ValueError) as exc:
            raise MarketObservationError("provider_schema_invalid") from exc
        delay_seconds = int((observed_at.astimezone(timezone.utc) - as_of).total_seconds())
        is_authorized_delay = feed == "eod" or as_of <= cutoff
        if not is_authorized_delay:
            health = "degraded"
            reason = "sip_delay_not_proven"
        provenance = ProvenanceEnvelope(
            source="alpaca",
            dataset=dataset,
            provider_record_id=f"{normalized_symbol}:{_rfc3339(as_of)}",
            source_url=url,
            fetched_at=observed_at,
            content_sha256=_content_sha256(record),
            authorization_snapshot_id=authorization.snapshot_id,
            qualification="online_observation",
            source_health=health,
            ai_context=authorization.ai_context,
            formal_research=authorization.formal_research,
            adjustment_policy="raw",
            missing_reason=reason,
        )
        return ObservedValue(
            availability="available" if is_authorized_delay else "not_available",
            value=(
                DelayedPrice(
                    symbol=normalized_symbol,
                    price=price_value,
                    currency="USD",
                    feed=feed,
                    delay_seconds=delay_seconds,
                )
                if is_authorized_delay
                else None
            ),
            reason_code=reason,
            source_health=health,
            as_of=as_of,
            provenance=provenance,
        )

    def observe_daily_bars(
        self,
        symbol: str,
        *,
        start_date: date,
        end_date: date,
        fetched_at: datetime,
        purpose: AuthorizationPurpose = "display",
    ) -> DailyBarsObservation:
        if start_date > end_date:
            raise ValueError("invalid_date_range")
        if fetched_at.tzinfo is None:
            raise ValueError("fetched_at_requires_timezone")
        normalized_symbol = _normalize_symbol(symbol)
        dataset = "alpaca_daily_bars"
        authorization = self._require_authorization(dataset, purpose)
        observations: dict[str, ObservedValue[tuple[DailyBar, ...]]] = {}
        deadline = self._monotonic() + self._request_deadline_seconds
        for adjustment in ("raw", "all"):
            query = urlencode(
                {
                    "timeframe": "1Day",
                    "start": start_date.isoformat(),
                    "end": end_date.isoformat(),
                    "limit": "10000",
                    "adjustment": adjustment,
                    "feed": "iex",
                    "sort": "asc",
                }
            )
            url = f"https://data.alpaca.markets/v2/stocks/{quote(normalized_symbol)}/bars?{query}"
            response = self._send(url, deadline=deadline)
            if response.status_code != 200 or not isinstance(response.body, Mapping):
                raise MarketObservationError("provider_schema_invalid")
            raw = response.body
            if raw.get("next_page_token") not in (None, ""):
                raise MarketObservationError("provider_pagination_incomplete")
            records = raw.get("bars")
            if not isinstance(records, list) or not records:
                raise MarketObservationError("provider_schema_invalid")
            try:
                bars = tuple(
                    _normalize_daily_bar(normalized_symbol, record)
                    for record in records
                    if isinstance(record, Mapping)
                )
            except (TypeError, ValueError) as exc:
                raise MarketObservationError("provider_schema_invalid") from exc
            if len(bars) != len(records):
                raise MarketObservationError("provider_schema_invalid")
            provenance = ProvenanceEnvelope(
                source="alpaca",
                dataset=dataset,
                provider_record_id=f"{normalized_symbol}:{start_date}:{end_date}:{adjustment}",
                source_url=url,
                fetched_at=fetched_at,
                content_sha256=_content_sha256(records),
                authorization_snapshot_id=authorization.snapshot_id,
                qualification="traceable_history",
                source_health="fresh",
                ai_context=authorization.ai_context,
                formal_research=authorization.formal_research,
                adjustment_policy=adjustment,
            )
            observations[adjustment] = ObservedValue(
                availability="available",
                value=bars,
                reason_code=None,
                source_health="fresh",
                as_of=max(_required_datetime(record, "t") for record in records),
                provenance=provenance,
            )
        return DailyBarsObservation(
            raw=observations["raw"],
            provider_adjusted=observations["all"],
        )

    def observe_corporate_actions(
        self,
        symbol: str,
        *,
        start_date: date,
        end_date: date,
        fetched_at: datetime,
        purpose: AuthorizationPurpose = "display",
    ) -> ObservedValue[tuple[CorporateAction, ...]]:
        if start_date > end_date:
            raise ValueError("invalid_date_range")
        if fetched_at.tzinfo is None:
            raise ValueError("fetched_at_requires_timezone")
        normalized_symbol = _normalize_symbol(symbol)
        dataset = "alpaca_corporate_actions"
        authorization = self._require_authorization(dataset, purpose)
        query = urlencode(
            {
                "symbols": normalized_symbol,
                "start": start_date.isoformat(),
                "end": end_date.isoformat(),
                "limit": "1000",
                "sort": "asc",
            }
        )
        url = f"https://data.alpaca.markets/v1/corporate-actions?{query}"
        response = self._send(url)
        if response.status_code != 200 or not isinstance(response.body, Mapping):
            raise MarketObservationError("provider_schema_invalid")
        raw = response.body
        if raw.get("next_page_token") not in (None, ""):
            raise MarketObservationError("provider_pagination_incomplete")
        grouped = raw.get("corporate_actions")
        if not isinstance(grouped, Mapping):
            raise MarketObservationError("provider_schema_invalid")
        unknown_nonempty = [
            key
            for key, records in grouped.items()
            if key not in _CORPORATE_ACTION_TYPES and records
        ]
        if unknown_nonempty:
            raise MarketObservationError("provider_schema_invalid")
        try:
            actions = tuple(
                sorted(
                    (
                        _normalize_corporate_action(action_type, record)
                        for action_type, records in grouped.items()
                        if action_type in _CORPORATE_ACTION_TYPES
                        for record in _required_record_list(records)
                    ),
                    key=lambda action: (
                        action.process_date,
                        action.action_type,
                        action.provider_record_id,
                    ),
                )
            )
        except (TypeError, ValueError) as exc:
            raise MarketObservationError("provider_schema_invalid") from exc
        if any(action.symbol != normalized_symbol for action in actions):
            raise MarketObservationError("provider_symbol_mismatch")
        provenance = ProvenanceEnvelope(
            source="alpaca",
            dataset=dataset,
            provider_record_id=None,
            source_url=url,
            fetched_at=fetched_at,
            content_sha256=_content_sha256(grouped),
            authorization_snapshot_id=authorization.snapshot_id,
            qualification="traceable_history",
            source_health="fresh",
            ai_context=authorization.ai_context,
            formal_research=authorization.formal_research,
            adjustment_policy="provider_reported_unverified",
        )
        return ObservedValue(
            availability="available",
            value=actions,
            reason_code=None,
            source_health="fresh",
            as_of=fetched_at,
            provenance=provenance,
        )

    def _send(self, url: str, *, deadline: float | None = None) -> ProviderResponse:
        if deadline is None:
            deadline = self._monotonic() + self._request_deadline_seconds
        budget_after_waits = self._request_deadline_seconds
        for attempt in range(1, 4):
            remaining = min(deadline - self._monotonic(), budget_after_waits)
            if remaining <= 0:
                raise MarketObservationError("provider_timeout")
            request = ProviderRequest(
                method="GET",
                url=url,
                headers={
                    "APCA-API-KEY-ID": self._credentials.key_id,
                    "APCA-API-SECRET-KEY": self._credentials.secret_key,
                },
                connect_timeout_seconds=min(5.0, remaining),
                total_timeout_seconds=remaining,
            )
            self._request_policy.require_allowed(request)
            self._acquire_local_request_budget()
            try:
                response = self._transport.send(request)
            except TimeoutError as exc:
                raise MarketObservationError("provider_timeout") from exc
            if response.final_url and response.final_url != request.url:
                self._request_policy.require_redirect_allowed(request.url, response.final_url)
            if 300 <= response.status_code < 400:
                location = _header(response.headers, "Location")
                if location:
                    self._request_policy.require_redirect_allowed(request.url, location)
                raise MarketObservationError("provider_redirect_not_supported")
            if response.status_code == 429:
                retry_after = _retry_after_seconds(response.headers)
                if attempt == 3 or retry_after >= remaining:
                    raise MarketObservationError(
                        "provider_rate_limited",
                        retry_after_seconds=retry_after,
                    )
                self._sleeper(retry_after)
                budget_after_waits -= retry_after
                continue
            if response.status_code in {403, 422}:
                raise MarketObservationError("entitlement_denied")
            if response.status_code == 401:
                raise MarketObservationError("provider_auth_failed")
            if response.status_code >= 400:
                raise MarketObservationError("provider_unavailable")
            return response
        raise AssertionError("unreachable")

    def _acquire_local_request_budget(self) -> None:
        with self._request_budget_lock:
            now = self._monotonic()
            while self._request_times and self._request_times[0] <= now - 60.0:
                self._request_times.popleft()
            if len(self._request_times) >= 120:
                retry_after = max(0.0, 60.0 - (now - self._request_times[0]))
                raise MarketObservationError(
                    "provider_rate_limited", retry_after_seconds=retry_after
                )
            self._request_times.append(now)

    def _require_authorization(
        self, dataset: str, purpose: AuthorizationPurpose
    ):
        snapshot = self._authorizations.require(
            "alpaca", dataset, self.PLAN, purpose
        )
        if snapshot.ai_context or snapshot.formal_research or snapshot.redistribute:
            raise AuthorizationDenied("authorization_policy_invalid")
        return snapshot

    def _delayed_price_fallback(
        self,
        symbol: str,
        *,
        observed_at: datetime,
        authorization_snapshot_id: str,
        url: str,
    ) -> ObservedValue[DelayedPrice]:
        fallback = self._eod_fallback(symbol) if self._eod_fallback else None
        if fallback is None:
            provenance = ProvenanceEnvelope(
                source="alpaca",
                dataset="alpaca_delayed_sip_prices",
                provider_record_id=None,
                source_url=url,
                fetched_at=observed_at,
                content_sha256=_content_sha256({"reason": "provider_timeout"}),
                authorization_snapshot_id=authorization_snapshot_id,
                qualification="online_observation",
                source_health="unavailable",
                ai_context=False,
                formal_research=False,
                adjustment_policy="raw",
                missing_reason="provider_timeout",
            )
            return ObservedValue(
                availability="not_available",
                value=None,
                reason_code="provider_timeout",
                source_health="unavailable",
                as_of=None,
                provenance=provenance,
            )
        try:
            fallback_symbol = _normalize_symbol(fallback.symbol)
        except (AttributeError, ValueError) as exc:
            raise MarketObservationError("fallback_symbol_mismatch") from exc
        if fallback_symbol != symbol:
            raise MarketObservationError("fallback_symbol_mismatch")
        if not isinstance(fallback.identity, str) or not fallback.identity.strip():
            raise MarketObservationError("fallback_identity_missing")
        fallback_identity = fallback.identity.strip()
        if fallback.as_of.tzinfo is None:
            raise ValueError("fallback_as_of_requires_timezone")
        price = _decimal_value(fallback.price, "fallback_price")
        as_of = fallback.as_of.astimezone(timezone.utc)
        provenance = ProvenanceEnvelope(
            source="alpaca",
            dataset="alpaca_delayed_sip_prices",
            provider_record_id=None,
            source_url=url,
            fetched_at=observed_at,
            content_sha256=_content_sha256(
                {"price": str(price), "as_of": _rfc3339(as_of)}
            ),
            authorization_snapshot_id=authorization_snapshot_id,
            qualification="traceable_history",
            source_health="stale",
            ai_context=False,
            formal_research=False,
            adjustment_policy="raw",
            fallback_identity=fallback_identity,
            missing_reason="provider_timeout_eod_fallback",
        )
        return ObservedValue(
            availability="available",
            value=DelayedPrice(
                symbol=symbol,
                price=price,
                currency="USD",
                feed="eod",
                delay_seconds=max(
                    0, int((observed_at.astimezone(timezone.utc) - as_of).total_seconds())
                ),
            ),
            reason_code="provider_timeout_eod_fallback",
            source_health="stale",
            as_of=as_of,
            provenance=provenance,
        )


def _normalize_symbol(value: str) -> str:
    normalized = value.strip().upper()
    if not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,14}", normalized):
        raise ValueError("unsupported_instrument")
    return normalized


def _required_text(raw: Mapping[str, Any], field_name: str) -> str:
    value = raw.get(field_name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(field_name)
    return value.strip()


def _required_bool(raw: Mapping[str, Any], field_name: str) -> bool:
    value = raw.get(field_name)
    if not isinstance(value, bool):
        raise ValueError(field_name)
    return value


def _required_decimal(raw: Mapping[str, Any], field_name: str) -> Decimal:
    value = raw.get(field_name)
    return _decimal_value(value, field_name)


def _decimal_value(value: Any, field_name: str) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise ValueError(field_name)
    try:
        normalized = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(field_name) from exc
    if not normalized.is_finite():
        raise ValueError(field_name)
    return normalized


def _required_datetime(raw: Mapping[str, Any], field_name: str) -> datetime:
    value = _required_text(raw, field_name)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(field_name) from exc
    if parsed.tzinfo is None:
        raise ValueError(field_name)
    return parsed.astimezone(timezone.utc)


def _rfc3339(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _normalize_daily_bar(symbol: str, raw: Mapping[str, Any]) -> DailyBar:
    volume = raw.get("v")
    if isinstance(volume, bool) or not isinstance(volume, int) or volume < 0:
        raise ValueError("v")
    return DailyBar(
        symbol=symbol,
        trade_date=_required_datetime(raw, "t").date(),
        open=_required_decimal(raw, "o"),
        high=_required_decimal(raw, "h"),
        low=_required_decimal(raw, "l"),
        close=_required_decimal(raw, "c"),
        volume=volume,
    )


def _required_record_list(value: Any) -> list[Mapping[str, Any]]:
    if not isinstance(value, list) or any(not isinstance(item, Mapping) for item in value):
        raise ValueError("corporate_action_records")
    return value


def _normalize_corporate_action(
    action_type: str, raw: Mapping[str, Any]
) -> CorporateAction:
    process_date = _required_date(raw, "process_date")
    effective_date = next(
        (
            _optional_date(raw, field_name)
            for field_name in ("ex_date", "effective_date", "payable_date")
            if raw.get(field_name) is not None
        ),
        process_date,
    )
    symbol_field = {
        "unit_splits": "old_symbol",
        "spin_offs": "source_symbol",
        "cash_mergers": "acquiree_symbol",
        "stock_mergers": "acquiree_symbol",
        "stock_and_cash_mergers": "acquiree_symbol",
        "name_changes": "old_symbol",
        "rights_distributions": "source_symbol",
    }.get(action_type, "symbol")
    cash_field = {
        "cash_dividends": "rate",
        "cash_mergers": "rate",
        "stock_and_cash_mergers": "cash_rate",
        "redemptions": "rate",
    }.get(action_type)
    ratio_fields = {
        "forward_splits": ("new_rate", "old_rate"),
        "reverse_splits": ("new_rate", "old_rate"),
        "unit_splits": ("new_rate", "old_rate"),
        "stock_dividends": ("rate", None),
        "spin_offs": ("new_rate", "source_rate"),
        "stock_mergers": ("acquirer_rate", "acquiree_rate"),
        "stock_and_cash_mergers": ("acquirer_rate", "acquiree_rate"),
        "rights_distributions": ("rate", None),
    }.get(action_type)
    ratio_numerator = _required_decimal(raw, ratio_fields[0]) if ratio_fields else None
    ratio_denominator = (
        _required_decimal(raw, ratio_fields[1])
        if ratio_fields and ratio_fields[1]
        else Decimal("1") if ratio_fields else None
    )
    return CorporateAction(
        provider_record_id=_required_text(raw, "id"),
        action_type=action_type[:-1],
        symbol=_normalize_symbol(_required_text(raw, symbol_field)),
        process_date=process_date,
        effective_date=effective_date,
        cash_amount=_required_decimal(raw, cash_field) if cash_field else None,
        currency="USD" if cash_field else None,
        ratio_numerator=ratio_numerator,
        ratio_denominator=ratio_denominator,
    )


def _required_date(raw: Mapping[str, Any], field_name: str) -> date:
    value = _required_text(raw, field_name)
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(field_name) from exc


def _optional_date(raw: Mapping[str, Any], field_name: str) -> date:
    return _required_date(raw, field_name)


def _header(headers: Mapping[str, str], name: str) -> str | None:
    lowered = name.lower()
    for key, value in headers.items():
        if key.lower() == lowered:
            return value
    return None


def _retry_after_seconds(headers: Mapping[str, str]) -> float:
    raw = _header(headers, "Retry-After")
    if raw is None:
        return 1.0
    try:
        value = float(raw)
    except ValueError:
        return 1.0
    if value < 0 or value > 15:
        return 15.0
    return value


def _content_sha256(value: Any) -> str:
    payload = json.dumps(
        _canonical_json_value(value),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return sha256(payload.encode("utf-8")).hexdigest()


def _canonical_json_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _canonical_json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonical_json_value(item) for item in value]
    return value


def _decode_json(payload: bytes) -> Any:
    if not payload:
        return None
    try:
        return json.loads(payload.decode("utf-8"), parse_float=Decimal)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
