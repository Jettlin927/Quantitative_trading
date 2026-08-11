"""两个 MCP transport 共用的只读领域装配。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from .mcp_gateway import (
    PERSONAL_MCP_STDIO_POLICY,
    PersonalMcpGateway,
    PersonalMcpTransportPolicy,
    normalize_actor_id,
)


class PersonalMcpConfigurationError(RuntimeError):
    pass


@dataclass(frozen=True)
class PersonalMcpConfig:
    enabled: bool
    actor_id: str = ""
    database_url: str = ""
    keyring_file: str = ""
    alpaca_credentials_file: str = ""
    alpaca_authorization_file: str = ""
    investment_news_dir: str = ""


def load_mcp_config(environment: Mapping[str, str]) -> PersonalMcpConfig:
    raw_enabled = environment.get("PERSONAL_MCP_ENABLED", "").strip().lower()
    if raw_enabled not in {"", "false", "true"}:
        raise PersonalMcpConfigurationError("personal_mcp_enabled_invalid")
    if raw_enabled != "true":
        return PersonalMcpConfig(enabled=False)
    values = {
        "actor_id": environment.get("PERSONAL_MCP_ACTOR_ID", "").strip(),
        "database_url": environment.get("PRIVATE_DATABASE_URL", "").strip(),
        "keyring_file": environment.get("PERSONAL_DATA_KEYRING_FILE", "").strip(),
        "alpaca_credentials_file": environment.get(
            "ALPACA_CREDENTIALS_FILE", ""
        ).strip(),
        "alpaca_authorization_file": environment.get(
            "ALPACA_AUTHORIZATION_FILE", ""
        ).strip(),
        "investment_news_dir": environment.get("INVESTMENT_NEWS_DIR", "").strip(),
    }
    if any(not value for value in values.values()):
        raise PersonalMcpConfigurationError("personal_mcp_unconfigured")
    try:
        values["actor_id"] = normalize_actor_id(values["actor_id"])
    except ValueError as exc:
        raise PersonalMcpConfigurationError("personal_mcp_actor_invalid") from exc
    values["database_url"] = _normalize_database_url(values["database_url"])
    return PersonalMcpConfig(enabled=True, **values)


def _normalize_database_url(value: str) -> str:
    try:
        from sqlalchemy.engine import make_url

        parsed = make_url(value)
        if parsed.drivername != "postgresql+psycopg":
            raise ValueError("database_driver_invalid")
        _ = parsed.port
        return parsed.render_as_string(hide_password=False)
    except Exception as exc:
        raise PersonalMcpConfigurationError(
            "personal_mcp_database_invalid"
        ) from exc


def build_personal_mcp_gateway(
    config: PersonalMcpConfig,
    *,
    transport_policy: PersonalMcpTransportPolicy = PERSONAL_MCP_STDIO_POLICY,
) -> PersonalMcpGateway:
    """通过现有 composition 装配唯一 registry 与共享 capability audit。"""

    from .composition import build_personal_services
    from .crypto import load_owner_only_keyring_file
    from .market_runtime import load_owner_only_personal_market_readers

    if not config.enabled:
        raise PersonalMcpConfigurationError("personal_mcp_disabled")
    try:
        database_url = _normalize_database_url(config.database_url)
        news_dir = Path(config.investment_news_dir)
        if not news_dir.is_dir() or not (
            news_dir / "scripts" / "fetch.py"
        ).is_file():
            raise PersonalMcpConfigurationError(
                "personal_mcp_news_source_invalid"
            )
        keyring = load_owner_only_keyring_file(config.keyring_file)
        market_readers = load_owner_only_personal_market_readers(
            credentials_file=config.alpaca_credentials_file,
            authorization_file=config.alpaca_authorization_file,
        )
        services = build_personal_services(
            database_url=database_url,
            keyring=keyring,
            challenge_key=sha256(
                f"personal-mcp-readonly|{config.actor_id}".encode("utf-8")
            ).digest(),
            investment_news_dir=config.investment_news_dir,
            market_readers=market_readers,
        )
        if services.portfolio_store.load(
            actor_id=config.actor_id
        ).workspace_id is None:
            raise PersonalMcpConfigurationError("personal_mcp_actor_unknown")
        if services.market_readers.market is None:
            raise PersonalMcpConfigurationError(
                "personal_mcp_market_source_invalid"
            )
        return PersonalMcpGateway(
            registry=services.domain_tools,
            audit_store=services.evidence_store,
            actor_id=config.actor_id,
            transport_policy=transport_policy,
        )
    except PersonalMcpConfigurationError:
        raise
    except Exception as exc:
        raise PersonalMcpConfigurationError(
            "personal_mcp_source_invalid"
        ) from exc
