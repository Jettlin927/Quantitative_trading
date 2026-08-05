from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timezone
from functools import lru_cache
from hashlib import sha256
from zoneinfo import ZoneInfo

import exchange_calendars as xcals

from .contracts import PersonalActor
from .portfolio import PortfolioBook
from .rules import ObservationRuleBook, RuleEvaluationRequest


_US_MARKET_TZ = ZoneInfo("America/New_York")
_PRE_MARKET_START = time(4, 0)
_POST_MARKET_END = time(20, 0)


@lru_cache(maxsize=1)
def _xnys_calendar():
    return xcals.get_calendar("XNYS")


def personal_rule_evaluation_slot(as_of: datetime) -> str | None:
    if as_of.tzinfo is None:
        raise ValueError("as_of_requires_timezone")
    market_time = as_of.astimezone(_US_MARKET_TZ)
    session_date = market_time.date()
    calendar = _xnys_calendar()
    if not calendar.is_session(session_date):
        return None
    local_time = market_time.time().replace(tzinfo=None)
    market_open = calendar.session_open(session_date).to_pydatetime()
    market_close = calendar.session_close(session_date).to_pydatetime()
    observed_at = as_of.astimezone(timezone.utc)
    if _PRE_MARKET_START <= local_time and observed_at < market_open:
        session = "pre_market"
    elif market_open <= observed_at < market_close:
        session = "regular_market"
    elif market_close <= observed_at and local_time < _POST_MARKET_END:
        session = "post_market"
    else:
        return None
    return f"{session_date.isoformat()}:{session}"


@dataclass(frozen=True)
class HoldingRuleAutomationResult:
    as_of: datetime
    evaluated_symbols: tuple[str, ...]
    evaluation_count: int
    failed_symbols: tuple[str, ...] = ()


class HoldingRuleAutomation:
    def __init__(
        self,
        *,
        portfolio: PortfolioBook,
        rules: ObservationRuleBook,
    ) -> None:
        self._portfolio = portfolio
        self._rules = rules

    def run_once(
        self, actor: PersonalActor, *, as_of: datetime
    ) -> HoldingRuleAutomationResult:
        schedule_slot = personal_rule_evaluation_slot(as_of)
        if schedule_slot is None:
            raise ValueError("personal_rule_evaluation_outside_market_sessions")
        active_symbols = set(self._portfolio.active_symbols(actor))
        enabled = tuple(
            rule
            for rule in self._rules.open(actor)["rules"]
            if rule.state == "enabled" and rule.symbol in active_symbols
        )
        symbols = tuple(sorted({rule.symbol for rule in enabled}))
        evaluation_count = 0
        evaluated_symbols = []
        failed_symbols = []
        for symbol in symbols:
            identity = "|".join(
                f"{rule.rule_id}:{rule.revision}"
                for rule in enabled
                if rule.symbol == symbol
            )
            revision_fingerprint = sha256(identity.encode("utf-8")).hexdigest()[:16]
            try:
                batch = self._rules.evaluate(
                    actor,
                    RuleEvaluationRequest(symbol=symbol, as_of=as_of),
                    idempotency_key=(
                        f"holding-rule-auto:{schedule_slot}:{symbol}:{revision_fingerprint}"
                    ),
                )
            except Exception:
                failed_symbols.append(symbol)
                continue
            evaluation_count += len(batch.evaluations)
            evaluated_symbols.append(symbol)
        return HoldingRuleAutomationResult(
            as_of=as_of,
            evaluated_symbols=tuple(evaluated_symbols),
            evaluation_count=evaluation_count,
            failed_symbols=tuple(failed_symbols),
        )
