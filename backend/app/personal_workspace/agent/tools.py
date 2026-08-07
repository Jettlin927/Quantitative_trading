"""内置工具装配：持仓 / K 线 / 新闻。"""

from __future__ import annotations

from ...market_observation.alpaca import AlpacaMarketObservationAdapter
from ..portfolio import PortfolioMarketReader
from .protocol import Tool
from .tools_impl.holdings import GetHoldingsTool, HoldingsReader
from .tools_impl.kline import GetKlineTool
from .tools_impl.news import GetNewsTool, InvestmentNewsReader


def build_agent_tools(
    *,
    portfolio_store: HoldingsReader,
    price_reader: PortfolioMarketReader | None = None,
    market_adapter: AlpacaMarketObservationAdapter | None = None,
    news_reader: InvestmentNewsReader | None = None,
) -> tuple[Tool, ...]:
    """装配三个内置工具。任一数据源未配置时对应工具降级为可用但失败（错误码回灌模型）。"""
    return (
        GetHoldingsTool(store=portfolio_store, price_reader=price_reader).as_tool(),
        GetKlineTool(adapter=market_adapter).as_tool(),
        GetNewsTool(reader=news_reader).as_tool(),
    )
