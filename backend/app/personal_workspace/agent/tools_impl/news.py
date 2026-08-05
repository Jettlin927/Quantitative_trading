"""get_news 工具：通过 investment-news 项目（本地子进程）检索美股产业新闻。

本工具以子进程方式运行外部项目的 ``scripts/fetch.py``（argv 列表、无 shell），
解析 ``data.js`` 后按美股 symbol、关键词和产业主题做启发式检索。数据与本仓库
完全解耦：不 vendor 代码，仅依赖显式配置的 checkout 目录。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
from typing import Any, Callable

from ..protocol import Tool, ToolContext, ToolResult

INVESTMENT_NEWS_MARKER = "window.DATA ="
DEFAULT_CACHE_TTL_SECONDS = 1800
DEFAULT_FETCH_TIMEOUT_SECONDS = 240

# 标的 → 赛道（investment-news 的 sector key）启发式映射；未收录标的走关键词检索。
SYMBOL_SECTORS: dict[str, tuple[str, ...]] = {
    "NVDA": ("ai", "semi"),
    "AMD": ("semi",),
    "INTC": ("semi",),
    "TSM": ("semi",),
    "ASML": ("semi",),
    "AVGO": ("semi", "ai"),
    "MU": ("semi",),
    "QCOM": ("semi", "consumer"),
    "MSFT": ("ai", "tech"),
    "GOOGL": ("ai", "tech"),
    "GOOG": ("ai", "tech"),
    "META": ("ai", "tech"),
    "AMZN": ("ai", "tech", "consumer"),
    "AAPL": ("consumer", "tech"),
    "TSLA": ("auto", "energy"),
    "NIO": ("auto",),
    "XPEV": ("auto",),
    "LI": ("auto",),
    "RIVN": ("auto",),
    "ORCL": ("tech",),
    "CRM": ("tech",),
    "ADBE": ("tech",),
    "PLTR": ("ai", "security"),
    "CRWD": ("security",),
    "FTNT": ("security",),
    "LLY": ("bio",),
    "PFE": ("bio",),
    "MRK": ("bio",),
    "JNJ": ("bio",),
    "MRNA": ("bio",),
    "UNH": ("bio",),
    "XOM": ("energy",),
    "CVX": ("energy",),
    "NEE": ("energy",),
    "ENPH": ("energy",),
    "F": ("auto",),
    "GM": ("auto",),
}


@dataclass(frozen=True)
class _NewsReaderConfig:
    checkout_dir: Path
    cache_ttl_seconds: int = DEFAULT_CACHE_TTL_SECONDS
    fetch_timeout_seconds: int = DEFAULT_FETCH_TIMEOUT_SECONDS
    python_executable: str | None = None
    runner: Callable[[list[str], Path], int] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "checkout_dir", Path(self.checkout_dir))


class InvestmentNewsReader:
    """investment-news 本地目录读取器：TTL 缓存刷新 + data.js 解析 + 启发式检索。"""

    def __init__(
        self,
        checkout_dir: Path | str,
        *,
        cache_ttl_seconds: int = DEFAULT_CACHE_TTL_SECONDS,
        fetch_timeout_seconds: int = DEFAULT_FETCH_TIMEOUT_SECONDS,
        python_executable: str | None = None,
        runner: Callable[[list[str], Path], int] | None = None,
    ) -> None:
        self._config = _NewsReaderConfig(
            checkout_dir=Path(checkout_dir),
            cache_ttl_seconds=cache_ttl_seconds,
            fetch_timeout_seconds=fetch_timeout_seconds,
            python_executable=python_executable,
            runner=runner,
        )
        self._lock = threading.Lock()
        self._last_refresh: datetime | None = None

    @property
    def checkout_dir(self) -> Path:
        return self._config.checkout_dir

    def available(self) -> bool:
        return (
            self.checkout_dir.is_dir()
            and (self.checkout_dir / "data.js").is_file()
        )

    def refresh(self, *, now: datetime | None = None) -> None:
        """TTL 内不重复抓取；抓取失败保留旧数据（由调用方处理 stale）。

        新鲜度以 data.js 的 mtime 为准（跨进程、重启后依然生效）：mtime 在缓存
        窗口内则直接跳过子进程抓取；否则执行 fetch.py（成功后 data.js mtime 更新）。
        """
        config = self._config
        clock_now = now or datetime.now(timezone.utc)
        data_path = self.checkout_dir / "data.js"
        if self._data_is_fresh(data_path, clock_now, config.cache_ttl_seconds):
            self._last_refresh = clock_now
            return
        with self._lock:
            if self._data_is_fresh(data_path, clock_now, config.cache_ttl_seconds):
                self._last_refresh = clock_now
                return
            fetch_script = self.checkout_dir / "scripts" / "fetch.py"
            if not fetch_script.is_file():
                raise RuntimeError("news_checkout_invalid")
            python = config.python_executable or sys.executable
            env = dict(os.environ)
            env["PYTHONUTF8"] = "1"
            if config.runner is not None:
                return_code = config.runner(
                    [python, str(fetch_script)], self.checkout_dir
                )
            else:
                try:
                    completed = subprocess.run(
                        [python, str(fetch_script)],
                        cwd=self.checkout_dir,
                        env=env,
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        timeout=config.fetch_timeout_seconds,
                    )
                except subprocess.TimeoutExpired:
                    raise RuntimeError("news_fetch_timeout") from None
                return_code = completed.returncode
            self._last_refresh = clock_now
            if return_code != 0:
                raise RuntimeError("news_fetch_failed")

    @staticmethod
    def _data_is_fresh(
        data_path: Path, now: datetime, cache_ttl_seconds: int
    ) -> bool:
        if not data_path.is_file():
            return False
        try:
            mtime = datetime.fromtimestamp(
                data_path.stat().st_mtime, tz=timezone.utc
            )
        except OSError:
            return False
        return (now - mtime).total_seconds() < cache_ttl_seconds

    def load(self) -> dict[str, Any]:
        data_path = self.checkout_dir / "data.js"
        text = data_path.read_text(encoding="utf-8")
        marker_index = text.find(INVESTMENT_NEWS_MARKER)
        if marker_index < 0:
            raise RuntimeError("news_data_invalid")
        payload = text[marker_index + len(INVESTMENT_NEWS_MARKER):].strip()
        payload = payload.rstrip().rstrip(";").strip()
        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError:
            raise RuntimeError("news_data_invalid") from None
        if not isinstance(parsed, dict):
            raise RuntimeError("news_data_invalid")
        return parsed

    def search(
        self,
        *,
        symbol: str | None = None,
        keyword: str | None = None,
        sector: str | None = None,
        limit: int = 8,
    ) -> list[dict[str, Any]]:
        self.refresh()
        data = self.load()
        industries = data.get("industries")
        if not isinstance(industries, list):
            raise RuntimeError("news_data_invalid")
        scored: list[tuple[int, int, dict[str, Any]]] = []
        normalized_symbol = (symbol or "").strip().upper()
        normalized_keyword = (keyword or "").strip().lower()
        hint_sectors = SYMBOL_SECTORS.get(normalized_symbol, ()) if normalized_symbol else ()
        for industry in industries:
            if not isinstance(industry, dict):
                continue
            sector_key = str(industry.get("key", ""))
            if sector and sector_key != sector:
                continue
            items = industry.get("items")
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, dict):
                    continue
                enriched = dict(item)
                enriched["sector"] = sector_key
                enriched["sector_name"] = str(industry.get("name", ""))
                score = _relevance_score(
                    enriched,
                    normalized_symbol=normalized_symbol,
                    hint_sectors=hint_sectors,
                    keyword=normalized_keyword,
                )
                if score <= 0:
                    continue
                ts = enriched.get("ts")
                scored.append(
                    (score, int(ts) if isinstance(ts, int) else 0, enriched)
                )
        scored.sort(key=lambda entry: (entry[0], entry[1]), reverse=True)
        return [entry[2] for entry in scored[:limit]]


def _relevance_score(
    item: dict[str, Any],
    *,
    normalized_symbol: str,
    hint_sectors: tuple[str, ...],
    keyword: str,
) -> int:
    haystack = " ".join(
        str(item.get(field, ""))
        for field in ("title", "summary", "zh", "source")
    ).lower()
    score = 0
    if normalized_symbol:
        if normalized_symbol.lower() in haystack:
            score += 3
        if item.get("sector") in hint_sectors:
            score += 2
    if keyword and keyword in haystack:
        score += 3
    if not normalized_symbol and not keyword:
        score = 1
    return score


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
