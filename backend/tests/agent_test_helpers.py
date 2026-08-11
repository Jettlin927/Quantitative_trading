"""agent 相关测试共享助手：脚本化 provider 与响应构造。"""

from __future__ import annotations

import json

from backend.app.personal_workspace.analysis import ProviderFailure


def completed_response(
    *,
    content: str | None = None,
    tool_calls: tuple[dict, ...] = (),
    input_tokens: int = 800,
    output_tokens: int = 400,
) -> dict:
    cache_hit = 300
    usage = {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_hit_tokens": cache_hit,
        "cache_miss_tokens": input_tokens - cache_hit,
    }
    return {
        "status": "completed",
        "message": {"content": content, "tool_calls": tool_calls},
        "usage": usage,
        "cost_usd": "0.0002",
    }


def tool_call(*, name: str, arguments: dict | None = None, call_id: str = "call-1") -> dict:
    return {"id": call_id, "name": name, "arguments": arguments or {}}


def claims_content(*, evidence_id: str = "tool:get_holdings:0") -> str:
    claims = [
        {
            "kind": "confirmed_fact",
            "statement": "已确认事实。",
            "evidence_ids": [evidence_id],
            "opposing_evidence_ids": [],
            "assumptions": [],
            "horizon": "截至工具数据时间",
            "invalidation_conditions": ["数据被修订"],
        },
        {
            "kind": "inference",
            "statement": "基于事实的推断。",
            "evidence_ids": [],
            "opposing_evidence_ids": [],
            "assumptions": ["明确假设"],
            "horizon": "条件期间",
            "invalidation_conditions": ["假设不成立"],
        },
        {
            "kind": "conditional_scenario",
            "statement": "条件情景。",
            "evidence_ids": [],
            "opposing_evidence_ids": [],
            "assumptions": ["情景条件"],
            "horizon": "情景期间",
            "invalidation_conditions": ["条件未发生"],
        },
        {
            "kind": "unknown",
            "statement": "仍未知的事项。",
            "evidence_ids": [],
            "opposing_evidence_ids": [],
            "assumptions": [],
            "horizon": "待确认",
            "invalidation_conditions": ["待新证据"],
        },
    ]
    return json.dumps({"claims": claims}, ensure_ascii=False)


class ScriptedAgentProvider:
    def __init__(self, script: list, *, available: bool = True) -> None:
        self.available = available
        self._script = list(script)
        self.captured_requests: list[dict] = []

    def create_response(self, request: dict) -> dict:
        self.captured_requests.append(request)
        if not self.available:
            raise ProviderFailure("provider_unavailable", retryable=False)
        if not self._script:
            raise ProviderFailure("provider_script_exhausted", retryable=False)
        response = self._script.pop(0)
        if isinstance(response, Exception):
            raise response
        return response
