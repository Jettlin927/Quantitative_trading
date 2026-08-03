from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
import json
from typing import Callable

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from .contracts import (
    AddHoldingCommand,
    CreateObservationRuleCommand,
    EditHoldingCommand,
    EvaluateObservationRulesCommand,
    PersonalActor,
    PrepareAnalysisCommand,
    PurgeHoldingCommand,
    RemoveHoldingCommand,
    RequestPurgeHoldingCommand,
    RestoreHoldingCommand,
    SaveSyntheticRecordCommand,
    SetUsdCashCommand,
    SetObservationRuleStateCommand,
    StartAnalysisCommand,
    SyntheticTraceCommand,
)
from .analysis import AnalysisIntent, AnalysisWorkspace
from .journey import PersonalResearchJourney
from .instrument import InstrumentQuery, InstrumentWorkbench
from .portfolio import PortfolioBook
from .rules import ObservationRuleBook, RuleEvaluationRequest
from .security import PersonalAccessConfig, authorize_personal_request


@dataclass(frozen=True)
class PersonalRuntime:
    access: PersonalAccessConfig
    actor: PersonalActor | None
    journey: PersonalResearchJourney | None
    portfolio: PortfolioBook | None = None
    instruments: InstrumentWorkbench | None = None
    rules: ObservationRuleBook | None = None
    analyses: AnalysisWorkspace | None = None

    @classmethod
    def unconfigured(cls) -> "PersonalRuntime":
        return cls(
            access=PersonalAccessConfig(
                gateway_token="",
                allowed_origins=frozenset(),
                configured=False,
            ),
            actor=None,
            journey=None,
            portfolio=None,
            instruments=None,
            rules=None,
            analyses=None,
        )


def create_personal_router(
    runtime_provider: Callable[[], PersonalRuntime],
) -> APIRouter:
    router = APIRouter(prefix="/api/personal", tags=["personal-workbench"])

    def require_read(request: Request) -> PersonalRuntime:
        runtime = runtime_provider()
        authorize_personal_request(request, runtime.access, write=False)
        return runtime

    def require_write(request: Request) -> PersonalRuntime:
        runtime = runtime_provider()
        authorize_personal_request(request, runtime.access, write=True)
        return runtime

    @router.get("/today")
    def open_today(runtime: PersonalRuntime = Depends(require_read)) -> dict:
        actor, journey = _configured_services(runtime)
        try:
            return asdict(journey.open_today(actor))
        except SQLAlchemyError as exc:
            _raise_store_error(exc)
        except ValueError as exc:
            _raise_domain_error(exc)

    @router.get("/portfolio")
    def open_portfolio(runtime: PersonalRuntime = Depends(require_read)) -> dict:
        actor, portfolio = _configured_portfolio(runtime)
        try:
            return asdict(portfolio.open(actor))
        except SQLAlchemyError as exc:
            _raise_store_error(exc)

    @router.get("/instruments/{asset_id}")
    def open_instrument(
        asset_id: str,
        as_of: datetime | None = None,
        selected_date: date | None = None,
        runtime: PersonalRuntime = Depends(require_read),
    ) -> dict:
        actor, instruments = _configured_instruments(runtime)
        try:
            return asdict(
                instruments.open(
                    actor,
                    InstrumentQuery(
                        symbol=asset_id,
                        as_of=as_of or datetime.now(timezone.utc),
                        selected_date=selected_date,
                    ),
                )
            )
        except SQLAlchemyError as exc:
            _raise_store_error(exc)
        except ValueError as exc:
            _raise_domain_error(exc)

    @router.get("/rule-templates")
    def list_rule_templates(runtime: PersonalRuntime = Depends(require_read)) -> list[dict]:
        actor, rules = _configured_rules(runtime)
        return [asdict(item) for item in rules.list_templates(actor)]

    @router.get("/rules")
    def open_rules(runtime: PersonalRuntime = Depends(require_read)) -> dict:
        actor, rules = _configured_rules(runtime)
        try:
            return _asdict_mapping(rules.open(actor))
        except SQLAlchemyError as exc:
            _raise_store_error(exc)

    @router.post("/rules/commands")
    async def revise_rules(
        request: Request,
        runtime: PersonalRuntime = Depends(require_write),
    ) -> dict:
        actor, rules = _configured_rules(runtime)
        command = await _parse_rule_command(request)
        try:
            if isinstance(command, EvaluateObservationRulesCommand):
                result = rules.evaluate(
                    actor,
                    RuleEvaluationRequest(symbol=command.symbol, as_of=command.as_of),
                    idempotency_key=request.headers["Idempotency-Key"].strip(),
                )
            else:
                result = rules.revise(
                    actor,
                    command,
                    idempotency_key=request.headers["Idempotency-Key"].strip(),
                )
            return asdict(result)
        except SQLAlchemyError as exc:
            _raise_store_error(exc)
        except ValueError as exc:
            _raise_domain_error(exc)

    @router.post("/portfolio/commands")
    async def revise_portfolio(
        request: Request,
        runtime: PersonalRuntime = Depends(require_write),
    ) -> dict:
        actor, portfolio = _configured_portfolio(runtime)
        command = await _parse_portfolio_command(request)
        try:
            if isinstance(command, RequestPurgeHoldingCommand):
                result = portfolio.request_purge(
                    actor,
                    holding_id=command.holding_id,
                    expected_portfolio_revision=command.expected_portfolio_revision,
                )
            elif isinstance(command, PurgeHoldingCommand):
                result = portfolio.purge(
                    actor,
                    command,
                    idempotency_key=request.headers["Idempotency-Key"].strip(),
                )
            else:
                result = portfolio.revise(
                    actor,
                    command,
                    idempotency_key=request.headers["Idempotency-Key"].strip(),
                )
        except SQLAlchemyError as exc:
            _raise_store_error(exc)
        except ValueError as exc:
            _raise_domain_error(exc)
        return asdict(result)

    @router.post("/analysis-drafts", status_code=status.HTTP_202_ACCEPTED)
    async def prepare_analysis(
        request: Request,
        runtime: PersonalRuntime = Depends(require_write),
    ) -> dict:
        actor, analyses = _configured_analyses(runtime)
        command = await _parse_command(request, PrepareAnalysisCommand)
        try:
            return asdict(
                analyses.prepare(
                    actor,
                    AnalysisIntent(
                        question=command.question,
                        subject_ids=command.subject_ids,
                        selected_private_fields=command.selected_private_fields,
                    ),
                    idempotency_key=request.headers["Idempotency-Key"].strip(),
                )
            )
        except SQLAlchemyError as exc:
            _raise_store_error(exc)
        except ValueError as exc:
            _raise_domain_error(exc)

    @router.get("/analysis-drafts/{draft_id}")
    def open_analysis_draft(
        draft_id: str,
        runtime: PersonalRuntime = Depends(require_read),
    ) -> dict:
        actor, analyses = _configured_analyses(runtime)
        try:
            return asdict(analyses.open_draft(actor, draft_id))
        except SQLAlchemyError as exc:
            _raise_store_error(exc)
        except ValueError as exc:
            _raise_domain_error(exc)

    @router.post("/analyses", status_code=status.HTTP_202_ACCEPTED)
    async def start_analysis(
        request: Request,
        runtime: PersonalRuntime = Depends(require_write),
    ) -> dict:
        actor, analyses = _configured_analyses(runtime)
        command = await _parse_command(request, StartAnalysisCommand)
        try:
            return asdict(
                analyses.start(
                    actor,
                    draft_id=command.draft_id,
                    preview_sha256=command.preview_sha256,
                    idempotency_key=request.headers["Idempotency-Key"].strip(),
                )
            )
        except SQLAlchemyError as exc:
            _raise_store_error(exc)
        except ValueError as exc:
            _raise_domain_error(exc)

    @router.get("/analyses/{run_id}")
    def observe_analysis(
        run_id: str,
        runtime: PersonalRuntime = Depends(require_read),
    ) -> dict:
        actor, analyses = _configured_analyses(runtime)
        try:
            return asdict(analyses.observe(actor, run_id))
        except SQLAlchemyError as exc:
            _raise_store_error(exc)
        except ValueError as exc:
            _raise_domain_error(exc)

    @router.get("/analyses/{run_id}/events")
    def observe_analysis_events(
        run_id: str,
        runtime: PersonalRuntime = Depends(require_read),
    ) -> StreamingResponse:
        actor, analyses = _configured_analyses(runtime)
        try:
            run = analyses.observe(actor, run_id)
        except SQLAlchemyError as exc:
            _raise_store_error(exc)
        except ValueError as exc:
            _raise_domain_error(exc)

        def stream():
            for event in run.events:
                payload = asdict(event)
                payload["occurred_at"] = event.occurred_at.isoformat()
                yield "event: analysis_stage\n"
                yield f"id: {event.sequence}\n"
                yield f"data: {json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}\n\n"

        return StreamingResponse(stream(), media_type="text/event-stream")

    @router.post("/analyses/{run_id}/cancel")
    def cancel_analysis(
        run_id: str,
        runtime: PersonalRuntime = Depends(require_write),
    ) -> dict:
        actor, analyses = _configured_analyses(runtime)
        try:
            return asdict(analyses.cancel(actor, run_id))
        except SQLAlchemyError as exc:
            _raise_store_error(exc)
        except ValueError as exc:
            _raise_domain_error(exc)

    @router.post("/synthetic-traces", status_code=status.HTTP_201_CREATED)
    async def create_synthetic_trace(
        request: Request,
        runtime: PersonalRuntime = Depends(require_write),
    ) -> dict:
        actor, journey = _configured_services(runtime)
        command = await _parse_command(request, SyntheticTraceCommand)
        try:
            trace = journey.create_synthetic_trace(
                actor,
                idempotency_key=request.headers["Idempotency-Key"].strip(),
                question=command.question,
            )
        except SQLAlchemyError as exc:
            _raise_store_error(exc)
        return asdict(trace)

    @router.post("/synthetic-records", status_code=status.HTTP_201_CREATED)
    async def save_synthetic_record(
        request: Request,
        runtime: PersonalRuntime = Depends(require_write),
    ) -> dict:
        actor, journey = _configured_services(runtime)
        command = await _parse_command(request, SaveSyntheticRecordCommand)
        try:
            record = journey.save_synthetic_record(
                actor,
                analysis_id=command.analysis_id,
                preview_sha256=command.preview_sha256,
                idempotency_key=request.headers["Idempotency-Key"].strip(),
            )
        except SQLAlchemyError as exc:
            _raise_store_error(exc)
        except ValueError as exc:
            _raise_domain_error(exc)
        return asdict(record)

    return router


def _configured_services(runtime: PersonalRuntime) -> tuple[PersonalActor, PersonalResearchJourney]:
    if runtime.actor is None or runtime.journey is None:
        raise HTTPException(
            status_code=503,
            detail={"code": "personal_access_unconfigured", "message": "个人工作台尚未配置。"},
        )
    return runtime.actor, runtime.journey


def _configured_portfolio(runtime: PersonalRuntime) -> tuple[PersonalActor, PortfolioBook]:
    if runtime.actor is None or runtime.portfolio is None:
        raise HTTPException(
            status_code=503,
            detail={"code": "personal_access_unconfigured", "message": "个人持仓尚未配置。"},
        )
    return runtime.actor, runtime.portfolio


def _configured_instruments(runtime: PersonalRuntime) -> tuple[PersonalActor, InstrumentWorkbench]:
    if runtime.actor is None or runtime.instruments is None:
        raise HTTPException(
            status_code=503,
            detail={"code": "personal_access_unconfigured", "message": "标的工作台尚未配置。"},
        )
    return runtime.actor, runtime.instruments


def _configured_rules(runtime: PersonalRuntime) -> tuple[PersonalActor, ObservationRuleBook]:
    if runtime.actor is None or runtime.rules is None:
        raise HTTPException(
            status_code=503,
            detail={"code": "personal_access_unconfigured", "message": "观察规则尚未配置。"},
        )
    return runtime.actor, runtime.rules


def _configured_analyses(runtime: PersonalRuntime) -> tuple[PersonalActor, AnalysisWorkspace]:
    if runtime.actor is None or runtime.analyses is None:
        raise HTTPException(
            status_code=503,
            detail={"code": "provider_unavailable", "message": "AI 分析当前不可用。"},
        )
    return runtime.actor, runtime.analyses


def _raise_domain_error(error: ValueError) -> None:
    code = str(error)
    if code in {
        "preview_changed",
        "preview_consumed",
        "preview_expired",
        "revision_conflict",
        "duplicate_symbol",
        "purge_challenge_invalid",
        "purge_challenge_expired",
    }:
        status_code = 409
    elif code in {"budget_blocked", "provider_rate_limited"}:
        status_code = 429
    elif code == "provider_unavailable":
        status_code = 503
    elif code in {
        "invalid_command",
        "invalid_decimal",
        "invalid_rule_parameters",
        "unsupported_instrument",
        "as_of_requires_timezone",
    }:
        status_code = 422
    else:
        status_code = 404
    raise HTTPException(
        status_code=status_code,
        detail={"code": code, "message": "个人工作台命令未执行。"},
    ) from error


def _raise_store_error(error: SQLAlchemyError) -> None:
    raise HTTPException(
        status_code=503,
        detail={"code": "private_store_unavailable", "message": "私有存储当前不可用。"},
    ) from error


async def _parse_command(request: Request, command_type):
    try:
        payload = await request.json()
        return command_type.model_validate(payload)
    except (ValueError, ValidationError) as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "invalid_command", "message": "个人工作台命令格式无效。"},
        ) from exc


_PORTFOLIO_COMMAND_TYPES = {
    "add_holding": AddHoldingCommand,
    "edit_holding": EditHoldingCommand,
    "remove_holding": RemoveHoldingCommand,
    "restore_holding": RestoreHoldingCommand,
    "set_usd_cash": SetUsdCashCommand,
    "request_purge": RequestPurgeHoldingCommand,
    "confirm_purge": PurgeHoldingCommand,
}

_RULE_COMMAND_TYPES = {
    "create_rule": CreateObservationRuleCommand,
    "set_rule_state": SetObservationRuleStateCommand,
    "evaluate_rules": EvaluateObservationRulesCommand,
}


async def _parse_rule_command(request: Request):
    try:
        payload = await request.json()
        if not isinstance(payload, dict):
            raise ValueError("invalid_command")
        command_type = _RULE_COMMAND_TYPES.get(payload.get("type"))
        if command_type is None:
            raise ValueError("invalid_command")
        return command_type.model_validate(payload)
    except (ValueError, TypeError, ValidationError) as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "invalid_command", "message": "观察规则命令格式无效。"},
        ) from exc


def _asdict_mapping(value: dict) -> dict:
    return {
        key: [asdict(item) for item in items]
        for key, items in value.items()
    }


async def _parse_portfolio_command(request: Request):
    try:
        payload = await request.json()
        if not isinstance(payload, dict):
            raise ValueError("invalid_command")
        command_type = _PORTFOLIO_COMMAND_TYPES.get(payload.get("type"))
        if command_type is None:
            raise ValueError("invalid_command")
        return command_type.model_validate(payload)
    except ValidationError as exc:
        decimal_fields = {"quantity", "average_cost", "usd_cash"}
        code = (
            "invalid_decimal"
            if any(error.get("loc", (None,))[-1] in decimal_fields for error in exc.errors())
            else "invalid_command"
        )
        raise HTTPException(
            status_code=422,
            detail={"code": code, "message": "个人持仓命令格式无效。"},
        ) from exc
    except (ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "invalid_command", "message": "个人持仓命令格式无效。"},
        ) from exc
