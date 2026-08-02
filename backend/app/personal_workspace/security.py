from __future__ import annotations

from dataclasses import dataclass, field
import secrets

from fastapi import HTTPException, Request


@dataclass(frozen=True)
class PersonalAccessConfig:
    gateway_token: str = field(repr=False)
    allowed_origins: frozenset[str]
    configured: bool


def authorize_personal_request(
    request: Request,
    config: PersonalAccessConfig,
    *,
    write: bool,
) -> None:
    if not config.configured:
        _reject(503, "personal_access_unconfigured", "个人工作台尚未配置。")

    supplied_token = request.headers.get("X-Personal-Gateway", "")
    if not supplied_token or not secrets.compare_digest(supplied_token, config.gateway_token):
        _reject(401, "personal_access_required", "请求未经过受信前端代理。")

    if not write:
        return

    if request.headers.get("Origin") not in config.allowed_origins:
        _reject(403, "origin_rejected", "写请求来源不在允许列表。")
    if request.headers.get("Sec-Fetch-Site") != "same-origin":
        _reject(403, "origin_rejected", "写请求必须来自同源页面。")
    if request.headers.get("X-Personal-Request") != "1":
        _reject(403, "origin_rejected", "缺少个人工作台写请求证明。")
    content_type = request.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
    if content_type != "application/json":
        _reject(422, "invalid_command", "写请求必须使用 application/json。")
    if not request.headers.get("Idempotency-Key", "").strip():
        _reject(422, "invalid_command", "写请求必须提供 idempotency key。")


def _reject(status_code: int, code: str, message: str) -> None:
    raise HTTPException(
        status_code=status_code,
        detail={"code": code, "message": message},
    )
