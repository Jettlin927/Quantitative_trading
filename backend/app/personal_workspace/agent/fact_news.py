"""事实新闻来源：授权后刷新并解析 investment-news 本地快照。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
from typing import Any, Callable, Literal, Mapping, Protocol


INVESTMENT_NEWS_MARKER = "window.DATA ="
DEFAULT_CACHE_TTL_SECONDS = 1800
DEFAULT_FETCH_TIMEOUT_SECONDS = 240
FACT_NEWS_SOURCE = "investment_news"
FACT_NEWS_AUTHORIZATION_SNAPSHOT_ID = "investment-news-local-v1"
FACT_NEWS_ALLOWED_PURPOSES = frozenset({"domain_tool"})
FACT_NEWS_RETENTION: Literal["encrypted_payload"] = "encrypted_payload"
_FETCH_ENVIRONMENT_FIELDS = (
    "PATH",
    "LANG",
    "LC_ALL",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "REQUESTS_CA_BUNDLE",
    "CURL_CA_BUNDLE",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "NO_PROXY",
)

# 标的 → 赛道（investment-news 的 sector key）启发式映射。
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
class FactNewsReadContext:
    permissions: frozenset[str]
    purpose: str


@dataclass(frozen=True)
class FactNewsGap:
    code: str
    subject: str


@dataclass(frozen=True)
class RawFactNews:
    title: str
    url: str
    published_at: datetime
    fetched_at: datetime
    summary: str
    source: str
    source_type: str
    sector: str
    related_symbols: tuple[str, ...]


@dataclass(frozen=True)
class NewsSourceSnapshot:
    items: tuple[RawFactNews, ...]
    gaps: tuple[FactNewsGap, ...] = ()
    fetched_at: datetime | None = None
    source: str = FACT_NEWS_SOURCE
    authorization_snapshot_id: str = FACT_NEWS_AUTHORIZATION_SNAPSHOT_ID
    persistence: Literal["encrypted_payload", "metadata_only"] = FACT_NEWS_RETENTION
    allowed_purposes: frozenset[str] = FACT_NEWS_ALLOWED_PURPOSES

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "gaps",
            tuple(
                gap
                if isinstance(gap, FactNewsGap)
                else FactNewsGap(str(gap), FACT_NEWS_SOURCE)
                for gap in self.gaps
            ),
        )
        object.__setattr__(self, "allowed_purposes", frozenset(self.allowed_purposes))


class StructuredNewsSource(Protocol):
    def read(
        self, *, context: FactNewsReadContext, now: datetime
    ) -> NewsSourceSnapshot: ...


@dataclass(frozen=True)
class _NewsReaderConfig:
    checkout_dir: Path
    cache_ttl_seconds: int = DEFAULT_CACHE_TTL_SECONDS
    fetch_timeout_seconds: int = DEFAULT_FETCH_TIMEOUT_SECONDS
    python_executable: str | None = None
    runner: Callable[[list[str], Path, Mapping[str, str]], int] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "checkout_dir", Path(self.checkout_dir))


class InvestmentNewsReader:
    """investment-news 本地刷新与 data.js 解析边界。"""

    def __init__(
        self,
        checkout_dir: Path | str,
        *,
        cache_ttl_seconds: int = DEFAULT_CACHE_TTL_SECONDS,
        fetch_timeout_seconds: int = DEFAULT_FETCH_TIMEOUT_SECONDS,
        python_executable: str | None = None,
        runner: Callable[[list[str], Path, Mapping[str, str]], int] | None = None,
    ) -> None:
        self._config = _NewsReaderConfig(
            checkout_dir=Path(checkout_dir),
            cache_ttl_seconds=cache_ttl_seconds,
            fetch_timeout_seconds=fetch_timeout_seconds,
            python_executable=python_executable,
            runner=runner,
        )
        self._lock = threading.Lock()

    @property
    def checkout_dir(self) -> Path:
        return self._config.checkout_dir

    def available(self) -> bool:
        return self.checkout_dir.is_dir() and (self.checkout_dir / "data.js").is_file()

    def refresh(self, *, now: datetime | None = None) -> None:
        config = self._config
        clock_now = now or datetime.now(timezone.utc)
        data_path = self.checkout_dir / "data.js"
        if self._data_is_fresh(data_path, clock_now, config.cache_ttl_seconds):
            return
        with self._lock:
            if self._data_is_fresh(data_path, clock_now, config.cache_ttl_seconds):
                return
            fetch_script = self.checkout_dir / "scripts" / "fetch.py"
            if not fetch_script.is_file():
                raise RuntimeError("news_checkout_invalid")
            python = config.python_executable or sys.executable
            environment = {
                key: os.environ[key]
                for key in _FETCH_ENVIRONMENT_FIELDS
                if key in os.environ
            }
            environment["PYTHONUTF8"] = "1"
            if config.runner is not None:
                return_code = config.runner(
                    [python, str(fetch_script)], self.checkout_dir, environment
                )
            else:
                try:
                    completed = subprocess.run(
                        [python, str(fetch_script)],
                        cwd=self.checkout_dir,
                        env=environment,
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        timeout=config.fetch_timeout_seconds,
                    )
                except subprocess.TimeoutExpired:
                    raise RuntimeError("news_fetch_timeout") from None
                return_code = completed.returncode
            if return_code != 0:
                raise RuntimeError("news_fetch_failed")

    @staticmethod
    def _data_is_fresh(
        data_path: Path, now: datetime, cache_ttl_seconds: int
    ) -> bool:
        if not data_path.is_file():
            return False
        try:
            mtime = datetime.fromtimestamp(data_path.stat().st_mtime, tz=timezone.utc)
        except OSError:
            return False
        return (now - mtime).total_seconds() < cache_ttl_seconds

    def load(self) -> dict[str, Any]:
        text = (self.checkout_dir / "data.js").read_text(encoding="utf-8")
        marker_index = text.find(INVESTMENT_NEWS_MARKER)
        if marker_index < 0:
            raise RuntimeError("news_data_invalid")
        payload = text[marker_index + len(INVESTMENT_NEWS_MARKER) :].strip()
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
        context: FactNewsReadContext,
        symbol: str | None = None,
        keyword: str | None = None,
        sector: str | None = None,
        limit: int = 8,
    ) -> list[dict[str, Any]]:
        if "news:read" not in context.permissions:
            raise RuntimeError("source_unauthorized")
        if context.purpose not in FACT_NEWS_ALLOWED_PURPOSES:
            raise RuntimeError("source_purpose_denied")
        self.refresh()
        data = self.load()
        industries = data.get("industries")
        if not isinstance(industries, list):
            raise RuntimeError("news_data_invalid")
        scored: list[tuple[int, int, dict[str, Any]]] = []
        normalized_symbol = (symbol or "").strip().upper()
        normalized_keyword = (keyword or "").strip().lower()
        hint_sectors = SYMBOL_SECTORS.get(normalized_symbol, ())
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
                if score > 0:
                    timestamp = enriched.get("ts")
                    scored.append(
                        (score, int(timestamp) if isinstance(timestamp, int) else 0, enriched)
                    )
        scored.sort(key=lambda entry: (entry[0], entry[1]), reverse=True)
        return [entry[2] for entry in scored[:limit]]


class InvestmentNewsStructuredSource:
    """先授权，再把 investment-news 快照转换为 typed fact snapshot。"""

    def __init__(
        self,
        reader: InvestmentNewsReader,
        *,
        refresh_before_read: bool = True,
        authorization_snapshot_id: str = FACT_NEWS_AUTHORIZATION_SNAPSHOT_ID,
        persistence: Literal["encrypted_payload", "metadata_only"] = FACT_NEWS_RETENTION,
        allowed_purposes: frozenset[str] = FACT_NEWS_ALLOWED_PURPOSES,
    ) -> None:
        self._reader = reader
        self._refresh_before_read = refresh_before_read
        self._authorization_snapshot_id = authorization_snapshot_id
        self._persistence = persistence
        self._allowed_purposes = frozenset(allowed_purposes)

    def read(
        self, *, context: FactNewsReadContext, now: datetime
    ) -> NewsSourceSnapshot:
        base = {
            "source": FACT_NEWS_SOURCE,
            "authorization_snapshot_id": self._authorization_snapshot_id,
            "persistence": self._persistence,
            "allowed_purposes": self._allowed_purposes,
        }
        if "news:read" not in context.permissions:
            return NewsSourceSnapshot(
                items=(), gaps=(FactNewsGap("source_unauthorized", FACT_NEWS_SOURCE),), **base
            )
        if context.purpose not in self._allowed_purposes:
            return NewsSourceSnapshot(
                items=(), gaps=(FactNewsGap("source_purpose_denied", context.purpose),), **base
            )
        try:
            if self._refresh_before_read:
                self._reader.refresh(now=now)
            payload = self._reader.load()
            fetched_at = datetime.fromtimestamp(
                (self._reader.checkout_dir / "data.js").stat().st_mtime,
                tz=timezone.utc,
            )
        except (RuntimeError, OSError, ValueError) as exc:
            detail = str(exc) if isinstance(exc, RuntimeError) and str(exc) else type(exc).__name__
            return NewsSourceSnapshot(
                items=(), gaps=(FactNewsGap("source_unavailable", detail[:80]),), **base
            )
        industries = payload.get("industries")
        if not isinstance(industries, list):
            return NewsSourceSnapshot(
                items=(), gaps=(FactNewsGap("source_contract_invalid", "industries"),), **base
            )
        items: list[RawFactNews] = []
        gaps: list[FactNewsGap] = []
        for industry in industries:
            if not isinstance(industry, Mapping):
                gaps.append(FactNewsGap("source_contract_invalid", "industry"))
                continue
            sector = str(industry.get("key", "")).strip()
            raw_items = industry.get("items")
            if not isinstance(raw_items, list):
                gaps.append(FactNewsGap("source_contract_invalid", sector or "items"))
                continue
            for item in raw_items:
                try:
                    normalized = _raw_investment_news_item(
                        item, sector=sector, fetched_at=fetched_at
                    )
                except (OSError, OverflowError, ValueError):
                    normalized = None
                if normalized is None:
                    gaps.append(FactNewsGap("source_contract_invalid", sector or "item"))
                else:
                    items.append(normalized)
        return NewsSourceSnapshot(
            items=tuple(items),
            gaps=tuple(dict.fromkeys(gaps)),
            fetched_at=fetched_at,
            **base,
        )


_SYMBOL_TERMS: Mapping[str, tuple[str, ...]] = {
    "NVDA": ("nvda", "nvidia", "英伟达"),
    "AMD": ("amd", "advanced micro devices", "超威"),
    "TSM": ("tsm", "tsmc", "台积电"),
    "ASML": ("asml", "阿斯麦"),
    "MSFT": ("msft", "microsoft", "微软"),
    "GOOGL": ("googl", "google", "alphabet", "谷歌"),
    "META": ("meta", "facebook", "脸书"),
    "AMZN": ("amzn", "amazon", "亚马逊"),
    "AAPL": ("aapl", "apple", "苹果"),
    "TSLA": ("tsla", "tesla", "特斯拉"),
}


def _raw_investment_news_item(
    item: Any, *, sector: str, fetched_at: datetime
) -> RawFactNews | None:
    if not isinstance(item, Mapping):
        return None
    timestamp = item.get("ts")
    if not isinstance(timestamp, (int, float)):
        return None
    title = str(item.get("title", "")).strip()
    summary = str(item.get("summary", "")).strip()
    source = str(item.get("source", "")).strip()
    url = str(item.get("url", "")).strip()
    if not title or not summary or not source or not url:
        return None
    haystack = " ".join((title, summary, str(item.get("zh", "")))).lower()
    explicit = item.get("symbols", ())
    symbols = set()
    if isinstance(explicit, (list, tuple)):
        symbols = {
            str(value).strip().upper() for value in explicit if str(value).strip()
        }
    for symbol, terms in _SYMBOL_TERMS.items():
        if any(term in haystack for term in terms):
            symbols.add(symbol)
    return RawFactNews(
        title=title,
        url=url,
        published_at=datetime.fromtimestamp(timestamp, tz=timezone.utc),
        fetched_at=fetched_at,
        summary=summary,
        source=source,
        source_type="structured_news",
        sector=sector,
        related_symbols=tuple(sorted(symbols)),
    )


def _relevance_score(
    item: dict[str, Any],
    *,
    normalized_symbol: str,
    hint_sectors: tuple[str, ...],
    keyword: str,
) -> int:
    haystack = " ".join(
        str(item.get(field, "")) for field in ("title", "summary", "zh", "source")
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
