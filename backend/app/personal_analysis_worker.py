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
    mode = os.getenv("PERSONAL_ANALYSIS_MODE", "legacy").strip().lower()
    if mode == "agent":
        workspace = _build_agent_workspace(
            session_factory=session_factory,
            keyring=keyring,
            api_key=credentials.api_key,
            monthly_budget=monthly_budget,
        )
    else:
        store = PostgresAnalysisStore(
            session_factory,
            cipher=PersonalDataCipher(keyring),
        )
        workspace = AnalysisWorkspace(
            store=store,
            evidence_reader=lambda actor, intent: (),
            provider=DeepSeekChatAdapter(api_key=credentials.api_key),
            monthly_soft_budget_usd=monthly_budget,
        )
    return PersonalAnalysisWorker(
        workspace=workspace,
        worker_id=os.getenv("PERSONAL_ANALYSIS_WORKER_ID", "personal-analysis-worker-1"),
    )


def _build_agent_workspace(
    *,
    session_factory,
    keyring,
    api_key: str,
    monthly_budget: Decimal,
):
    """agent 模式：tool-use 循环 + 持仓/K线/新闻工具（数据源缺失时工具降级）。"""
    from .personal_workspace.agent.deepseek_provider import DeepSeekAgentChatAdapter
    from .personal_workspace.agent.workspace import build_agent_workspace
    from .personal_workspace.analysis import PostgresAnalysisStore

    store = PostgresAnalysisStore(
        session_factory,
        cipher=PersonalDataCipher(keyring),
    )
    return build_agent_workspace(
        store=store,
        session_factory=session_factory,
        cipher=PersonalDataCipher(keyring),
        provider=DeepSeekAgentChatAdapter(api_key=api_key),
        monthly_soft_budget_usd=monthly_budget,
        monthly_spend_reader=lambda actor, now: store.monthly_spend_usd(
            actor.actor_id, now
        ),
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
