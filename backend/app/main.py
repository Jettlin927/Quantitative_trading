from __future__ import annotations

from contextlib import asynccontextmanager
import os
from pathlib import Path
from typing import Any
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, Response
from sqlalchemy import func, select
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
from .quant_research.readiness import (
    evaluate_quality_run_readiness,
    evaluate_research_readiness,
)
from .research_analytics import HistoricalAnalyticsError, get_publication_analytics
from .research_catalog import (
    get_formal_research_detail,
    get_publication_projection,
    get_strategy_profile,
    list_strategy_profiles,
)
from .research_publication import (
    get_evaluation_artifact_path,
    render_evaluation_report,
    render_evaluation_report_page,
)
from .schemas import (
    DataQualityRunRequest,
    FormalResearchDetailOut,
    ResearchPublicationAnalyticsOut,
    ResearchPublicationProjectionOut,
    StrategyProfileOut,
    StrategyProfileSummaryOut,
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    assert_schema_revision_at_head(engine)
    yield


app = FastAPI(title="US Research Workspace", version="1.0.0", lifespan=lifespan)
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
    return {"service": "us-research-workspace", "status": "ok"}


@app.get("/api/health")
def health(db: Session = Depends(get_db), include_schema: bool = False) -> dict[str, Any]:
    db.execute(select(1))
    payload: dict[str, Any] = {
        "status": "ok",
        "service": "us-research-workspace",
        "database": "ok",
        "market": "us",
    }
    if include_schema:
        payload["schema"] = {
            "revisionManaged": True,
            "tableCount": len(Base.metadata.tables),
        }
    return payload


def get_research_table_counts(db: Session) -> dict[str, int]:
    counts: dict[str, int] = {}
    for name, table in Base.metadata.tables.items():
        try:
            counts[name] = int(db.scalar(select(func.count()).select_from(table)) or 0)
        except Exception:
            db.rollback()
            counts[name] = 0
    return counts


@app.get("/api/research/readiness")
def get_research_readiness(
    scope: str = "etf_time_series", db: Session = Depends(get_db)
) -> dict[str, Any]:
    try:
        return evaluate_research_readiness(
            scope,
            Base.metadata.tables.keys(),
            get_research_table_counts(db),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/research/strategies", response_model=list[StrategyProfileSummaryOut])
def list_research_strategy_profiles(
    db: Session = Depends(get_db),
) -> list[StrategyProfileSummaryOut]:
    return list_strategy_profiles(db)


@app.get("/api/research/strategies/{strategy_id}", response_model=StrategyProfileOut)
def get_research_strategy_profile(
    strategy_id: str, db: Session = Depends(get_db)
) -> StrategyProfileOut:
    profile = get_strategy_profile(db, strategy_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="策略档案不存在")
    return profile


@app.get(
    "/api/research/formal-researches/{research_id}",
    response_model=FormalResearchDetailOut,
)
def get_research_detail(
    research_id: UUID, db: Session = Depends(get_db)
) -> FormalResearchDetailOut:
    detail = get_formal_research_detail(db, str(research_id))
    if detail is None:
        raise HTTPException(status_code=404, detail="正式研究不存在")
    return detail


@app.get(
    "/api/research/publications/{publication_id}",
    response_model=ResearchPublicationProjectionOut,
)
def get_research_publication(
    publication_id: UUID, db: Session = Depends(get_db)
) -> ResearchPublicationProjectionOut:
    projection = get_publication_projection(db, str(publication_id))
    if projection is None:
        raise HTTPException(status_code=404, detail="研究发布不存在")
    return projection


@app.get(
    "/api/research/publications/{publication_id}/analytics",
    response_model=ResearchPublicationAnalyticsOut,
)
def get_research_publication_analytics(
    publication_id: UUID, db: Session = Depends(get_db)
) -> ResearchPublicationAnalyticsOut:
    try:
        analytics = get_publication_analytics(db, str(publication_id))
    except HistoricalAnalyticsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if analytics is None:
        raise HTTPException(status_code=404, detail="研究发布不存在")
    return analytics


@app.get("/api/research/evaluations/{evaluation_id}/artifacts/{filename}")
def get_research_evaluation_artifact(
    evaluation_id: UUID,
    filename: str,
    db: Session = Depends(get_db),
) -> Response:
    artifact_root = Path(os.getenv("RESEARCH_ARTIFACT_ROOT", "outputs/research-runs"))
    try:
        path = get_evaluation_artifact_path(artifact_root, str(evaluation_id), filename)
        if filename == "report.html":
            return HTMLResponse(
                render_evaluation_report(db, artifact_root, str(evaluation_id)),
                headers={"Cache-Control": "no-cache"},
            )
        if not path.is_file():
            raise FileNotFoundError
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="发布工件不存在") from exc
    media_type = "application/json" if filename.endswith(".json") else "application/octet-stream"
    return FileResponse(path, media_type=media_type)


@app.get("/api/research/evaluations/{evaluation_id}/report")
def get_research_evaluation_report(
    evaluation_id: UUID, db: Session = Depends(get_db)
) -> HTMLResponse:
    artifact_root = Path(os.getenv("RESEARCH_ARTIFACT_ROOT", "outputs/research-runs"))
    try:
        report = render_evaluation_report_page(db, artifact_root, str(evaluation_id))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="研究评价报告不存在") from exc
    return HTMLResponse(report, headers={"Cache-Control": "no-cache"})


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


@app.get("/api/research/readiness/{quality_run_id}")
def get_research_readiness_by_quality_run(
    quality_run_id: str, db: Session = Depends(get_db)
) -> dict[str, Any]:
    run = db.get(DataQualityRun, quality_run_id)
    if not run:
        raise HTTPException(status_code=404, detail="数据质量运行不存在")
    return evaluate_quality_run_readiness(run, list_quality_results(db, quality_run_id))
