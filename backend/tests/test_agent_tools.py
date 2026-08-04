from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import json
import os
import tempfile
import unittest
from pathlib import Path

from backend.app.market_observation.contracts import (
    DailyBar,
    DailyBarsObservation,
    ObservedValue,
)
from backend.app.personal_workspace.agent.protocol import ToolContext
from backend.app.personal_workspace.agent.tools import build_agent_tools
from backend.app.personal_workspace.agent.tools_impl.holdings import GetHoldingsTool
from backend.app.personal_workspace.agent.tools_impl.kline import GetKlineTool
from backend.app.personal_workspace.agent.tools_impl.news import (
    GetNewsTool,
    InvestmentNewsReader,
)
from backend.app.personal_workspace.analysis import AnalysisIntent
from backend.app.personal_workspace.portfolio import (
    HoldingState,
    InMemoryPortfolioStore,
    PortfolioPriceObservation,
    PortfolioState,
)

NOW = datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc)


def make_context(actor_id: str = "actor-1") -> ToolContext:
    return ToolContext(
        actor_id=actor_id,
        intent=AnalysisIntent(question="测试", subject_ids=("NVDA",)),
        clock=lambda: NOW,
    )


class FakePriceReader:
    def __init__(self, price: str | None = "123.45") -> None:
        self._price = price

    def observe_price(self, symbol: str) -> PortfolioPriceObservation:
        if self._price is None:
            return PortfolioPriceObservation.unavailable("provider_timeout")
        return PortfolioPriceObservation.available(
            price=Decimal(self._price),
            source_health="fresh",
            as_of=NOW,
            feed="iex",
            delay_seconds=15,
            source_ids=(),
        )


class FakeBarsAdapter:
    def __init__(self, *, raise_error: Exception | None = None) -> None:
        self._raise_error = raise_error
        self.calls: list[dict] = []

    def observe_daily_bars(self, symbol: str, **kwargs):
        self.calls.append({"symbol": symbol, **kwargs})
        if self._raise_error is not None:
            raise self._raise_error
        bar = DailyBar(
            symbol=symbol,
            trade_date=date(2026, 8, 9),
            open=Decimal("100"),
            high=Decimal("110"),
            low=Decimal("99"),
            close=Decimal("108.5"),
            volume=1000,
        )
        observed = ObservedValue(
            availability="available",
            value=(bar,),
            reason_code=None,
            source_health="fresh",
            as_of=NOW,
            provenance=None,
        )
        return DailyBarsObservation(raw=observed, provider_adjusted=observed)


class GetHoldingsToolTest(unittest.TestCase):
    def test_returns_holdings_with_price_when_reader_available(self) -> None:
        store = InMemoryPortfolioStore()
        store._states["actor-1"] = PortfolioState(
            workspace_id="w1",
            revision=1,
            usd_cash=Decimal("1000.5"),
            holdings={
                "h1": HoldingState(
                    holding_id="h1",
                    symbol="NVDA",
                    name="NVIDIA",
                    quantity=Decimal("10"),
                    average_cost=Decimal("90"),
                )
            },
        )
        tool = GetHoldingsTool(store=store, price_reader=FakePriceReader())
        result = tool.run(make_context(), {})
        self.assertTrue(result.ok)
        payload = json.loads(result.content)
        self.assertEqual(payload["count"], 1)
        holding = payload["holdings"][0]
        self.assertEqual(holding["symbol"], "NVDA")
        self.assertEqual(holding["quantity"], "10")
        self.assertEqual(holding["average_cost"], "90")
        self.assertEqual(holding["current_price"], "123.45")
        self.assertEqual(payload["usd_cash"], "1000.5")

    def test_omits_price_when_unavailable(self) -> None:
        store = InMemoryPortfolioStore()
        store._states["actor-1"] = PortfolioState(
            workspace_id="w1",
            revision=1,
            usd_cash=Decimal("0"),
            holdings={
                "h1": HoldingState(
                    holding_id="h1",
                    symbol="TSLA",
                    name="Tesla",
                    quantity=Decimal("2"),
                    average_cost=Decimal("200"),
                )
            },
        )
        tool = GetHoldingsTool(store=store, price_reader=FakePriceReader(price=None))
        result = tool.run(make_context(), {})
        payload = json.loads(result.content)
        self.assertNotIn("current_price", payload["holdings"][0])

    def test_empty_holdings_is_ok(self) -> None:
        store = InMemoryPortfolioStore()
        tool = GetHoldingsTool(store=store)
        result = tool.run(make_context(), {})
        self.assertTrue(result.ok)
        self.assertEqual(json.loads(result.content)["count"], 0)


class GetKlineToolTest(unittest.TestCase):
    def test_requests_ai_context_purpose_and_returns_bars(self) -> None:
        adapter = FakeBarsAdapter()
        tool = GetKlineTool(adapter=adapter)
        result = tool.run(make_context(), {"symbol": "nvda", "days": 60, "limit": 5})
        self.assertTrue(result.ok)
        self.assertEqual(adapter.calls[0]["purpose"], "ai_context")
        self.assertEqual(adapter.calls[0]["symbol"], "NVDA")
        payload = json.loads(result.content)
        self.assertEqual(payload["symbol"], "NVDA")
        self.assertEqual(payload["bars"][0]["close"], "108.5")
        self.assertEqual(payload["bars"][0]["date"], "2026-08-09")

    def test_missing_symbol_is_invalid(self) -> None:
        tool = GetKlineTool(adapter=FakeBarsAdapter())
        result = tool.run(make_context(), {})
        self.assertFalse(result.ok)
        self.assertEqual(result.error, "invalid_args")

    def test_unconfigured_adapter_fails_closed(self) -> None:
        tool = GetKlineTool(adapter=None)
        result = tool.run(make_context(), {"symbol": "NVDA"})
        self.assertFalse(result.ok)
        self.assertEqual(result.error, "kline_unavailable")

    def test_authorization_denied_is_reported(self) -> None:
        tool = GetKlineTool(adapter=FakeBarsAdapter(raise_error=PermissionError("denied")))
        result = tool.run(make_context(), {"symbol": "NVDA"})
        self.assertFalse(result.ok)
        self.assertEqual(result.error, "authorization_denied")

    def test_market_error_code_is_reported(self) -> None:
        from backend.app.market_observation.alpaca import MarketObservationError

        tool = GetKlineTool(
            adapter=FakeBarsAdapter(
                raise_error=MarketObservationError("provider_pagination_incomplete")
            )
        )
        result = tool.run(make_context(), {"symbol": "NVDA"})
        self.assertFalse(result.ok)
        self.assertEqual(result.error, "provider_pagination_incomplete")


NEWS_FIXTURE = """// data.js —— 测试夹具
window.DATA = {
  "generated_at": "2026-08-09 09:00",
  "recent_days": 7,
  "industries": [
    {"key": "semi", "name": "半导体 / 芯片", "total": 2, "items": [
      {"title": "NVIDIA 发布新一代 AI 芯片", "url": "https://example.com/1",
       "time": "08-09 08:00", "ts": 1783600000, "summary": "NVIDIA 新芯片量产。",
       "source": "DIGITIMES", "zh": "英伟达发布新芯片"},
      {"title": "台积电 2nm 良率提升", "url": "https://example.com/2",
       "time": "08-08 20:00", "ts": 1783500000, "summary": "TSMC 2nm 进展。",
       "source": "SemiAnalysis", "zh": "台积电先进制程"}
    ]},
    {"key": "auto", "name": "汽车 / 新能源车", "total": 1, "items": [
      {"title": "特斯拉上海工厂扩产", "url": "https://example.com/3",
       "time": "08-07 12:00", "ts": 1783400000, "summary": "TSLA 扩产。",
       "source": "Electrek", "zh": "特斯拉扩产"}
    ]}
  ]
}
"""


class GetNewsToolTest(unittest.TestCase):
    def _make_reader(self, runner, *, checkout_dir=None) -> InvestmentNewsReader:
        if checkout_dir is None:
            checkout_dir = tempfile.mkdtemp()
        root = Path(checkout_dir)
        (root / "scripts").mkdir(parents=True, exist_ok=True)
        (root / "scripts" / "fetch.py").write_text("#!/usr/bin/env python3\n", encoding="utf-8")
        data_path = root / "data.js"
        data_path.write_text(NEWS_FIXTURE, encoding="utf-8")
        # 数据老化到 TTL 窗口之外，确保测试走抓取路径（相对真实时钟）
        old_mtime = (datetime.now(timezone.utc) - timedelta(hours=2)).timestamp()
        os.utime(data_path, (old_mtime, old_mtime))
        return InvestmentNewsReader(root, runner=runner, cache_ttl_seconds=3600)

    def test_search_by_symbol_matches_sector_and_keyword(self) -> None:
        reader = self._make_reader(runner=lambda argv, cwd: 0)
        result = build_agent_tools(
            portfolio_store=InMemoryPortfolioStore(), news_reader=reader
        )[2].run(make_context(), {"symbol": "NVDA", "limit": 5})
        self.assertTrue(result.ok)
        payload = json.loads(result.content)
        self.assertGreaterEqual(payload["count"], 1)
        self.assertEqual(payload["items"][0]["sector"], "semi")

    def test_search_by_keyword(self) -> None:
        reader = self._make_reader(runner=lambda argv, cwd: 0)
        tool = GetNewsTool(reader=reader).as_tool()
        result = tool.run(make_context(), {"keyword": "台积电"})
        payload = json.loads(result.content)
        self.assertEqual(payload["count"], 1)
        self.assertIn("台积电", payload["items"][0]["title"])

    def test_search_by_sector_filter(self) -> None:
        reader = self._make_reader(runner=lambda argv, cwd: 0)
        tool = GetNewsTool(reader=reader).as_tool()
        result = tool.run(make_context(), {"sector": "auto"})
        payload = json.loads(result.content)
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["items"][0]["sector"], "auto")

    def test_fetch_failure_keeps_stale_data_but_returns_error(self) -> None:
        root = Path(tempfile.mkdtemp())
        (root / "scripts").mkdir(parents=True)
        (root / "scripts" / "fetch.py").write_text("#!/usr/bin/env python3\n", encoding="utf-8")
        data_path = root / "data.js"
        data_path.write_text(NEWS_FIXTURE, encoding="utf-8")
        old_mtime = (datetime.now(timezone.utc) - timedelta(hours=2)).timestamp()
        os.utime(data_path, (old_mtime, old_mtime))
        reader = InvestmentNewsReader(root, runner=lambda argv, cwd: 1, cache_ttl_seconds=3600)
        tool = GetNewsTool(reader=reader).as_tool()
        result = tool.run(make_context(), {"keyword": "NVIDIA"})
        self.assertFalse(result.ok)
        self.assertEqual(result.error, "news_fetch_failed")

    def test_fresh_data_skips_fetch_within_ttl(self) -> None:
        """data.js mtime 在 TTL 窗口内时跳过子进程抓取（跨进程/重启生效）。"""
        root = Path(tempfile.mkdtemp())
        (root / "scripts").mkdir(parents=True)
        (root / "scripts" / "fetch.py").write_text("#!/usr/bin/env python3\n", encoding="utf-8")
        (root / "data.js").write_text(NEWS_FIXTURE, encoding="utf-8")
        calls: list[tuple] = []
        reader = InvestmentNewsReader(
            root,
            runner=lambda argv, cwd: calls.append((argv, cwd)) or 0,
            cache_ttl_seconds=3600,
        )
        tool = GetNewsTool(reader=reader).as_tool()
        result = tool.run(make_context(), {"keyword": "NVIDIA"})
        self.assertTrue(result.ok)
        self.assertEqual(json.loads(result.content)["count"], 1)
        self.assertEqual(calls, [], "数据新鲜时不应触发子进程抓取")

    def test_unconfigured_reader_fails_closed(self) -> None:
        tool = GetNewsTool(reader=None).as_tool()
        result = tool.run(make_context(), {"keyword": "NVIDIA"})
        self.assertFalse(result.ok)
        self.assertEqual(result.error, "news_unavailable")

    def test_missing_data_file_is_error(self) -> None:
        root = Path(tempfile.mkdtemp())
        (root / "scripts").mkdir(parents=True)
        (root / "scripts" / "fetch.py").write_text("#!/usr/bin/env python3\n", encoding="utf-8")
        reader = InvestmentNewsReader(root, runner=lambda argv, cwd: 0, cache_ttl_seconds=3600)
        tool = GetNewsTool(reader=reader).as_tool()
        result = tool.run(make_context(), {"keyword": "NVIDIA"})
        self.assertFalse(result.ok)
        self.assertEqual(result.error, "news_data_invalid")


if __name__ == "__main__":
    unittest.main()
