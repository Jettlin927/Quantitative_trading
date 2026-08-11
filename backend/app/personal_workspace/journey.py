from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable
from uuid import uuid4

from sqlalchemy.exc import SQLAlchemyError

from .contracts import (
    ExcludedAnalysisField,
    PersonalActor,
    SyntheticAnalysisPreview,
    SyntheticAnalysisClaim,
    SyntheticHoldingView,
    SyntheticMarketBar,
    SyntheticMarketView,
    SyntheticRuleEvaluation,
    SyntheticTraceView,
    InstrumentStatesView,
    TodayContextView,
    TodayGapView,
    TodayPortfolioSummaryView,
    TodayReadModel,
    TodayWorkspace,
)
from .crypto import EncryptedEnvelope, PersonalDataCipher
from .persistence import (
    InMemoryPersonalJourneyStore,
    StoredEncryptedRow,
    StoredSyntheticTrace,
)
from .synthetic import SyntheticWorkspaceAdapters

if TYPE_CHECKING:
    from .portfolio import EquitySnapshotView, PortfolioBook, PortfolioView
    from .rules import AttentionItem, ObservationRuleBook


class PersonalResearchJourney:
    def __init__(
        self,
        *,
        store: InMemoryPersonalJourneyStore,
        cipher: PersonalDataCipher,
        adapters: SyntheticWorkspaceAdapters,
        portfolio: "PortfolioBook | None" = None,
        rulebook: "ObservationRuleBook | None" = None,
        instrument_states_reader: Callable[[PersonalActor], InstrumentStatesView] | None = None,
        today_context_reader: Callable[[PersonalActor], TodayContextView] | None = None,
        equity_history_reader: Callable[[PersonalActor], tuple["EquitySnapshotView", ...]] | None = None,
    ) -> None:
        self._store = store
        self._cipher = cipher
        self._adapters = adapters
        self._portfolio = portfolio
        self._rulebook = rulebook
        self._instrument_states_reader = instrument_states_reader
        self._today_context_reader = today_context_reader
        self._equity_history_reader = equity_history_reader

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

    def open_today(
        self, actor: PersonalActor, *, include_synthetic: bool = False
    ) -> TodayWorkspace:
        trace = (
            self._store.latest_trace(actor_id=actor.actor_id)
            if include_synthetic
            else None
        )
        portfolio = self._portfolio.open(actor) if self._portfolio is not None else None
        attention_items = self._rulebook.attention(actor) if self._rulebook is not None else ()
        instrument_states = (
            self._instrument_states_reader(actor)
            if self._instrument_states_reader is not None
            else None
        )
        today_context = (
            self._today_context_reader(actor)
            if self._today_context_reader is not None
            else None
        )
        equity_snapshot_status = "unavailable"
        equity_snapshots: tuple[EquitySnapshotView, ...] = ()
        if self._equity_history_reader is not None:
            try:
                equity_snapshots = self._equity_history_reader(actor)
                equity_snapshot_status = (
                    "available" if len(equity_snapshots) >= 2 else "insufficient"
                )
            except (SQLAlchemyError, OSError, RuntimeError):
                equity_snapshot_status = "failed"
        if portfolio is not None:
            attention_items = tuple(
                item for item in attention_items if item.symbol in portfolio.active_symbols
            )
        read_model = _today_read_model(
            portfolio=portfolio,
            attention_items=attention_items,
            instrument_states=instrument_states,
            today_context=today_context,
            equity_snapshot_status=equity_snapshot_status,
            equity_snapshots=equity_snapshots,
        )
        if trace is None:
            return TodayWorkspace(
                trace=None,
                portfolio=portfolio,
                attention_items=attention_items,
                read_model=read_model,
            )
        return TodayWorkspace(
            trace=self._decode_trace(trace),
            portfolio=portfolio,
            attention_items=attention_items,
            read_model=read_model,
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


def _today_read_model(
    *,
    portfolio: "PortfolioView | None",
    attention_items: tuple["AttentionItem", ...],
    instrument_states: InstrumentStatesView | None,
    today_context: TodayContextView | None,
    equity_snapshot_status: str,
    equity_snapshots: tuple["EquitySnapshotView", ...],
) -> TodayReadModel:
    active = tuple(
        holding
        for holding in (portfolio.holdings if portfolio is not None else ())
        if holding.state == "active"
    )
    total_equity = portfolio.total_equity if portfolio is not None else None
    portfolio_summary = TodayPortfolioSummaryView(
        portfolio_revision=(portfolio.portfolio_revision if portfolio is not None else 0),
        total_equity_availability=(
            total_equity.availability if total_equity is not None else "not_available"
        ),
        total_equity_value=(total_equity.value if total_equity is not None else None),
        total_equity_as_of=(total_equity.as_of if total_equity is not None else None),
        active_holding_count=len(active),
        active_holding_symbols=tuple(holding.symbol for holding in active),
        priced_holding_count=sum(
            holding.market_value.availability == "available" for holding in active
        ),
        issues=tuple(portfolio.issues if portfolio is not None else ()),
        equity_snapshot_status=equity_snapshot_status,
        equity_snapshots=equity_snapshots,
    )
    gaps = list(today_context.gaps if today_context is not None else ())
    if equity_snapshot_status != "available":
        gaps.append(
            TodayGapView(
                code=f"equity_snapshots_{equity_snapshot_status}",
                subject="portfolio_equity_history",
            )
        )
    status = today_context.status if today_context is not None else "unavailable"
    if status == "success" and gaps:
        status = "partial"
    states = instrument_states or InstrumentStatesView(
        revision=0,
        items=(),
        followed_items=(),
        watch_observations=(),
        active_candidates=(),
        archived_candidates=(),
    )
    return TodayReadModel(
        status=status,
        as_of=today_context.as_of if today_context is not None else None,
        period=today_context.period if today_context is not None else None,
        portfolio=portfolio_summary,
        attention_items=attention_items,
        fact_events=today_context.fact_events if today_context is not None else (),
        watch_observations=states.followed_items,
        active_candidates=states.active_candidates,
        archived_candidates=states.archived_candidates,
        gaps=tuple(gaps),
        field_coverage=(today_context.field_coverage if today_context is not None else None),
        freshness_seconds=(
            today_context.freshness_seconds if today_context is not None else None
        ),
    )

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
