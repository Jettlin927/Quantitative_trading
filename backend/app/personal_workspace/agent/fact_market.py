"""Alpaca 市场观察到持久工具证据的 typed fact service。"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from hashlib import sha256
import json
from typing import Any, Mapping

from backend.app.market_observation.alpaca import AlpacaMarketObservationAdapter
from backend.app.market_observation.contracts import ObservedValue, ProvenanceEnvelope

from .evidence import (
    EvidenceLedger,
    EvidenceLedgerError,
    EvidenceReadContext,
    EvidenceRecord,
    Persistence,
)


MARKET_FACT_TTL = timedelta(hours=2)
MARKET_EVIDENCE_PURPOSE_POLICY_REVISION = "market-evidence-purpose-v2"
MARKET_EVIDENCE_ALLOWED_PURPOSES = frozenset({"domain_tool", "mcp_stdio"})
MARKET_EVIDENCE_PURPOSE_POLICY_HISTORY = {
    "market-evidence-purpose-v1": frozenset({"domain_tool"}),
    MARKET_EVIDENCE_PURPOSE_POLICY_REVISION: MARKET_EVIDENCE_ALLOWED_PURPOSES,
}


@dataclass(frozen=True)
class MarketDossierFact:
    data: Mapping[str, Any]
    records: tuple[EvidenceRecord, ...]
    gaps: tuple[str, ...]
    source_health: str


class MarketFactUnavailable(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


class MarketFactService:
    """授权先于 I/O，并只信任 adapter provenance 与装配期 retention。"""

    def __init__(
        self,
        *,
        adapter: AlpacaMarketObservationAdapter | None,
        evidence_ledger: EvidenceLedger,
        retention_by_authorization: Mapping[tuple[str, str], Persistence],
    ) -> None:
        self._adapter = adapter
        self._evidence_ledger = evidence_ledger
        self._retention_by_authorization = dict(retention_by_authorization)

    def read_dossier(
        self,
        *,
        context: EvidenceReadContext,
        symbol: str,
        bar_days: int,
        bar_limit: int,
    ) -> MarketDossierFact:
        self._authorize(context)
        now = context.now
        identity = self._adapter.observe_asset(
            symbol, purpose="ai_context", fetched_at=now
        )
        selected, bars_payload, gaps, adjustment = self._observe_bars(
            symbol=symbol,
            now=now,
            bar_days=bar_days,
        )
        if identity.availability != "available" or identity.value is None:
            raise MarketFactUnavailable(
                identity.reason_code or "asset_identity_unavailable"
            )
        self._require_provenance(identity, dataset="alpaca_assets")
        normalized_symbol = identity.value.symbol
        if normalized_symbol != symbol:
            raise ValueError("provider_symbol_mismatch")

        asset_payload = {
            "symbol": normalized_symbol,
            "name": identity.value.name,
            "asset_class": identity.value.asset_class,
        }
        asset_record = self._put(
            context=context,
            provenance=identity.provenance,
            logical_identity=(
                f"alpaca_assets:{normalized_symbol}:"
                f"{identity.value.provider_asset_id}"
            ),
            payload=asset_payload,
            observed_at=identity.as_of,
        )
        bars_record = self._put(
            context=context,
            provenance=selected.provenance,
            logical_identity=_bars_logical_identity(
                symbol=normalized_symbol,
                adjustment=adjustment,
                observation=selected,
            ),
            payload=bars_payload,
            observed_at=selected.as_of,
        )
        projected_bars = _project_bars(bars_payload, bar_limit=bar_limit)
        return MarketDossierFact(
            data={
                **asset_payload,
                **projected_bars,
                "authorization_snapshot_ids": sorted(
                    {
                        identity.provenance.authorization_snapshot_id,
                        selected.provenance.authorization_snapshot_id,
                    }
                ),
            },
            records=(asset_record, bars_record),
            gaps=gaps,
            source_health=selected.source_health,
        )

    def read_bars(
        self,
        *,
        context: EvidenceReadContext,
        symbol: str,
        bar_days: int,
        bar_limit: int,
    ) -> MarketDossierFact:
        """读取 legacy K 线所需的 bars；不依赖资产主数据。"""
        self._authorize(context)
        selected, payload, gaps, adjustment = self._observe_bars(
            symbol=symbol,
            now=context.now,
            bar_days=bar_days,
        )
        record = self._put(
            context=context,
            provenance=selected.provenance,
            logical_identity=_bars_logical_identity(
                symbol=symbol,
                adjustment=adjustment,
                observation=selected,
            ),
            payload=payload,
            observed_at=selected.as_of,
        )
        return MarketDossierFact(
            data={
                **_project_bars(payload, bar_limit=bar_limit),
                "authorization_snapshot_ids": [
                    selected.provenance.authorization_snapshot_id
                ],
            },
            records=(record,),
            gaps=gaps,
            source_health=selected.source_health,
        )

    def _authorize(self, context: EvidenceReadContext) -> None:
        if (
            context.purpose not in MARKET_EVIDENCE_ALLOWED_PURPOSES
            or "market:read" not in context.permissions
        ):
            raise PermissionError("source_unauthorized")
        if self._adapter is None:
            raise RuntimeError("market_dossier_unavailable")

    def _observe_bars(
        self,
        *,
        symbol: str,
        now: datetime,
        bar_days: int,
    ) -> tuple[ObservedValue[Any], dict[str, Any], tuple[str, ...], str]:
        bars = self._adapter.observe_daily_bars(
            symbol,
            start_date=now.date() - timedelta(days=bar_days),
            end_date=now.date(),
            fetched_at=now,
            purpose="ai_context",
        )
        selected = (
            bars.provider_adjusted
            if bars.provider_adjusted.availability == "available"
            else bars.raw
        )
        if selected.availability != "available" or not selected.value:
            raise MarketFactUnavailable(
                selected.reason_code or "daily_bars_unavailable"
            )
        self._require_provenance(selected, dataset="alpaca_daily_bars")
        if any(bar.symbol != symbol for bar in selected.value):
            raise ValueError("provider_symbol_mismatch")
        adjustment = (
            "provider_adjusted"
            if selected is bars.provider_adjusted
            else "raw"
        )
        payload = {
            "symbol": symbol,
            "adjustment": adjustment,
            "as_of": selected.as_of.isoformat() if selected.as_of else None,
            "source_health": selected.source_health,
            "bars": [
                {
                    "date": bar.trade_date.isoformat(),
                    "open": str(bar.open),
                    "high": str(bar.high),
                    "low": str(bar.low),
                    "close": str(bar.close),
                    "volume": bar.volume,
                }
                for bar in selected.value
            ],
            "count": len(selected.value),
        }
        gaps = ()
        if (
            bars.provider_adjusted.availability != "available"
            and selected is bars.raw
        ):
            gaps = (
                bars.provider_adjusted.reason_code
                or "provider_adjusted_unavailable",
            )
        return selected, payload, gaps, adjustment

    def _put(
        self,
        *,
        context: EvidenceReadContext,
        provenance: ProvenanceEnvelope,
        logical_identity: str,
        payload: Mapping[str, Any],
        observed_at: datetime | None,
    ) -> EvidenceRecord:
        persistence = self._retention_by_authorization.get(
            (provenance.source, provenance.authorization_snapshot_id)
        )
        if persistence is None:
            raise EvidenceLedgerError("source_retention_unknown")
        content_sha256 = _payload_sha256(payload)
        authorized_logical_identity = (
            f"{provenance.authorization_snapshot_id}:"
            f"{MARKET_EVIDENCE_PURPOSE_POLICY_REVISION}:{logical_identity}"
        )
        identity_sha256 = sha256(
            authorized_logical_identity.encode("utf-8")
        ).hexdigest()
        base_id = (
            f"market:{provenance.dataset}:"
            f"{identity_sha256[:12]}:"
            f"{content_sha256[:24]}"
        )
        record = self._record(
            context=context,
            provenance=provenance,
            logical_identity=authorized_logical_identity,
            evidence_id=base_id,
            content_sha256=content_sha256,
            persistence=persistence,
            payload=payload,
            observed_at=observed_at,
        )
        stored = self._evidence_ledger.put(context, record)
        if stored.expires_at is None or context.now < stored.expires_at:
            return stored
        revalidation = sha256(
            (
                f"{provenance.authorization_snapshot_id}|"
                f"{MARKET_EVIDENCE_PURPOSE_POLICY_REVISION}|"
                f"{provenance.content_sha256}|"
                f"{content_sha256}|{provenance.fetched_at.isoformat()}"
            ).encode("utf-8")
        ).hexdigest()
        return self._evidence_ledger.put(
            context,
            replace(record, evidence_id=f"{base_id}:{revalidation[:24]}"),
        )

    @staticmethod
    def _record(
        *,
        context: EvidenceReadContext,
        provenance: ProvenanceEnvelope,
        logical_identity: str,
        evidence_id: str,
        content_sha256: str,
        persistence: Persistence,
        payload: Mapping[str, Any],
        observed_at: datetime | None,
    ) -> EvidenceRecord:
        return EvidenceRecord(
            evidence_id=evidence_id,
            logical_identity=logical_identity,
            scope="actor",
            source=provenance.source,
            content_sha256=content_sha256,
            authorized_fields=tuple(payload),
            required_permissions=frozenset({"market:read"}),
            allowed_purposes=MARKET_EVIDENCE_ALLOWED_PURPOSES,
            authorization_snapshot_id=provenance.authorization_snapshot_id,
            observed_at=observed_at,
            published_at=None,
            effective_at=None,
            available_from=provenance.fetched_at,
            fetched_at=provenance.fetched_at,
            verified_at=context.now,
            expires_at=provenance.fetched_at + MARKET_FACT_TTL,
            persistence=persistence,
            payload=dict(payload),
        )

    @staticmethod
    def _require_provenance(
        observation: ObservedValue[Any], *, dataset: str
    ) -> None:
        provenance = observation.provenance
        if (
            provenance.source != "alpaca"
            or provenance.dataset != dataset
            or not provenance.authorization_snapshot_id
            or not provenance.ai_context
            or provenance.formal_research
        ):
            raise PermissionError("source_unauthorized")


def _payload_sha256(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def _bars_logical_identity(
    *, symbol: str, adjustment: str, observation: ObservedValue[Any]
) -> str:
    return (
        f"alpaca_daily_bars:{symbol}:{adjustment}:"
        f"{observation.provenance.content_sha256}"
    )


def _project_bars(
    payload: Mapping[str, Any], *, bar_limit: int
) -> dict[str, Any]:
    bars = list(payload["bars"])[-bar_limit:]
    return {**payload, "bars": bars, "count": len(bars)}
