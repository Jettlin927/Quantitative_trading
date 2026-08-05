from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256

from .contracts import PersonalActor
from .portfolio import PortfolioBook
from .rules import ObservationRuleBook, RuleEvaluationRequest


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
        schedule_interval_seconds: float = 900,
    ) -> None:
        if schedule_interval_seconds <= 0:
            raise ValueError("personal_rule_evaluation_interval_invalid")
        self._portfolio = portfolio
        self._rules = rules
        self._schedule_interval_seconds = schedule_interval_seconds

    def run_once(
        self, actor: PersonalActor, *, as_of: datetime
    ) -> HoldingRuleAutomationResult:
        if as_of.tzinfo is None:
            raise ValueError("as_of_requires_timezone")
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
        schedule_bucket = int(as_of.timestamp() // self._schedule_interval_seconds)
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
                        f"holding-rule-auto:{schedule_bucket}:{symbol}:{revision_fingerprint}"
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
