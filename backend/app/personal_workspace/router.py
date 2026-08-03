from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Callable

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from .contracts import (
    AddHoldingCommand,
    EditHoldingCommand,
    PersonalActor,
    PurgeHoldingCommand,
    RemoveHoldingCommand,
    RequestPurgeHoldingCommand,
    RestoreHoldingCommand,
    SaveSyntheticRecordCommand,
    SetUsdCashCommand,
    SyntheticTraceCommand,
)
from .journey import PersonalResearchJourney
from .portfolio import PortfolioBook
from .security import PersonalAccessConfig, authorize_personal_request


@dataclass(frozen=True)
class PersonalRuntime:
    access: PersonalAccessConfig
    actor: PersonalActor | None
    journey: PersonalResearchJourney | None
    portfolio: PortfolioBook | None = None

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


def _raise_domain_error(error: ValueError) -> None:
    code = str(error)
    if code in {
        "preview_changed",
        "revision_conflict",
        "duplicate_symbol",
        "purge_challenge_invalid",
        "purge_challenge_expired",
    }:
        status_code = 409
    elif code in {"invalid_command", "invalid_decimal", "unsupported_instrument"}:
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
