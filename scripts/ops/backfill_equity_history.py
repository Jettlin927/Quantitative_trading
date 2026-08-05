#!/usr/bin/env python3
"""把过去 N 个 ET 交易日按当前持仓基准回填权益快照。

用途：个人持仓页的权益日线需要历史数据；系统只在每次打开页面时记录当天
快照，历史日期需要回填。本脚本以当前持仓（数量/均价/现金）为基准，取每个
交易日的 EOD 复权收盘价计算组合权益并 upsert 到 personal_equity_snapshots。

口径与边界：
- 交易日按 America/New_York 自然日（周一至周五），不包含美股节假日判断；
- 价格优先 provider_adjusted（复权）收盘，缺失时回退 raw；
- 某日任一活跃持仓缺失收盘价时，该日整体跳过（不写部分权益）；
- 同日已存在的快照会被 EOD 口径覆盖（幂等 upsert）。

运行环境：生产 api 容器（已挂载 keyring / Alpaca 凭据，env 已配置）。
用法：
  python -m scripts.ops.backfill_equity_history --days 7 --dry-run
  python -m scripts.ops.backfill_equity_history --days 7
"""

from __future__ import annotations

import argparse
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
import os
import sys
from typing import Callable, Sequence
from zoneinfo import ZoneInfo

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app.personal_workspace.contracts import PersonalActor
from backend.app.personal_workspace.crypto import load_keyring_file, PersonalDataCipher
from backend.app.personal_workspace.market_runtime import load_personal_market_readers
from backend.app.personal_workspace.portfolio import (
    EquitySnapshot,
    HoldingState,
    PostgresEquitySnapshotStore,
    PostgresPortfolioStore,
)

US_MARKET_TZ = ZoneInfo("America/New_York")


def et_trading_days(end: date, count: int) -> list[date]:
    """返回截止 end（含）的最近 count 个 ET 工作日（自然日周一至周五）。"""
    days: list[date] = []
    cursor = end
    while len(days) < count:
        if cursor.weekday() < 5:
            days.append(cursor)
        cursor -= timedelta(days=1)
    return list(reversed(days))


def price_close_for_day(
    market,
    symbol: str,
    day: date,
    *,
    fetched_at: datetime,
) -> Decimal | None:
    """取某标的某交易日 EOD 收盘价：优先复权，缺失回退 raw。"""
    observed = market.observe_daily_bars(
        symbol,
        start_date=day,
        end_date=day,
        fetched_at=fetched_at,
        purpose="display",
    )
    adjusted = observed.provider_adjusted
    if adjusted.availability == "available" and adjusted.value:
        closes = [bar.close for bar in adjusted.value if bar.trade_date == day]
        if closes:
            return closes[-1]
    raw = observed.raw
    if raw.availability == "available" and raw.value:
        closes = [bar.close for bar in raw.value if bar.trade_date == day]
        if closes:
            return closes[-1]
    return None


def build_equity_snapshots(
    *,
    holdings: Sequence[HoldingState],
    usd_cash: Decimal,
    trading_days: Sequence[date],
    close_provider: Callable[[str, date], Decimal | None],
    observed_at: datetime,
) -> tuple[EquitySnapshot, ...]:
    """按日计算组合权益。某日任一持仓缺价则跳过该日。"""
    snapshots: list[EquitySnapshot] = []
    for day in trading_days:
        closes: dict[str, Decimal] = {}
        for holding in holdings:
            close = close_provider(holding.symbol, day)
            if close is None:
                closes = {}
                break
            closes[holding.symbol] = close
        if not closes:
            print(f"  跳过 {day.isoformat()}：存在缺失收盘价", file=sys.stderr)
            continue
        total_market = sum(
            (holding.quantity * closes[holding.symbol] for holding in holdings),
            Decimal("0"),
        )
        snapshots.append(
            EquitySnapshot(
                market_day=day,
                total_equity=total_market + usd_cash,
                total_market_value=total_market,
                usd_cash=usd_cash,
                holdings_count=len(holdings),
                priced_count=len(holdings),
                after_close=True,
                observed_at=observed_at,
                payload={
                    "holdings": [
                        {
                            "symbol": holding.symbol,
                            "quantity": str(holding.quantity),
                            "average_cost": str(holding.average_cost),
                        }
                        for holding in holdings
                    ],
                    "prices": {
                        holding.symbol: {
                            "price": str(closes[holding.symbol]),
                            "feed": "eod",
                            "as_of": datetime.combine(
                                day, time(20, 0), tzinfo=US_MARKET_TZ
                            ).isoformat(),
                            "cached": False,
                        }
                        for holding in holdings
                    },
                },
            )
        )
    return tuple(snapshots)


def main() -> int:
    parser = argparse.ArgumentParser(description="按当前持仓基准回填历史权益快照")
    parser.add_argument("--days", type=int, default=7, help="回填的 ET 交易日数量")
    parser.add_argument("--dry-run", action="store_true", help="只计算并打印，不写库")
    parser.add_argument(
        "--end-date",
        type=date.fromisoformat,
        default=None,
        help="截止 ET 交易日（默认今天）",
    )
    args = parser.parse_args()

    now = datetime.now(timezone.utc)
    et_now = now.astimezone(US_MARKET_TZ)
    end = args.end_date or (
        et_now.date() - timedelta(days=1)
        if et_now.time() < time(16, 0)
        else et_now.date()
    )
    trading_days = et_trading_days(end, args.days)
    print(f"回填 {len(trading_days)} 个交易日: {trading_days[0]} .. {trading_days[-1]}")

    actor = PersonalActor(actor_id="local-owner")
    session_factory = sessionmaker(
        bind=create_engine(os.environ["PRIVATE_DATABASE_URL"], pool_pre_ping=True),
        autoflush=False,
        expire_on_commit=False,
    )
    cipher = PersonalDataCipher(load_keyring_file(os.environ["PERSONAL_DATA_KEYRING_FILE"]))
    store = PostgresPortfolioStore(session_factory, cipher=cipher)
    state = store.load(actor_id=actor.actor_id)
    holdings = [h for h in state.holdings.values() if h.state == "active"]
    if not holdings:
        print("当前没有活跃持仓，无需回填", file=sys.stderr)
        return 1
    print(
        "基准持仓: "
        + ", ".join(f"{h.symbol} x{h.quantity} @ {h.average_cost}" for h in holdings)
        + f" | 现金 {state.usd_cash}"
    )

    readers = load_personal_market_readers(
        credentials_file=os.environ.get("ALPACA_CREDENTIALS_FILE", ""),
        authorization_file=os.environ.get("ALPACA_AUTHORIZATION_FILE", ""),
    )

    def close_for(symbol: str, day: date) -> Decimal | None:
        return price_close_for_day(
            readers.market, symbol, day, fetched_at=now
        )

    snapshots = build_equity_snapshots(
        holdings=holdings,
        usd_cash=state.usd_cash,
        trading_days=trading_days,
        close_provider=close_for,
        observed_at=now,
    )
    if not snapshots:
        print("没有可回填的交易日", file=sys.stderr)
        return 1

    print(f"{'日期':<12}{'权益':>14}{'持仓市值':>14}{'现金':>12}{'覆盖':>7}")
    for item in snapshots:
        print(
            f"{item.market_day.isoformat():<12}"
            f"{item.total_equity:>14}{item.total_market_value:>14}"
            f"{item.usd_cash:>12}"
            f"{item.priced_count}/{item.holdings_count:>5}"
        )

    if args.dry_run:
        print("dry-run：未写库")
        return 0

    snapshot_store = PostgresEquitySnapshotStore(session_factory, cipher=cipher)
    for item in snapshots:
        snapshot_store.upsert(actor_id=actor.actor_id, snapshot=item)
        print(f"已写入 {item.market_day.isoformat()} 权益 {item.total_equity}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
