"""legacy get_news 兼容 adapter；来源 I/O 统一委托 fact_news module。"""

from __future__ import annotations

import json
from typing import Any

from ..fact_news import FactNewsReadContext, InvestmentNewsReader
from ..protocol import Tool, ToolContext, ToolResult


class GetNewsTool:
    name = "get_news"
    description = (
        "检索目标标的或产业赛道最近 7 天的产业新闻（investment-news 本地抓取，覆盖全球 100+ 权威源）。"
        "参数：symbol（美股代码，可选）、keyword（关键词，可选）、sector（赛道 key，可选，"
        "取值 ai/semi/robot/auto/energy/bio/space/security/tech/consumer/macro/science）、"
        "limit（返回条数，默认 8，最大 20）。标的→赛道为启发式映射，未收录标的使用关键词检索。"
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "symbol": {"type": "string"},
            "keyword": {"type": "string"},
            "sector": {"type": "string"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 20},
        },
        "required": [],
    }

    def __init__(self, *, reader: InvestmentNewsReader | None) -> None:
        self._reader = reader

    def as_tool(self) -> Tool:
        return Tool(
            name=self.name,
            description=self.description,
            input_schema=self.input_schema,
            run=self.run,
        )

    def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        if self._reader is None:
            return ToolResult(ok=False, content="", error="news_unavailable")
        symbol = str(args.get("symbol") or "").strip() or None
        keyword = str(args.get("keyword") or "").strip() or None
        sector = str(args.get("sector") or "").strip() or None
        try:
            limit = int(args.get("limit", 8))
        except (TypeError, ValueError):
            limit = 8
        limit = max(1, min(20, limit))
        try:
            items = self._reader.search(
                context=FactNewsReadContext(
                    permissions=frozenset({"news:read"}), purpose="domain_tool"
                ),
                symbol=symbol, keyword=keyword, sector=sector, limit=limit
            )
        except (RuntimeError, OSError, ValueError) as exc:
            code = getattr(exc, "code", None)
            if isinstance(code, str) and code:
                error = code[:80]
            elif isinstance(exc, RuntimeError) and str(exc):
                error = str(exc)[:80]
            elif isinstance(exc, OSError):
                error = "news_data_invalid"
            else:
                error = type(exc).__name__
            return ToolResult(ok=False, content="", error=error)
        if not items:
            return ToolResult(
                ok=True,
                content=json.dumps(
                    {"items": [], "count": 0, "note": "未找到匹配新闻（可换关键词或赛道重试）"},
                    ensure_ascii=False,
                ),
            )
        return ToolResult(
            ok=True,
            content=json.dumps(
                {
                    "items": items,
                    "count": len(items),
                    "note": "标的→赛道为启发式映射；条目为最近 7 天抓取快照",
                },
                ensure_ascii=False,
            ),
        )
