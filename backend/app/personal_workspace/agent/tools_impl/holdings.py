"""get_holdings 工具：查询当前真实美股持仓（仅当前 actor 自己的私有数据）。"""

from __future__ import annotations

import json
from typing import Any

from ...portfolio import PortfolioMarketReader, PortfolioStore
from ..protocol import Tool, ToolContext, ToolResult


class GetHoldingsTool:
    name = "get_holdings"
    description = (
        "查询当前真实美股持仓（用户手工维护的私有数据）：返回各持仓的 symbol、名称、数量、"
        "平均成本、币种与状态，以及可用时的最新价与市值快照（含数据时间/源信息）。"
        "无参数。数据仅限当前用户，为时间点快照。"
    )
    input_schema: dict[str, Any] = {"type": "object", "properties": {}, "required": []}

    def __init__(
        self,
        *,
        store: PortfolioStore,
        price_reader: PortfolioMarketReader | None = None,
    ) -> None:
        self._store = store
        self._price_reader = price_reader

    def as_tool(self) -> Tool:
        return Tool(
            name=self.name,
            description=self.description,
            input_schema=self.input_schema,
            run=self.run,
        )

    def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        try:
            state = self._store.load(actor_id=ctx.actor_id)
        except Exception as exc:
            return ToolResult(ok=False, content="", error=_failure_code(exc))
        rows: list[dict[str, Any]] = []
        for holding in state.holdings.values():
            row: dict[str, Any] = {
                "symbol": holding.symbol,
                "name": holding.name,
                "quantity": str(holding.quantity),
                "average_cost": str(holding.average_cost),
                "currency": "USD",
                "state": holding.state,
            }
            if self._price_reader is not None:
                observation = self._price_reader.observe_price(holding.symbol)
                if (
                    observation.availability == "available"
                    and observation.price is not None
                ):
                    row["current_price"] = str(observation.price)
                    row["price_as_of"] = (
                        observation.as_of.isoformat()
                        if observation.as_of is not None
                        else None
                    )
                    row["price_feed"] = observation.feed
                    row["delay_seconds"] = observation.delay_seconds
            rows.append(row)
        return ToolResult(
            ok=True,
            content=json.dumps(
                {
                    "holdings": rows,
                    "count": len(rows),
                    "usd_cash": str(state.usd_cash),
                },
                ensure_ascii=False,
            ),
        )


def _failure_code(exc: Exception) -> str:
    code = getattr(exc, "code", None)
    if isinstance(code, str) and code:
        return code[:80]
    return type(exc).__name__
