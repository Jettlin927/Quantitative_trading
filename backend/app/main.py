from __future__ import annotations

from contextlib import asynccontextmanager
import os
from typing import Any

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select
from sqlalchemy.orm import Session

from .database import Base, assert_schema_revision_at_head, engine, get_db
from .personal_workspace.router import create_personal_router
from .personal_workspace.runtime import get_personal_runtime


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
