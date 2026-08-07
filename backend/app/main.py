from __future__ import annotations

from contextlib import asynccontextmanager
import os
from typing import Any

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select
from sqlalchemy.orm import Session

from .data_quality.contracts import QualityCheckContract
from .data_quality.runner import (
    list_quality_results,
    quality_run_to_dict,
    run_data_quality_check,
)
from .database import Base, assert_schema_revision_at_head, engine, get_db
from .models import DataQualityRun
from .personal_workspace.router import create_personal_router
from .personal_workspace.runtime import get_personal_runtime
from .schemas import DataQualityRunRequest


@asynccontextmanager
async def lifespan(_: FastAPI):
    assert_schema_revision_at_head(engine)
    yield


app = FastAPI(title="US Personal Investment Workspace", version="1.0.0", lifespan=lifespan)
cors_allowed_origins = sorted(
    {
        origin.strip()
        for variable in ("CORS_ALLOWED_ORIGINS", "PERSONAL_ALLOWED_ORIGINS")
        for origin in os.getenv(variable, "").split(",")
        if origin.strip()
    }
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_allowed_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(create_personal_router(get_personal_runtime))


@app.get("/")
def root() -> dict[str, str]:
    return {"service": "us-personal-investment-workspace", "status": "ok"}


@app.get("/api/health")
def health(db: Session = Depends(get_db), include_schema: bool = False) -> dict[str, Any]:
    db.execute(select(1))
    payload: dict[str, Any] = {
        "status": "ok",
        "service": "us-personal-investment-workspace",
        "database": "ok",
        "market": "us",
    }
    if include_schema:
        payload["schema"] = {
            "revisionManaged": True,
            "tableCount": len(Base.metadata.tables),
        }
    return payload


@app.post("/api/data-quality/runs", status_code=201)
def create_data_quality_run(
    payload: DataQualityRunRequest, db: Session = Depends(get_db)
) -> dict[str, Any]:
    try:
        contract = QualityCheckContract.create(
            scope=payload.scope,
            start_date=payload.start_date,
            end_date=payload.end_date,
            universe=payload.universe,
            universe_type=payload.universe_type,
            universe_source=payload.universe_source,
            universe_as_of_date=payload.universe_as_of_date,
            required_datasets=payload.required_datasets,
            benchmark=payload.benchmark,
            statement_timeout_ms=payload.statement_timeout_ms,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return run_data_quality_check(db, contract, code_commit=payload.code_commit)


@app.get("/api/data-quality/runs/{quality_run_id}")
def get_data_quality_run(
    quality_run_id: str, db: Session = Depends(get_db)
) -> dict[str, Any]:
    run = db.get(DataQualityRun, quality_run_id)
    if not run:
        raise HTTPException(status_code=404, detail="数据质量运行不存在")
    return quality_run_to_dict(run, list_quality_results(db, quality_run_id))
