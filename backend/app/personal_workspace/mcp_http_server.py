"""固定 loopback 的远端个人 MCP HTTP 进程入口。"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
import os
from pathlib import Path
import sys
from typing import Any

from .mcp_gateway import PERSONAL_MCP_HTTP_POLICY, PersonalMcpGatewayStopped
from .mcp_http import (
    PersonalMcpHttpConfigurationError,
    create_personal_mcp_http_app,
)
from .mcp_composition import (
    PersonalMcpConfig,
    PersonalMcpConfigurationError,
    build_personal_mcp_gateway,
)
from .mcp_gateway import normalize_actor_id
from .owner_only_file import OwnerOnlyFileError, read_owner_only_file


PERSONAL_MCP_HTTP_HOST = "127.0.0.1"
PERSONAL_MCP_HTTP_PORT = 16174
PERSONAL_MCP_HTTP_ALLOWED_ORIGINS = ("http://127.0.0.1:26174",)
_FORBIDDEN_CONFIGURATION = frozenset(
    {
        "PERSONAL_MCP_HTTP_HOST",
        "PERSONAL_MCP_HTTP_PORT",
        "PRIVATE_DATABASE_URL",
        "PERSONAL_MCP_TOKEN",
    }
)


class PersonalMcpHttpServerConfigurationError(RuntimeError):
    pass


@dataclass(frozen=True)
class PersonalMcpHttpServerConfig:
    enabled: bool
    actor_id: str = ""
    database_url_file: str = ""
    database_url: str = ""
    token_file: str = ""
    keyring_file: str = ""
    alpaca_credentials_file: str = ""
    alpaca_authorization_file: str = ""
    investment_news_dir: str = ""


def load_http_server_config(
    environment: Mapping[str, str],
) -> PersonalMcpHttpServerConfig:
    raw_enabled = environment.get("PERSONAL_MCP_ENABLED", "").strip().lower()
    if raw_enabled not in {"", "false", "true"}:
        raise PersonalMcpHttpServerConfigurationError(
            "personal_mcp_enabled_invalid"
        )
    if raw_enabled != "true":
        return PersonalMcpHttpServerConfig(enabled=False)
    if any(key in environment for key in _FORBIDDEN_CONFIGURATION):
        raise PersonalMcpHttpServerConfigurationError(
            "personal_mcp_http_forbidden_configuration"
        )
    values = {
        "actor_id": environment.get("PERSONAL_MCP_ACTOR_ID", "").strip(),
        "database_url_file": environment.get(
            "PERSONAL_MCP_DATABASE_URL_FILE", ""
        ).strip(),
        "token_file": environment.get("PERSONAL_MCP_TOKEN_FILE", "").strip(),
        "keyring_file": environment.get(
            "PERSONAL_DATA_KEYRING_FILE", ""
        ).strip(),
        "alpaca_credentials_file": environment.get(
            "ALPACA_CREDENTIALS_FILE", ""
        ).strip(),
        "alpaca_authorization_file": environment.get(
            "ALPACA_AUTHORIZATION_FILE", ""
        ).strip(),
        "investment_news_dir": environment.get("INVESTMENT_NEWS_DIR", "").strip(),
    }
    if any(not value for value in values.values()):
        raise PersonalMcpHttpServerConfigurationError(
            "personal_mcp_http_unconfigured"
        )
    try:
        values["actor_id"] = normalize_actor_id(values["actor_id"])
    except ValueError as exc:
        raise PersonalMcpHttpServerConfigurationError(
            "personal_mcp_actor_invalid"
        ) from exc
    return PersonalMcpHttpServerConfig(enabled=True, **values)


def _with_database_url(
    config: PersonalMcpHttpServerConfig,
) -> PersonalMcpHttpServerConfig:
    path = Path(config.database_url_file)
    try:
        raw = read_owner_only_file(path, maximum_bytes=4096)
    except OwnerOnlyFileError as exc:
        code = (
            "personal_mcp_database_file_permissions_invalid"
            if str(exc) == "permissions"
            else "personal_mcp_database_file_invalid"
        )
        raise PersonalMcpHttpServerConfigurationError(
            code
        ) from exc
    try:
        database_url = raw.decode("utf-8", errors="strict").strip()
    except UnicodeDecodeError as exc:
        raise PersonalMcpHttpServerConfigurationError(
            "personal_mcp_database_file_invalid"
        ) from exc
    if not database_url or "\n" in database_url:
        raise PersonalMcpHttpServerConfigurationError(
            "personal_mcp_database_file_invalid"
        )
    return replace(config, database_url=database_url)


def create_http_app_from_config(config: PersonalMcpHttpServerConfig) -> Any:
    mcp_config = PersonalMcpConfig(
        enabled=True,
        actor_id=config.actor_id,
        database_url=config.database_url,
        keyring_file=config.keyring_file,
        alpaca_credentials_file=config.alpaca_credentials_file,
        alpaca_authorization_file=config.alpaca_authorization_file,
        investment_news_dir=config.investment_news_dir,
    )
    gateway = build_personal_mcp_gateway(
        mcp_config,
        transport_policy=PERSONAL_MCP_HTTP_POLICY,
    )
    try:
        return create_personal_mcp_http_app(
            gateway=gateway,
            token_file=Path(config.token_file),
            allowed_origins=PERSONAL_MCP_HTTP_ALLOWED_ORIGINS,
        )
    except BaseException:
        gateway.close()
        raise


def serve_http_app(app: Any) -> None:
    import uvicorn

    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host=PERSONAL_MCP_HTTP_HOST,
            port=PERSONAL_MCP_HTTP_PORT,
            proxy_headers=False,
            forwarded_allow_ips="",
        )
    )
    server.run()


def run_from_environment(
    environment: Mapping[str, str],
    *,
    app_builder: Callable[[PersonalMcpHttpServerConfig], Any],
    app_runner: Callable[[Any], Any],
) -> int:
    config = load_http_server_config(environment)
    if not config.enabled:
        return 2
    app_runner(app_builder(_with_database_url(config)))
    return 0


def main(environment: Mapping[str, str] | None = None) -> int:
    try:
        return run_from_environment(
            environment if environment is not None else os.environ,
            app_builder=create_http_app_from_config,
            app_runner=serve_http_app,
        )
    except (
        PersonalMcpHttpServerConfigurationError,
        PersonalMcpHttpConfigurationError,
        PersonalMcpConfigurationError,
        PersonalMcpGatewayStopped,
    ) as exc:
        print(str(exc), file=sys.stderr, flush=True)
        return 2
    except KeyboardInterrupt:
        return 0
    except Exception:
        print("personal_mcp_http_runtime_failed", file=sys.stderr, flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
