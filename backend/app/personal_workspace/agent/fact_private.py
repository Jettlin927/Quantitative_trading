"""当前 actor 自有的本地事实到 EvidenceLedger 的可信边界。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
import json
from types import MappingProxyType
from typing import Any, Mapping

from .evidence import EvidenceLedger, EvidenceReadContext, EvidenceRecord, Persistence


@dataclass(frozen=True)
class ActorOwnedFactPolicy:
    source: str
    authorization_snapshot_id: str
    required_permissions: frozenset[str]
    persistence: Persistence = "encrypted_payload"
    allowed_purposes: frozenset[str] = frozenset({"domain_tool"})


def _policy(
    *, source: str, snapshot_id: str, permission: str
) -> ActorOwnedFactPolicy:
    return ActorOwnedFactPolicy(
        source=source,
        authorization_snapshot_id=snapshot_id,
        required_permissions=frozenset({permission}),
    )


PRIVATE_FACT_POLICY_HISTORY = MappingProxyType(
    {
        "personal_portfolio": (
            _policy(
                source="personal_portfolio",
                snapshot_id="actor-owned-personal-portfolio-v1",
                permission="portfolio:read",
            ),
        ),
        "personal_instrument_state": (
            _policy(
                source="personal_instrument_state",
                snapshot_id="actor-owned-personal-instrument-state-v1",
                permission="portfolio:read",
            ),
        ),
        "observation_rule_attention": (
            _policy(
                source="observation_rule_attention",
                snapshot_id="actor-owned-observation-rule-attention-v1",
                permission="portfolio:read",
            ),
        ),
        "instrument_relation_map": (
            _policy(
                source="instrument_relation_map",
                snapshot_id="actor-owned-instrument-relation-map-v1",
                permission="market:read",
            ),
        ),
    }
)

_PRIVATE_CURRENT_SNAPSHOT_BY_SOURCE = MappingProxyType(
    {
        "personal_portfolio": "actor-owned-personal-portfolio-v1",
        "personal_instrument_state": "actor-owned-personal-instrument-state-v1",
        "observation_rule_attention": "actor-owned-observation-rule-attention-v1",
        "instrument_relation_map": "actor-owned-instrument-relation-map-v1",
    }
)

# current 只能引用不可变历史 catalog；record API 不接受 snapshot/retention。
PRIVATE_FACT_POLICIES = MappingProxyType(
    {
        source: next(
            policy
            for policy in history
            if policy.authorization_snapshot_id
            == _PRIVATE_CURRENT_SNAPSHOT_BY_SOURCE[source]
        )
        for source, history in PRIVATE_FACT_POLICY_HISTORY.items()
    }
)

PRIVATE_FACT_RETENTION_BY_AUTHORIZATION = MappingProxyType(
    {
        (policy.source, policy.authorization_snapshot_id): policy.persistence
        for history in PRIVATE_FACT_POLICY_HISTORY.values()
        for policy in history
    }
)


class ActorOwnedFactService:
    """只接受内建 current policy；同 revision/content 是同一持久领域事实。"""

    def __init__(self, evidence_ledger: EvidenceLedger) -> None:
        self._evidence_ledger = evidence_ledger

    def record(
        self,
        *,
        context: EvidenceReadContext,
        source: str,
        logical_identity: str,
        payload: Mapping[str, Any],
        observed_at: datetime | None,
    ) -> EvidenceRecord:
        policy = PRIVATE_FACT_POLICIES.get(source)
        if policy is None:
            raise ValueError("private_fact_source_unknown")
        record = _actor_owned_record(
            context=context,
            policy=policy,
            logical_identity=logical_identity,
            payload=payload,
            observed_at=observed_at,
        )
        return self._evidence_ledger.put(context, record)


def _actor_owned_record(
    *,
    context: EvidenceReadContext,
    policy: ActorOwnedFactPolicy,
    logical_identity: str,
    payload: Mapping[str, Any],
    observed_at: datetime | None,
) -> EvidenceRecord:
    if context.purpose not in policy.allowed_purposes:
        raise PermissionError("source_unauthorized")
    missing = policy.required_permissions - context.permissions
    if missing:
        raise PermissionError("source_unauthorized")
    data = dict(payload)
    content_sha256 = _payload_sha256(data)
    authorized_logical_identity = (
        f"{policy.source}:{policy.authorization_snapshot_id}:{logical_identity}"
    )
    identity_sha256 = sha256(
        authorized_logical_identity.encode("utf-8")
    ).hexdigest()
    # 调用方的 freshness=0 表示本轮刚从 authoritative store 读回；这里保留
    # 不可变事实首次入账时间，不用墙钟时间为同 revision/content 伪造新 identity。
    return EvidenceRecord(
        evidence_id=(
            f"{policy.source}:{identity_sha256[:12]}:{content_sha256[:24]}"
        ),
        logical_identity=authorized_logical_identity,
        scope="actor",
        source=policy.source,
        content_sha256=content_sha256,
        authorized_fields=tuple(data),
        required_permissions=policy.required_permissions,
        allowed_purposes=policy.allowed_purposes,
        authorization_snapshot_id=policy.authorization_snapshot_id,
        observed_at=observed_at,
        published_at=None,
        effective_at=None,
        available_from=context.now,
        fetched_at=context.now,
        verified_at=context.now,
        expires_at=None,
        persistence=policy.persistence,
        payload=data,
    )


def _payload_sha256(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return sha256(encoded).hexdigest()
