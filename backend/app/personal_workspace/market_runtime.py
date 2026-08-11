from __future__ import annotations

from dataclasses import dataclass, field, fields
from datetime import datetime
import json
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Mapping

from backend.app.market_observation.alpaca import (
    AlpacaCredentials,
    AlpacaMarketObservationAdapter,
    ProviderTransport,
    UrllibProviderTransport,
)
from backend.app.market_observation.contracts import (
    AppendOnlyAuthorizationRegistry,
    SourceAuthorizationSnapshot,
)

from .instrument import (
    InstrumentObservationReader,
    TypedInstrumentObservationReader,
    UnavailableInstrumentObservationReader,
)
from .portfolio import (
    AlpacaPortfolioMarketReader,
    PortfolioMarketReader,
    UnavailablePortfolioMarketReader,
)


_ALPACA_PLAN = "basic_delayed_sip_eod"
_ALPACA_DATASETS = frozenset(
    {
        "alpaca_assets",
        "alpaca_delayed_sip_prices",
        "alpaca_daily_bars",
        "alpaca_corporate_actions",
    }
)
_SNAPSHOT_FIELDS = frozenset(field.name for field in fields(SourceAuthorizationSnapshot))
_BOOLEAN_FIELDS = frozenset(
    {
        "display",
        "internal_analysis",
        "ai_context",
        "persist",
        "backfill",
        "redistribute",
        "formal_research",
    }
)


@dataclass(frozen=True)
class PersonalMarketReaders:
    portfolio: PortfolioMarketReader
    instrument: InstrumentObservationReader
    market: AlpacaMarketObservationAdapter | None = None
    evidence_retention_by_authorization: Mapping[
        tuple[str, str], str
    ] = field(default_factory=lambda: MappingProxyType({}))

    @classmethod
    def unavailable(
        cls,
        *,
        evidence_retention_by_authorization: Mapping[
            tuple[str, str], str
        ]
        | None = None,
    ) -> "PersonalMarketReaders":
        return cls(
            portfolio=UnavailablePortfolioMarketReader(),
            instrument=UnavailableInstrumentObservationReader(),
            market=None,
            evidence_retention_by_authorization=(
                evidence_retention_by_authorization or MappingProxyType({})
            ),
        )


def load_personal_market_readers(
    *,
    credentials_file: str | Path,
    authorization_file: str | Path,
    transport: ProviderTransport | None = None,
) -> PersonalMarketReaders:
    """从只读文件装配个人工作台行情；任何配置异常都整体 fail closed。"""

    return _load_personal_market_readers(
        credentials_file=credentials_file,
        authorization_file=authorization_file,
        transport=transport,
        mapping_reader=_read_mapping,
    )


def load_owner_only_personal_market_readers(
    *,
    credentials_file: str | Path,
    authorization_file: str | Path,
    transport: ProviderTransport | None = None,
) -> PersonalMarketReaders:
    """只从 owner-only 有界文件装配行情；任何异常都整体 fail closed。"""

    return _load_personal_market_readers(
        credentials_file=credentials_file,
        authorization_file=authorization_file,
        transport=transport,
        mapping_reader=_read_owner_only_mapping,
    )


def _load_personal_market_readers(
    *,
    credentials_file: str | Path,
    authorization_file: str | Path,
    transport: ProviderTransport | None,
    mapping_reader: Callable[[str | Path], Mapping[str, Any]],
) -> PersonalMarketReaders:
    try:
        authorizations = _load_authorizations(authorization_file, mapping_reader)
    except (
        OSError,
        RuntimeError,
        json.JSONDecodeError,
        KeyError,
        TypeError,
        ValueError,
    ):
        return PersonalMarketReaders.unavailable()
    retention_by_authorization = MappingProxyType(
        {
            (snapshot.source, snapshot.snapshot_id): _evidence_persistence(snapshot)
            for snapshot in authorizations.snapshots
        }
    )
    try:
        credentials = _load_credentials(credentials_file, mapping_reader)
    except (
        OSError,
        RuntimeError,
        json.JSONDecodeError,
        KeyError,
        TypeError,
        ValueError,
    ):
        return PersonalMarketReaders.unavailable(
            evidence_retention_by_authorization=retention_by_authorization
        )

    adapter = AlpacaMarketObservationAdapter(
        transport=transport or UrllibProviderTransport(),
        authorizations=authorizations,
        credentials=credentials,
        request_deadline_seconds=3.2,
    )
    return PersonalMarketReaders(
        portfolio=AlpacaPortfolioMarketReader(adapter=adapter),
        instrument=TypedInstrumentObservationReader(
            market=adapter,
            official_events=None,
            provider_wait_seconds=4.5,
        ),
        market=adapter,
        evidence_retention_by_authorization=retention_by_authorization,
    )


def _load_credentials(
    path: str | Path,
    mapping_reader: Callable[[str | Path], Mapping[str, Any]],
) -> AlpacaCredentials:
    payload = mapping_reader(path)
    if set(payload) != {"key_id", "secret_key"}:
        raise ValueError("alpaca_credentials_schema_invalid")
    key_id = _required_clean_text(payload, "key_id")
    secret_key = _required_clean_text(payload, "secret_key")
    return AlpacaCredentials(key_id=key_id, secret_key=secret_key)


def _load_authorizations(
    path: str | Path,
    mapping_reader: Callable[[str | Path], Mapping[str, Any]],
) -> AppendOnlyAuthorizationRegistry:
    payload = mapping_reader(path)
    if set(payload) != {"feed", "delay_seconds", "snapshots"}:
        raise ValueError("alpaca_authorization_schema_invalid")
    if payload["feed"] != "sip" or payload["delay_seconds"] != 900:
        raise ValueError("alpaca_authorization_market_scope_invalid")
    raw_snapshots = payload["snapshots"]
    if not isinstance(raw_snapshots, list) or len(raw_snapshots) < len(
        _ALPACA_DATASETS
    ):
        raise ValueError("alpaca_authorization_datasets_invalid")

    registry = AppendOnlyAuthorizationRegistry()
    datasets: set[str] = set()
    for raw_snapshot in raw_snapshots:
        if (
            not isinstance(raw_snapshot, Mapping)
            or set(raw_snapshot) != _SNAPSHOT_FIELDS
        ):
            raise ValueError("alpaca_authorization_snapshot_schema_invalid")
        if any(type(raw_snapshot[field]) is not bool for field in _BOOLEAN_FIELDS):
            raise ValueError("alpaca_authorization_purpose_invalid")
        if not (
            raw_snapshot["display"]
            and raw_snapshot["internal_analysis"]
            and raw_snapshot["persist"]
            and not raw_snapshot["backfill"]
            and not raw_snapshot["redistribute"]
            and not raw_snapshot["formal_research"]
        ):
            raise ValueError("alpaca_authorization_purpose_invalid")
        if raw_snapshot["source"] != "alpaca" or raw_snapshot["plan"] != _ALPACA_PLAN:
            raise ValueError("alpaca_authorization_identity_invalid")
        dataset = _required_clean_text(raw_snapshot, "dataset")
        if dataset not in _ALPACA_DATASETS:
            raise ValueError("alpaca_authorization_datasets_invalid")
        datasets.add(dataset)
        snapshot = SourceAuthorizationSnapshot(
            **{
                **raw_snapshot,
                "snapshot_id": _required_clean_text(raw_snapshot, "snapshot_id"),
                "terms_url": _required_clean_text(raw_snapshot, "terms_url"),
                "checked_at": datetime.fromisoformat(
                    _required_clean_text(raw_snapshot, "checked_at").replace(
                        "Z", "+00:00"
                    )
                ),
                "retention_policy": _required_clean_text(
                    raw_snapshot, "retention_policy"
                ),
                "evidence_sha256": _required_clean_text(
                    raw_snapshot, "evidence_sha256"
                ),
            }
        )
        _evidence_persistence(snapshot)
        registry.append(snapshot)
    if datasets != _ALPACA_DATASETS:
        raise ValueError("alpaca_authorization_datasets_invalid")
    for dataset in _ALPACA_DATASETS:
        registry.require("alpaca", dataset, _ALPACA_PLAN, "display")
    return registry


def _evidence_persistence(snapshot: SourceAuthorizationSnapshot) -> str:
    if snapshot.retention_policy != "personal_private_workspace_only":
        raise ValueError("alpaca_authorization_retention_invalid")
    return "encrypted_payload"


def _read_mapping(path: str | Path) -> Mapping[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError("alpaca_file_schema_invalid")
    return payload


def _read_owner_only_mapping(path: str | Path) -> Mapping[str, Any]:
    from .owner_only_file import read_owner_only_file

    raw = read_owner_only_file(path, maximum_bytes=1024 * 1024)
    payload = json.loads(raw.decode("utf-8", errors="strict"))
    if not isinstance(payload, Mapping):
        raise ValueError("alpaca_file_schema_invalid")
    return payload


def _required_clean_text(payload: Mapping[str, Any], key: str) -> str:
    value = payload[key]
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{key}_invalid")
    return value
