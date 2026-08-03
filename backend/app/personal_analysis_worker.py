from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import json
import os
from pathlib import Path
import signal
from stat import S_IMODE
from threading import Event

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from .personal_workspace.analysis import (
    AnalysisWorkspace,
    DeepSeekChatAdapter,
    PostgresAnalysisStore,
)
from .personal_workspace.crypto import PersonalDataCipher, load_keyring_file


@dataclass(frozen=True, repr=False)
class DeepSeekCredentials:
    api_key: str

    def __repr__(self) -> str:
        return "DeepSeekCredentials(api_key=<redacted>)"


def load_deepseek_credentials_file(path: str | Path) -> DeepSeekCredentials:
    credentials_path = Path(path)
    if not credentials_path.is_file():
        raise ValueError("deepseek_credentials_invalid")
    mode = S_IMODE(credentials_path.stat().st_mode)
    if mode & 0o077 or not mode & 0o400:
        raise ValueError("deepseek_credentials_mode_invalid")
    try:
        payload = json.loads(credentials_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise ValueError("deepseek_credentials_invalid") from None
    if not isinstance(payload, dict) or set(payload) != {"api_key"}:
        raise ValueError("deepseek_credentials_invalid")
    api_key = payload["api_key"]
    if not isinstance(api_key, str) or not api_key.strip() or api_key != api_key.strip():
        raise ValueError("deepseek_credentials_invalid")
    return DeepSeekCredentials(api_key=api_key)


@dataclass(frozen=True)
class PersonalAnalysisWorker:
    workspace: AnalysisWorkspace
    worker_id: str

    def run_once(self):
        return self.workspace.run_next(worker_id=self.worker_id)

    def run_forever(self, *, poll_seconds: float, stop_event: Event) -> None:
        if poll_seconds <= 0:
            raise ValueError("personal_analysis_worker_poll_invalid")
        while not stop_event.is_set():
            if self.run_once() is None:
                stop_event.wait(poll_seconds)


def build_personal_analysis_worker_from_environment() -> PersonalAnalysisWorker:
    database_url = os.getenv("PERSONAL_ANALYSIS_DATABASE_URL", "").strip()
    keyring_path = os.getenv("PERSONAL_DATA_KEYRING_FILE", "").strip()
    credentials_path = os.getenv("DEEPSEEK_CREDENTIALS_FILE", "").strip()
    if not database_url or not keyring_path or not credentials_path:
        raise RuntimeError("personal_analysis_worker_unconfigured")
    keyring = load_keyring_file(Path(keyring_path))
    credentials = load_deepseek_credentials_file(credentials_path)
    monthly_budget = Decimal(os.getenv("DEEPSEEK_MONTHLY_SOFT_BUDGET_USD", "5"))
    if monthly_budget <= 0:
        raise RuntimeError("personal_analysis_worker_unconfigured")
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
        provider=DeepSeekChatAdapter(api_key=credentials.api_key),
        monthly_soft_budget_usd=monthly_budget,
    )
    return PersonalAnalysisWorker(
        workspace=workspace,
        worker_id=os.getenv("PERSONAL_ANALYSIS_WORKER_ID", "personal-analysis-worker-1"),
    )


def main() -> None:
    worker = build_personal_analysis_worker_from_environment()
    poll_seconds = float(os.getenv("PERSONAL_ANALYSIS_WORKER_POLL_SECONDS", "5"))
    stop_event = Event()
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda _signum, _frame: stop_event.set())
    worker.run_forever(poll_seconds=poll_seconds, stop_event=stop_event)


if __name__ == "__main__":
    main()
