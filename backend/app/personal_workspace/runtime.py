from __future__ import annotations

from functools import lru_cache
import os
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from .contracts import PersonalActor
from .crypto import PersonalDataCipher, load_keyring_file
from .journey import PersonalResearchJourney
from .persistence import PostgresPersonalJourneyStore
from .router import PersonalRuntime
from .security import PersonalAccessConfig
from .synthetic import SyntheticWorkspaceAdapters


@lru_cache(maxsize=1)
def get_personal_runtime() -> PersonalRuntime:
    database_url = os.getenv("PRIVATE_DATABASE_URL", "").strip()
    gateway_path = os.getenv("PERSONAL_GATEWAY_TOKEN_FILE", "").strip()
    keyring_path = os.getenv("PERSONAL_DATA_KEYRING_FILE", "").strip()
    allowed_origins = frozenset(
        origin.strip()
        for origin in os.getenv("PERSONAL_ALLOWED_ORIGINS", "").split(",")
        if origin.strip()
    )
    if not database_url or not gateway_path or not keyring_path or not allowed_origins:
        return PersonalRuntime.unconfigured()

    try:
        gateway_token = Path(gateway_path).read_text(encoding="utf-8").strip()
        keyring = load_keyring_file(keyring_path)
    except (OSError, ValueError, KeyError, TypeError):
        return PersonalRuntime.unconfigured()
    if not gateway_token:
        return PersonalRuntime.unconfigured()

    private_engine = create_engine(database_url, pool_pre_ping=True)
    store = PostgresPersonalJourneyStore(
        sessionmaker(bind=private_engine, autoflush=False, expire_on_commit=False)
    )
    return PersonalRuntime(
        access=PersonalAccessConfig(
            gateway_token=gateway_token,
            allowed_origins=allowed_origins,
            configured=True,
        ),
        actor=PersonalActor(actor_id="local-owner"),
        journey=PersonalResearchJourney(
            store=store,
            cipher=PersonalDataCipher(keyring),
            adapters=SyntheticWorkspaceAdapters(provider_available=False),
        ),
    )
