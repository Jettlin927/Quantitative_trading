"""get_kline 工具：查询目标美股标的的日 K 线（ohlc + 成交量）。

通过市场观察适配器以 purpose="ai_context" 授权获取——授权未授予 ai_context 时
fail-closed 返回错误，符合来源授权契约。
"""

from __future__ import annotations

from datetime import timedelta
import json
from typing import Any

from ....market_observation.alpaca import (
    AlpacaMarketObservationAdapter,
    MarketObservationError,
)
from ..protocol import Tool, ToolContext, ToolResult


class GetKlineTool:
    name = "get_kline"
    description = (
        "查询目标美股标的的日 K 线（open/high/low/close + 成交量，按交易日升序）。"
        "参数：symbol（美股代码，必需）、days（回溯自然日，默认 90，范围 10-500）、"
        "limit（返回最近 N 根，默认 120，范围 1-500）。数据为 Alpaca 日线快照。"
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "symbol": {"type": "string"},
            "days": {"type": "integer", "minimum": 10, "maximum": 500},
            "limit": {"type": "integer", "minimum": 1, "maximum": 500},
        },
        "required": ["symbol"],
    }

    def __init__(self, *, adapter: AlpacaMarketObservationAdapter | None) -> None:
        self._adapter = adapter

    def as_tool(self) -> Tool:
        return Tool(
            name=self.name,
            description=self.description,
            input_schema=self.input_schema,
            run=self.run,
        )

    def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        if self._adapter is None:
            return ToolResult(ok=False, content="", error="kline_unavailable")
        symbol = str(args.get("symbol") or "").strip().upper()
        if not symbol:
            return ToolResult(ok=False, content="", error="invalid_args")
        days = _clamp_int(args.get("days"), 90, 10, 500)
        limit = _clamp_int(args.get("limit"), 120, 1, 500)
        now = ctx.clock()
        end_date = now.date()
        start_date = end_date - timedelta(days=days)
        try:
            observation = self._adapter.observe_daily_bars(
                symbol,
                start_date=start_date,
                end_date=end_date,
                fetched_at=now,
                purpose="ai_context",
            )
        except PermissionError as exc:
            return ToolResult(ok=False, content="", error=_failure_code(exc))
        except (ValueError, MarketObservationError) as exc:
            return ToolResult(ok=False, content="", error=_failure_code(exc))
        selected = (
            observation.provider_adjusted
            if observation.provider_adjusted.availability == "available"
            else observation.raw
        )
        if selected.availability != "available" or not selected.value:
            return ToolResult(
                ok=False, content="", error=selected.reason_code or "kline_unavailable"
            )
        bars = [
            {
                "date": bar.trade_date.isoformat(),
                "open": str(bar.open),
                "high": str(bar.high),
                "low": str(bar.low),
                "close": str(bar.close),
                "volume": bar.volume,
            }
            for bar in selected.value[-limit:]
        ]
        return ToolResult(
            ok=True,
            content=json.dumps(
                {
                    "symbol": symbol,
                    "adjustment": (
                        "provider_adjusted"
                        if selected is observation.provider_adjusted
                        else "raw"
                    ),
                    "as_of": selected.as_of.isoformat() if selected.as_of else None,
                    "source_health": selected.source_health,
                    "bars": bars,
                    "count": len(bars),
                },
                ensure_ascii=False,
            ),
        )


def _clamp_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, parsed))


def _failure_code(exc: Exception) -> str:
    code = getattr(exc, "code", None)
    if isinstance(code, str) and code:
        return code[:80]
    if isinstance(exc, PermissionError):
        return "authorization_denied"
    if isinstance(exc, ValueError) and str(exc):
        return str(exc)[:80]
    return type(exc).__name__
