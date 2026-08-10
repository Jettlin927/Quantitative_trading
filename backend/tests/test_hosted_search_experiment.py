from __future__ import annotations

from decimal import Decimal
from copy import deepcopy
from datetime import datetime, timezone
import unittest
from urllib.error import HTTPError

from backend.app.personal_workspace.agent.ai_runtime import (
    RuntimeBudget,
    RuntimeRequest,
    run_runtime,
)
from backend.app.personal_workspace.agent.hosted_search_experiment import (
    ANTHROPIC_MESSAGES_URL,
    PUBLIC_EXPERIMENT_INPUT,
    DeepSeekHostedSearchExperiment,
    HostedSearchExperimentConfig,
    VerifiedWebEvidence,
)


DEEPSEEK_MODEL = "deepseek-v4-flash"


class HostedSearchExperimentTest(unittest.TestCase):
    def test_experiment_limits_cannot_exceed_issue_cap(self) -> None:
        with self.assertRaisesRegex(ValueError, "hosted_search_limits_invalid"):
            HostedSearchExperimentConfig(
                revision="hosted-search-experiment-v1",
                max_tool_calls=6,
                max_queries=10,
            )
        with self.assertRaisesRegex(ValueError, "hosted_search_limits_invalid"):
            HostedSearchExperimentConfig(
                revision="hosted-search-experiment-v1",
                max_tool_calls=5,
                max_queries=11,
            )
        with self.assertRaisesRegex(ValueError, "hosted_search_limits_invalid"):
            HostedSearchExperimentConfig(
                revision="hosted-search-experiment-v1",
                max_tool_calls=True,
                max_queries=10,
            )

    def test_public_case_returns_only_secondarily_verified_citations(self) -> None:
        captured: list[dict[str, object]] = []

        def transport(**request: object) -> dict[str, object]:
            captured.append(request)
            return valid_provider_response()

        class Verifier:
            def verify(
                self, *, url: str, title: str, provider_excerpt: str
            ) -> VerifiedWebEvidence:
                self.assert_inputs = (url, title, provider_excerpt)
                return VerifiedWebEvidence(
                    evidence_id="web:sec:nvda-10k",
                    source_url=url,
                    title=title,
                    excerpt="NVIDIA filed its annual report.",
                    content_sha256="a" * 64,
                    verified_at=datetime(2026, 8, 10, tzinfo=timezone.utc),
                )

        verifier = Verifier()
        runtime = DeepSeekHostedSearchExperiment(
            config=HostedSearchExperimentConfig(
                revision="hosted-search-experiment-v1",
                max_tool_calls=5,
                max_queries=10,
                hosted_search_usd_per_request=Decimal("0.001"),
            ),
            transport=transport,
            verifier=verifier,
        )

        result = run_runtime(runtime, public_request())

        self.assertEqual(result.status, "completed")
        self.assertEqual(captured[0]["url"], ANTHROPIC_MESSAGES_URL)
        body = captured[0]["body"]
        self.assertEqual(
            set(body), {"model", "max_tokens", "system", "messages", "tools", "stream"}
        )
        self.assertEqual(body["tools"][0]["max_uses"], 5)
        self.assertEqual(body["tools"][0]["allowed_domains"], ["www.sec.gov"])
        self.assertEqual(
            verifier.assert_inputs,
            (
                "https://www.sec.gov/Archives/edgar/data/1045810/report.htm",
                "NVIDIA annual report",
                "NVIDIA filed its annual report.",
            ),
        )
        self.assertEqual(result.events[1].type, "hosted_tool_started")
        self.assertEqual(result.events[2].evidence_ids, ("web:sec:nvda-10k",))
        self.assertEqual(result.events[-1].text, "NVIDIA has a recent annual report.")
        self.assertEqual(result.citations[0].evidence_id, "web:sec:nvda-10k")
        self.assertEqual(result.citations[0].output_block_index, 0)
        self.assertEqual(result.evidence[0].evidence_id, "web:sec:nvda-10k")
        self.assertEqual(result.usage.input_tokens, 120)
        self.assertEqual(result.usage.cache_hit_tokens, 20)
        self.assertEqual(result.usage.cache_miss_tokens, 100)
        self.assertEqual(result.usage.hosted_tool_calls, 1)
        self.assertEqual(result.usage.web_search_queries, 1)
        self.assertEqual(result.usage.hosted_cost_usd, Decimal("0.001"))
        self.assertEqual(result.usage.cost_usd, Decimal("0.001022456"))
        self.assertNotIn("provider-opaque-content", repr(result))

    def test_unpaired_or_over_limit_blocks_fail_closed(self) -> None:
        unpaired = valid_provider_response()
        unpaired["content"] = [
            block
            for block in unpaired["content"]
            if block["type"] != "web_search_tool_result"
        ]
        over_limit = valid_provider_response()
        over_limit["content"].insert(
            2,
            {
                "type": "server_tool_use",
                "id": "srvtoolu_2",
                "name": "web_search",
                "input": {"query": "NVIDIA SEC filing date"},
            },
        )
        over_limit["content"].insert(
            3,
            {
                "type": "web_search_tool_result",
                "tool_use_id": "srvtoolu_2",
                "content": [
                    {
                        "type": "web_search_result",
                        "title": "NVIDIA filing index",
                        "url": "https://www.sec.gov/Archives/edgar/data/1045810/index.htm",
                        "content": "NVIDIA filing index.",
                    }
                ],
            },
        )
        over_queries = valid_provider_response()
        over_queries["content"][0]["input"] = {
            "queries": [f"public query {index}" for index in range(11)]
        }
        cases = (
            (unpaired, 5, 10),
            (over_limit, 1, 10),
            (over_queries, 5, 10),
        )
        for response, max_tool_calls, max_queries in cases:
            with self.subTest(response=response):
                result = run_runtime(
                    runtime_for(
                        response,
                        max_tool_calls=max_tool_calls,
                        max_queries=max_queries,
                    ),
                    public_request(),
                )
                self.assertEqual(result.status, "failed")
                self.assertEqual(result.failure.code, "provider_invalid_response")

    def test_protocol_drift_usage_and_unverified_citation_fail_closed(self) -> None:
        invalid_usage = valid_provider_response()
        invalid_usage["usage"]["cache_read_input_tokens"] = -1
        wrong_model = valid_provider_response()
        wrong_model["model"] = "unexpected-model"
        unverified = valid_provider_response()

        class WrongVerifier:
            def verify(self, **_: str) -> VerifiedWebEvidence:
                return VerifiedWebEvidence(
                    evidence_id="web:wrong",
                    source_url="https://example.invalid/wrong",
                    title="wrong",
                    excerpt="wrong",
                    content_sha256="b" * 64,
                    verified_at=datetime(2026, 8, 10, tzinfo=timezone.utc),
                )

        invalid_usage_result = run_runtime(
            runtime_for(invalid_usage), public_request()
        )
        self.assertEqual(invalid_usage_result.failure.code, "provider_invalid_response")
        wrong_model_result = run_runtime(runtime_for(wrong_model), public_request())
        self.assertEqual(wrong_model_result.failure.code, "provider_invalid_response")
        unverified_result = run_runtime(
            DeepSeekHostedSearchExperiment(
                config=experiment_config(),
                transport=lambda **_: unverified,
                verifier=WrongVerifier(),
            ),
            public_request(),
        )
        self.assertEqual(unverified_result.failure.code, "provider_invalid_response")

    def test_server_search_usage_is_required_and_must_match_observed_calls(self) -> None:
        missing = valid_provider_response()
        missing["usage"].pop("server_tool_use")
        drifted = valid_provider_response()
        drifted["usage"]["server_tool_use"]["web_search_requests"] = 2
        negative = valid_provider_response()
        negative["usage"]["server_tool_use"]["web_search_requests"] = -1
        boolean = valid_provider_response()
        boolean["usage"]["server_tool_use"]["web_search_requests"] = True

        for response in (missing, drifted, negative, boolean):
            with self.subTest(response=response):
                result = run_runtime(runtime_for(response), public_request())
                self.assertEqual(result.status, "failed")
                self.assertEqual(result.failure.code, "provider_invalid_response")

    def test_multiple_text_support_still_rejects_unknown_blocks_and_no_citations(self) -> None:
        unknown_block = valid_provider_response()
        unknown_block["content"].insert(2, {"type": "thinking", "thinking": "raw"})
        no_citations = valid_provider_response()
        no_citations["content"][-1].pop("citations")
        partially_uncited = valid_provider_response()
        partially_uncited["content"].append(
            {"type": "text", "text": "unsupported conclusion"}
        )

        for response in (unknown_block, no_citations, partially_uncited):
            with self.subTest(response=response):
                result = run_runtime(runtime_for(response), public_request())
                self.assertEqual(result.status, "failed")
                self.assertEqual(result.failure.code, "provider_invalid_response")

    def test_local_source_allowlist_rejects_provider_domain_drift(self) -> None:
        response = valid_provider_response()
        response["content"][1]["content"][0]["url"] = "https://example.com/report"
        response["content"][-1]["citations"][0]["url"] = "https://example.com/report"

        result = run_runtime(runtime_for(response), public_request())

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.failure.code, "provider_invalid_response")

    def test_arbitrary_input_and_default_network_are_unavailable_without_side_effects(self) -> None:
        calls: list[object] = []
        runtime = DeepSeekHostedSearchExperiment(
            config=experiment_config(),
            transport=lambda **request: calls.append(request),
            verifier=AcceptingVerifier(),
        )
        arbitrary = public_request()
        arbitrary = RuntimeRequest(
            model=arbitrary.model,
            instructions=arbitrary.instructions,
            input_text="我的持仓成本是多少？",
            hosted_tools=arbitrary.hosted_tools,
            budget=arbitrary.budget,
        )
        rejected = run_runtime(runtime, arbitrary)
        self.assertEqual(rejected.failure.code, "provider_invalid_response")
        self.assertEqual(calls, [])

        offline = run_runtime(
            DeepSeekHostedSearchExperiment(
                config=experiment_config(), verifier=AcceptingVerifier()
            ),
            public_request(),
        )
        self.assertEqual(offline.failure.code, "provider_unavailable")

    def test_http_rate_limit_has_stable_retryable_failure(self) -> None:
        def rate_limited(**_: object) -> dict[str, object]:
            raise HTTPError(
                ANTHROPIC_MESSAGES_URL, 429, "rate limited", {}, None
            )

        result = run_runtime(
            DeepSeekHostedSearchExperiment(
                config=experiment_config(),
                transport=rate_limited,
                verifier=AcceptingVerifier(),
            ),
            public_request(),
        )
        self.assertEqual(result.failure.code, "provider_rate_limited")
        self.assertTrue(result.failure.retryable)


def public_request() -> RuntimeRequest:
    return RuntimeRequest(
        model=DEEPSEEK_MODEL,
        instructions="仅报告已验证的公开来源。",
        input_text=PUBLIC_EXPERIMENT_INPUT,
        hosted_tools=("web_search",),
        budget=RuntimeBudget(remaining_usd=Decimal("0.01")),
    )


def experiment_config(
    *, max_tool_calls: int = 5, max_queries: int = 10
) -> HostedSearchExperimentConfig:
    return HostedSearchExperimentConfig(
        revision="hosted-search-experiment-v1",
        max_tool_calls=max_tool_calls,
        max_queries=max_queries,
    )


class AcceptingVerifier:
    def verify(
        self, *, url: str, title: str, provider_excerpt: str
    ) -> VerifiedWebEvidence:
        return VerifiedWebEvidence(
            evidence_id=f"web:{len(url)}",
            source_url=url,
            title=title,
            excerpt=provider_excerpt,
            content_sha256="c" * 64,
            verified_at=datetime(2026, 8, 10, tzinfo=timezone.utc),
        )


def runtime_for(
    response: dict[str, object], *, max_tool_calls: int = 5, max_queries: int = 10
) -> DeepSeekHostedSearchExperiment:
    return DeepSeekHostedSearchExperiment(
        config=experiment_config(
            max_tool_calls=max_tool_calls, max_queries=max_queries
        ),
        transport=lambda **_: deepcopy(response),
        verifier=AcceptingVerifier(),
    )


def valid_provider_response() -> dict[str, object]:
    url = "https://www.sec.gov/Archives/edgar/data/1045810/report.htm"
    return {
        "id": "msg_synth",
        "type": "message",
        "role": "assistant",
        "model": DEEPSEEK_MODEL,
        "stop_reason": "end_turn",
        "content": [
            {
                "type": "server_tool_use",
                "id": "srvtoolu_1",
                "name": "web_search",
                "input": {"query": "NVIDIA latest SEC annual report"},
            },
            {
                "type": "web_search_tool_result",
                "tool_use_id": "srvtoolu_1",
                "content": [
                    {
                        "type": "web_search_result",
                        "title": "NVIDIA annual report",
                        "url": url,
                        "encrypted_content": "provider-opaque-content",
                        "page_age": "2026-02-25",
                    }
                ],
            },
            {
                "type": "text",
                "text": "NVIDIA has a recent annual report.",
                "citations": [
                    {
                        "type": "web_search_result_location",
                        "url": url,
                        "title": "NVIDIA annual report",
                        "cited_text": "NVIDIA filed its annual report.",
                    }
                ],
            },
        ],
        "usage": {
            "input_tokens": 100,
            "output_tokens": 30,
            "cache_read_input_tokens": 20,
            "server_tool_use": {"web_search_requests": 1},
        },
    }


if __name__ == "__main__":
    unittest.main()
