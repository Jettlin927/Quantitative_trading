"""两个 MCP transport 共享的官方 low-level server 翻译。"""

from __future__ import annotations

from contextlib import contextmanager
import logging
from typing import Any, Iterator

from .mcp_gateway import (
    PersonalMcpGateway,
    bounded_audit_tool_name,
    call_tool_result,
)


_MCP_LOWLEVEL_LOGGER = "mcp.server.lowlevel.server"
_UNKNOWN_TOOL_WARNING = "Tool '%s' not listed, no validation will be performed"


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


@contextmanager
def redact_mcp_protocol_logs() -> Iterator[None]:
    logger = logging.getLogger(_MCP_LOWLEVEL_LOGGER)
    redaction_filter = _UnknownToolWarningRedactionFilter()
    logger.addFilter(redaction_filter)
    try:
        yield
    finally:
        logger.removeFilter(redaction_filter)


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
