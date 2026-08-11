"""官方 MCP stdio 协议翻译与默认关闭的进程入口。"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from hashlib import sha256
import logging
import os
from pathlib import Path
import sys
from typing import Any

from .mcp_gateway import (
    PERSONAL_MCP_TOOL_ALLOWLIST,
    PersonalMcpGateway,
    PersonalMcpGatewayStopped,
    bounded_audit_tool_name,
    call_tool_result,
    normalize_actor_id,
)


_MCP_LOWLEVEL_LOGGER = "mcp.server.lowlevel.server"
_UNKNOWN_TOOL_WARNING = (
    "Tool '%s' not listed, no validation will be performed"
)


class _UnknownToolWarningRedactionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            if (
                record.name == _MCP_LOWLEVEL_LOGGER
                and record.msg == _UNKNOWN_TOOL_WARNING
            ):
                if isinstance(record.args, tuple) and record.args:
                    record.args = (bounded_audit_tool_name(record.args[0]),)
                else:
                    record.args = ("rejected_tool:0000000000000000",)
        except BaseException:
            try:
                record.msg = _UNKNOWN_TOOL_WARNING
                record.args = ("rejected_tool:0000000000000000",)
            except BaseException:
                pass
        return True


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


def create_mcp_protocol_server(gateway: PersonalMcpGateway):
    """只注册 tools；initialize、生命周期和 framing 全交给官方 SDK。"""

    from mcp import types
    from mcp.server.lowlevel import Server

    server = Server("personal-investment-workbench", version="1")

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        return [
            types.Tool(
                name=item.name,
                description=item.description,
                inputSchema=dict(item.input_schema),
            )
            for item in gateway.tool_definitions()
        ]

    @server.call_tool(validate_input=False)
    async def call_tool(name: str, arguments: dict[str, Any]):
        return call_tool_result(await gateway.call_tool(name, arguments))

    return server


async def serve_stdio_gateway(gateway: PersonalMcpGateway) -> None:
    """只运行官方逐行 UTF-8 stdio transport，不创建网络 transport。"""

    import mcp.server.stdio

    server = create_mcp_protocol_server(gateway)
    sdk_logger = logging.getLogger(_MCP_LOWLEVEL_LOGGER)
    redaction_filter = _UnknownToolWarningRedactionFilter()
    sdk_logger.addFilter(redaction_filter)
    try:
        async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
            await server.run(
                read_stream,
                write_stream,
                server.create_initialization_options(),
            )
    finally:
        sdk_logger.removeFilter(redaction_filter)
        gateway.close()


def build_personal_mcp_gateway(config: PersonalMcpConfig) -> PersonalMcpGateway:
    """通过现有 composition 装配唯一 registry 与共享 capability audit。"""

    from .composition import build_personal_services
    from .crypto import load_keyring_file

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
        keyring = load_keyring_file(config.keyring_file)
        services = build_personal_services(
            database_url=database_url,
            keyring=keyring,
            challenge_key=sha256(
                f"personal-mcp-readonly|{config.actor_id}".encode("utf-8")
            ).digest(),
            alpaca_credentials_file=config.alpaca_credentials_file,
            alpaca_authorization_file=config.alpaca_authorization_file,
            investment_news_dir=config.investment_news_dir,
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
        )
    except PersonalMcpConfigurationError:
        raise
    except Exception as exc:
        raise PersonalMcpConfigurationError(
            "personal_mcp_source_invalid"
        ) from exc


def run_from_environment(
    environment: Mapping[str, str],
    *,
    services_builder: Callable[[PersonalMcpConfig], Any],
    stdio_runner: Callable[[Any], Any],
) -> int:
    """只有显式启用时才装配领域服务并进入 stdio 服务循环。"""

    config = load_mcp_config(environment)
    if not config.enabled:
        return 2
    services = services_builder(config)
    stdio_runner(services)
    return 0


def main(environment: Mapping[str, str] | None = None) -> int:
    """进程入口只写 stderr；stdout 专供官方 MCP stdio transport。"""

    try:
        config = load_mcp_config(environment if environment is not None else os.environ)
        if not config.enabled:
            print("personal_mcp_disabled", file=sys.stderr, flush=True)
            return 2
        gateway = build_personal_mcp_gateway(config)
        asyncio.run(serve_stdio_gateway(gateway))
    except (PersonalMcpConfigurationError, PersonalMcpGatewayStopped) as exc:
        print(str(exc), file=sys.stderr, flush=True)
        return 2
    except KeyboardInterrupt:
        return 0
    except Exception:
        print("personal_mcp_runtime_failed", file=sys.stderr, flush=True)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
