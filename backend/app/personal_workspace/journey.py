from __future__ import annotations

from dataclasses import asdict
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from .contracts import (
    ExcludedAnalysisField,
    PersonalActor,
    SyntheticAnalysisPreview,
    SyntheticAnalysisClaim,
    SyntheticHoldingView,
    SyntheticMarketBar,
    SyntheticMarketView,
    SyntheticRecordView,
    SyntheticRuleEvaluation,
    SyntheticTraceView,
    TodayWorkspace,
)
from .crypto import EncryptedEnvelope, PersonalDataCipher
from .persistence import (
    InMemoryPersonalJourneyStore,
    StoredEncryptedRow,
    StoredSyntheticRecord,
    StoredSyntheticTrace,
)
from .synthetic import SyntheticWorkspaceAdapters

if TYPE_CHECKING:
    from .portfolio import PortfolioBook
    from .rules import ObservationRuleBook


class PersonalResearchJourney:
    def __init__(
        self,
        *,
        store: InMemoryPersonalJourneyStore,
        cipher: PersonalDataCipher,
        adapters: SyntheticWorkspaceAdapters,
        portfolio: "PortfolioBook | None" = None,
        rulebook: "ObservationRuleBook | None" = None,
    ) -> None:
        self._store = store
        self._cipher = cipher
        self._adapters = adapters
        self._portfolio = portfolio
        self._rulebook = rulebook

    def create_synthetic_trace(
        self,
        actor: PersonalActor,
        *,
        idempotency_key: str,
        question: str,
    ) -> SyntheticTraceView:
        existing = self._store.get_trace_by_idempotency(
            actor_id=actor.actor_id,
            idempotency_key=idempotency_key,
        )
        if existing is not None:
            return self._decode_trace(existing)

        workspace_id = self._store.workspace_id_for_actor(actor_id=actor.actor_id) or str(uuid4())
        holding_id = str(uuid4())
        analysis_id = str(uuid4())
        payload = self._adapters.build_trace_payload(
            workspace_id=workspace_id,
            holding_id=holding_id,
            analysis_id=analysis_id,
            question=question,
        )
        aad = _aad("personal_analysis_drafts", analysis_id)
        envelope = self._cipher.encrypt_json(payload, aad=aad)
        rules_id = str(uuid4())
        supporting_rows = (
            _encrypted_row(
                self._cipher,
                kind="workspace",
                table="personal_workspaces",
                row_id=workspace_id,
                value={"workspace_id": workspace_id, "synthetic": True},
            ),
            _encrypted_row(
                self._cipher,
                kind="holding",
                table="personal_holdings",
                row_id=holding_id,
                value=payload["holding"],
                metadata={
                    "symbol_hmac": self._cipher.symbol_lookup(
                        workspace_id=workspace_id,
                        normalized_symbol=payload["holding"]["symbol"],
                    )
                },
            ),
            _encrypted_row(
                self._cipher,
                kind="rules",
                table="personal_rule_evaluations",
                row_id=rules_id,
                value=payload["rule_evaluations"],
            ),
        )
        stored = self._store.save_trace(
            StoredSyntheticTrace(
                actor_id=actor.actor_id,
                analysis_id=analysis_id,
                idempotency_key=idempotency_key,
                preview_sha256=payload["analysis_preview"]["preview_sha256"],
                envelope=envelope,
                aad=aad,
                workspace_id=workspace_id,
                supporting_rows=supporting_rows,
            )
        )
        return self._decode_trace(stored)

    def save_synthetic_record(
        self,
        actor: PersonalActor,
        *,
        analysis_id: str,
        preview_sha256: str,
        idempotency_key: str,
    ) -> SyntheticRecordView:
        existing = self._store.get_record_by_idempotency(
            actor_id=actor.actor_id,
            idempotency_key=idempotency_key,
        )
        if existing is not None:
            return self._decode_record(existing)
        trace = self._store.get_trace(actor_id=actor.actor_id, analysis_id=analysis_id)
        if trace is None:
            raise ValueError("private_object_not_found")
        if trace.preview_sha256 != preview_sha256:
            raise ValueError("preview_changed")

        record_id = str(uuid4())
        record = SyntheticRecordView(
            record_id=record_id,
            analysis_id=analysis_id,
            version=1,
            status="saved",
            synthetic=True,
            research_eligible=False,
        )
        trace_view = self._decode_trace(trace)
        aad = _aad("personal_research_records", record_id)
        stored = self._store.save_record(
            StoredSyntheticRecord(
                actor_id=actor.actor_id,
                record_id=record_id,
                analysis_id=analysis_id,
                idempotency_key=idempotency_key,
                envelope=self._cipher.encrypt_json(
                    {
                        "view": asdict(record),
                        "content": {
                            "title": "合成研究记录",
                            "accepted_claim": asdict(trace_view.analysis_claim),
                            "boundary": "SYNTHETIC；不用于正式研究",
                        },
                    },
                    aad=aad,
                ),
                aad=aad,
            )
        )
        return self._decode_record(stored)

    def open_today(self, actor: PersonalActor) -> TodayWorkspace:
        trace = self._store.latest_trace(actor_id=actor.actor_id)
        portfolio = self._portfolio.open(actor) if self._portfolio is not None else None
        attention_items = self._rulebook.attention(actor) if self._rulebook is not None else ()
        if trace is None:
            return TodayWorkspace(
                trace=None,
                record=None,
                portfolio=portfolio,
                attention_items=attention_items,
            )
        record = self._store.record_for_analysis(
            actor_id=actor.actor_id,
            analysis_id=trace.analysis_id,
        )
        return TodayWorkspace(
            trace=self._decode_trace(trace),
            record=self._decode_record(record) if record is not None else None,
            portfolio=portfolio,
            attention_items=attention_items,
        )

    def _decode_trace(self, stored: StoredSyntheticTrace) -> SyntheticTraceView:
        payload = self._cipher.decrypt_json(stored.envelope, aad=stored.aad)
        holding = SyntheticHoldingView(**payload["holding"])
        market = SyntheticMarketView(
            source_health=payload["market"]["source_health"],
            as_of=payload["market"]["as_of"],
            bars=tuple(SyntheticMarketBar(**bar) for bar in payload["market"]["bars"]),
        )
        preview_payload = payload["analysis_preview"]
        preview = SyntheticAnalysisPreview(
            status=preview_payload["status"],
            provider=preview_payload["provider"],
            model=preview_payload["model"],
            included_fields=tuple(preview_payload["included_fields"]),
            excluded_fields=tuple(
                ExcludedAnalysisField(**item) for item in preview_payload["excluded_fields"]
            ),
            preview_sha256=preview_payload["preview_sha256"],
            retention=preview_payload["retention"],
        )
        return SyntheticTraceView(
            workspace_id=payload["workspace_id"],
            analysis_id=payload["analysis_id"],
            synthetic=payload["synthetic"],
            research_eligible=payload["research_eligible"],
            holding=holding,
            market=market,
            rule_evaluations=tuple(
                SyntheticRuleEvaluation(**item) for item in payload["rule_evaluations"]
            ),
            analysis_preview=preview,
            analysis_claim=SyntheticAnalysisClaim(
                claim_id=payload["analysis_claim"]["claim_id"],
                kind=payload["analysis_claim"]["kind"],
                statement=payload["analysis_claim"]["statement"],
                evidence_ids=tuple(payload["analysis_claim"]["evidence_ids"]),
            ),
            issues=tuple(payload["issues"]),
        )

    def _decode_record(self, stored: StoredSyntheticRecord) -> SyntheticRecordView:
        payload = self._cipher.decrypt_json(stored.envelope, aad=stored.aad)
        return SyntheticRecordView(**payload["view"])


def _aad(table: str, row_id: str) -> str:
    return f"private_workbench|{table}|{row_id}|payload|1"


def _encrypted_row(
    cipher: PersonalDataCipher,
    *,
    kind: str,
    table: str,
    row_id: str,
    value: Any,
    metadata: dict[str, Any] | None = None,
) -> StoredEncryptedRow:
    aad = _aad(table, row_id)
    return StoredEncryptedRow(
        kind=kind,
        row_id=row_id,
        envelope=cipher.encrypt_json(value, aad=aad),
        aad=aad,
        metadata=metadata or {},
    )
