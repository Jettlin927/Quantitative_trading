from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import os
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from .personal_workspace.analysis import (
    AnalysisWorkspace,
    OpenAIResponsesAdapter,
    PostgresAnalysisStore,
)
from .personal_workspace.crypto import PersonalDataCipher, load_keyring_file


@dataclass(frozen=True)
class PersonalAnalysisWorker:
    workspace: AnalysisWorkspace
    worker_id: str

    def run_once(self):
        return self.workspace.run_next(worker_id=self.worker_id)


def build_personal_analysis_worker_from_environment() -> PersonalAnalysisWorker:
    database_url = os.getenv("PRIVATE_DATABASE_URL", "").strip()
    keyring_path = os.getenv("PERSONAL_DATA_KEYRING_FILE", "").strip()
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not database_url or not keyring_path or not api_key:
        raise RuntimeError("personal_analysis_worker_unconfigured")
    keyring = load_keyring_file(Path(keyring_path))
    engine = create_engine(database_url, pool_pre_ping=True)
    session_factory = sessionmaker(
        bind=engine,
        autoflush=False,
        expire_on_commit=False,
    )
    workspace = AnalysisWorkspace(
        store=PostgresAnalysisStore(
            session_factory,
            cipher=PersonalDataCipher(keyring),
        ),
        evidence_reader=lambda actor, intent: (),
        provider=OpenAIResponsesAdapter(api_key=api_key),
        monthly_soft_budget_usd=Decimal(
            os.getenv("OPENAI_MONTHLY_SOFT_BUDGET_USD", "25")
        ),
    )
    return PersonalAnalysisWorker(
        workspace=workspace,
        worker_id=os.getenv("PERSONAL_ANALYSIS_WORKER_ID", "personal-analysis-worker-1"),
    )
