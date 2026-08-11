"""官方 MCP stdio 协议翻译与默认关闭的进程入口。"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
import os
import sys
from typing import Any

from .mcp_gateway import (
    PersonalMcpGateway,
    PersonalMcpGatewayStopped,
)
from .mcp_composition import (
    PersonalMcpConfig,
    PersonalMcpConfigurationError,
    build_personal_mcp_gateway,
    load_mcp_config,
)
from .mcp_protocol import create_mcp_protocol_server, redact_mcp_protocol_logs


async def serve_stdio_gateway(gateway: PersonalMcpGateway) -> None:
    """只运行官方逐行 UTF-8 stdio transport，不创建网络 transport。"""

    import mcp.server.stdio

    server = create_mcp_protocol_server(gateway)
    try:
        with redact_mcp_protocol_logs():
            async with mcp.server.stdio.stdio_server() as (
                read_stream,
                write_stream,
            ):
                await server.run(
                    read_stream,
                    write_stream,
                    server.create_initialization_options(),
                )
    finally:
        gateway.close()


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
