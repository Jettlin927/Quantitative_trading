"""受认证的官方 Streamable HTTP MCP ASGI adapter。"""

from __future__ import annotations

from contextlib import asynccontextmanager
import hmac
import os
from pathlib import Path
import stat
from typing import Any
from urllib.parse import urlsplit

from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route

from .mcp_gateway import PERSONAL_MCP_HTTP_POLICY, PersonalMcpGateway
from .mcp_protocol import create_mcp_protocol_server, redact_mcp_protocol_logs


class PersonalMcpHttpConfigurationError(RuntimeError):
    pass


def create_personal_mcp_http_app(
    *,
    gateway: PersonalMcpGateway,
    token_file: Path,
    allowed_origins: tuple[str, ...],
) -> Starlette:
    if gateway.transport_policy != PERSONAL_MCP_HTTP_POLICY:
        raise PersonalMcpHttpConfigurationError(
            "personal_mcp_http_transport_policy_required"
        )
    token = _load_bearer_token(token_file)
    origins = _validate_origins(allowed_origins)
    from mcp.server.streamable_http_manager import StreamableHTTPSessionManager

    manager = StreamableHTTPSessionManager(
        create_mcp_protocol_server(gateway),
        json_response=True,
    )
    protected = _AuthenticatedMcpApp(manager.handle_request, token, origins)

    @asynccontextmanager
    async def lifespan(_app: Starlette):
        try:
            with redact_mcp_protocol_logs():
                async with manager.run():
                    yield
        finally:
            gateway.close()

    return Starlette(
        routes=[Route("/mcp", endpoint=protected, methods=["GET", "POST", "DELETE"])],
        lifespan=lifespan,
    )


class _AuthenticatedMcpApp:
    def __init__(self, app: Any, token: bytes, origins: frozenset[str]) -> None:
        self._app = app
        self._token = token
        self._origins = origins

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        authorization = _single_header(scope, b"authorization")
        if not isinstance(authorization, bytes) or not authorization.startswith(
            b"Bearer "
        ):
            await _reject(401, "personal_mcp_unauthorized", scope, receive, send)
            return
        supplied = authorization[len(b"Bearer ") :]
        if not supplied or not hmac.compare_digest(supplied, self._token):
            await _reject(401, "personal_mcp_unauthorized", scope, receive, send)
            return
        origin = _single_header(scope, b"origin")
        if origin is False:
            await _reject(403, "personal_mcp_origin_forbidden", scope, receive, send)
            return
        if isinstance(origin, bytes):
            try:
                decoded_origin = origin.decode("ascii")
            except UnicodeDecodeError:
                decoded_origin = ""
            if decoded_origin not in self._origins:
                await _reject(403, "personal_mcp_origin_forbidden", scope, receive, send)
                return
        await self._app(scope, receive, send)


def _single_header(scope: dict[str, Any], name: bytes) -> bytes | None | bool:
    values = [value for key, value in scope.get("headers", ()) if key.lower() == name]
    if len(values) > 1:
        return False
    return values[0] if values else None


async def _reject(status: int, code: str, scope: Any, receive: Any, send: Any) -> None:
    response = PlainTextResponse(code, status_code=status)
    await response(scope, receive, send)


def _load_bearer_token(path: Path) -> bytes:
    try:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        try:
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != os.geteuid()
                or metadata.st_mode & 0o077
                or not metadata.st_mode & stat.S_IRUSR
            ):
                raise PersonalMcpHttpConfigurationError(
                    "personal_mcp_token_file_permissions_invalid"
                )
            raw = os.read(descriptor, 4097)
        finally:
            os.close(descriptor)
    except PersonalMcpHttpConfigurationError:
        raise
    except (OSError, TypeError, ValueError) as exc:
        raise PersonalMcpHttpConfigurationError(
            "personal_mcp_token_file_invalid"
        ) from exc
    token = raw.rstrip(b"\r\n")
    if (
        not token
        or len(raw) > 4096
        or any(byte <= 0x20 or byte == 0x7F for byte in token)
    ):
        raise PersonalMcpHttpConfigurationError("personal_mcp_token_invalid")
    return token


def _validate_origins(values: tuple[str, ...]) -> frozenset[str]:
    origins = frozenset(values)
    if not origins or len(origins) != len(values):
        raise PersonalMcpHttpConfigurationError("personal_mcp_origins_invalid")
    for origin in origins:
        try:
            parsed = urlsplit(origin)
            _ = parsed.port
        except (TypeError, ValueError) as exc:
            raise PersonalMcpHttpConfigurationError(
                "personal_mcp_origins_invalid"
            ) from exc
        if (
            origin in {"", "*", "null"}
            or "," in origin
            or parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.hostname is None
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path
            or parsed.query
            or parsed.fragment
            or origin != f"{parsed.scheme}://{parsed.netloc}"
        ):
            raise PersonalMcpHttpConfigurationError(
                "personal_mcp_origins_invalid"
            )
    return origins
