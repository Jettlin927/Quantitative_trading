from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from .alpaca import ProviderRequest, ProviderResponse


@dataclass(frozen=True)
class CapturedProviderRequest:
    method: str
    url: str
    header_names: tuple[str, ...]
    connect_timeout_seconds: float
    total_timeout_seconds: float


class DenyRecordingTransport:
    """只消费显式脚本响应，绝不进行未登记的网络请求。"""

    def __init__(self, responses: list[ProviderResponse | BaseException]) -> None:
        self._responses = deque(responses)
        self.requests: list[CapturedProviderRequest] = []

    def send(self, request: ProviderRequest) -> ProviderResponse:
        self.requests.append(
            CapturedProviderRequest(
                method=request.method,
                url=request.url,
                header_names=tuple(sorted(request.headers)),
                connect_timeout_seconds=request.connect_timeout_seconds,
                total_timeout_seconds=request.total_timeout_seconds,
            )
        )
        if not self._responses:
            raise AssertionError("unexpected_provider_request")
        scripted = self._responses.popleft()
        if isinstance(scripted, BaseException):
            raise scripted
        return scripted
