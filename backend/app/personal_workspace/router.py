from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Callable

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from .contracts import (
    PersonalActor,
    SaveSyntheticRecordCommand,
    SyntheticTraceCommand,
)
from .journey import PersonalResearchJourney
from .security import PersonalAccessConfig, authorize_personal_request


@dataclass(frozen=True)
class PersonalRuntime:
    access: PersonalAccessConfig
    actor: PersonalActor | None
    journey: PersonalResearchJourney | None

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


def _raise_domain_error(error: ValueError) -> None:
    code = str(error)
    status_code = 409 if code == "preview_changed" else 404
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
