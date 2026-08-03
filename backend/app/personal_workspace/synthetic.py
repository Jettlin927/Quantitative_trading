from __future__ import annotations

from hashlib import sha256
import json


class SyntheticMarketObservationAdapter:
    def __init__(self, *, provider_available: bool) -> None:
        self._provider_available = provider_available

    def read(self) -> dict:
        return {
            "source_health": "fresh" if self._provider_available else "unavailable",
            "as_of": "2026-08-01T20:00:00Z",
            "bars": [
                {"date": "2026-07-30", "open": "78.0000", "high": "81.0000", "low": "77.5000", "close": "80.0000", "volume": "1000"},
                {"date": "2026-07-31", "open": "80.0000", "high": "82.5000", "low": "79.5000", "close": "82.0000", "volume": "1200"},
            ],
        }


class SyntheticOfficialEvidenceAdapter:
    evidence_id = "synthetic-official-evidence-001"

    def minimum_excerpt(self) -> str:
        return "合成的一手证据摘录，仅用于边界测试。"


class SyntheticAnalysisModelAdapter:
    def __init__(self) -> None:
        self.captured_payloads: list[dict] = []

    def analyze(self, payload: dict) -> dict:
        self.captured_payloads.append(dict(payload))
        return {
            "claim_id": "synthetic-claim-001",
            "kind": "inference",
            "statement": "合成证据只支持条件性影响机制，不构成买卖建议。",
            "evidence_ids": [SyntheticOfficialEvidenceAdapter.evidence_id],
        }


class SyntheticWorkspaceAdapters:
    def __init__(self, *, provider_available: bool = False) -> None:
        self.provider_available = provider_available
        self.market = SyntheticMarketObservationAdapter(provider_available=provider_available)
        self.evidence = SyntheticOfficialEvidenceAdapter()
        self.analysis_model = SyntheticAnalysisModelAdapter()

    def build_trace_payload(
        self,
        *,
        workspace_id: str,
        holding_id: str,
        analysis_id: str,
        question: str,
    ) -> dict:
        included = {
            "user_symbol": "SYNTH-001",
            "user_question": question,
            "official_evidence_excerpt": self.evidence.minimum_excerpt(),
        }
        analysis_claim = self.analysis_model.analyze(included)
        preview_sha256 = sha256(
            json.dumps(
                included,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        issues = [] if self.provider_available else ["provider_unavailable"]
        return {
            "workspace_id": workspace_id,
            "analysis_id": analysis_id,
            "synthetic": True,
            "research_eligible": False,
            "holding": {
                "holding_id": holding_id,
                "symbol": "SYNTH-001",
                "name": "合成边界测试标的",
                "quantity": "12.5000",
                "average_cost": "80.0000",
                "currency": "USD",
            },
            "market": self.market.read(),
            "rule_evaluations": [
                {"rule_id": "synthetic-hit", "label": "合成条件命中", "result": "hit", "reason": "合成收盘价达到固定阈值。"},
                {"rule_id": "synthetic-not-hit", "label": "合成条件未命中", "result": "not_hit", "reason": "合成收盘价未达到第二阈值。"},
                {"rule_id": "synthetic-insufficient", "label": "合成数据不足", "result": "insufficient_data", "reason": "合成窗口不足。"},
                {"rule_id": "synthetic-failed", "label": "合成计算失败", "result": "calculation_failed", "reason": "合成分母为零。"},
            ],
            "analysis_preview": {
                "status": "ready",
                "provider": "synthetic-model",
                "model": "scripted-deny-v1",
                "included_fields": list(included),
                "excluded_fields": [
                    {"field": "market_prices", "reason_code": "source_ai_context_denied"},
                    {"field": "derived_indicators", "reason_code": "derived_from_denied_source"},
                    {"field": "portfolio_weight", "reason_code": "derived_from_denied_source"},
                    {"field": "unrealized_return", "reason_code": "derived_from_denied_source"},
                    {"field": "price_rule_results", "reason_code": "local_deterministic_only"},
                ],
                "preview_sha256": preview_sha256,
                "retention": "store=false；不使用真实 provider",
            },
            "analysis_claim": analysis_claim,
            "issues": issues,
        }
