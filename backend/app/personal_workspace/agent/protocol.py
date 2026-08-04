"""tool-use agent 核心契约：Tool / Skill / AgentProvider / 回合结果。

本模块不依赖具体 provider 与存储实现，只定义装配方与运行时之间遵守的契约。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Protocol

from ..analysis import AnalysisClaim, AnalysisIntent, AnalysisUsage, FrozenEvidence


@dataclass(frozen=True)
class ToolContext:
    """工具执行上下文：当前 actor 与用户意图。"""

    actor_id: str
    intent: AnalysisIntent
    clock: Callable[[], datetime]


@dataclass(frozen=True)
class ToolResult:
    """工具执行结果。ok=False 时 error 为失败码，内容会回灌给模型继续决策。"""

    ok: bool
    content: str
    error: str | None = None
    evidence: tuple[FrozenEvidence, ...] = ()


@dataclass(frozen=True)
class Tool:
    """工具定义：name/description/JSON Schema + 执行函数。"""

    name: str
    description: str
    input_schema: dict[str, Any]
    run: Callable[[ToolContext, dict[str, Any]], ToolResult]

    def function_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": self.input_schema.get("properties", {}),
                    "required": list(self.input_schema.get("required", [])),
                },
            },
        }


@dataclass(frozen=True)
class Skill:
    """技能：为特定分析场景注入系统提示，并声明推荐使用的工具子集。"""

    skill_id: str
    name: str
    description: str
    system_prompt: str
    tools: tuple[str, ...]


class AgentProvider(Protocol):
    """provider 契约：多轮消息 + tools 的 create_response。

    响应规范：
    - 正常：{"status": "completed", "message": {"content": str|None,
      "tool_calls": ({"id", "name", "arguments": dict}, ...)}, "usage": {...},
      "cost_usd": str}
    - 拒绝：{"status": "refusal", "message": {"content": None, "tool_calls": ()}}
    失败时抛 ProviderFailure(code, retryable)。
    """

    available: bool

    def create_response(self, request: dict[str, Any]) -> dict[str, Any]: ...


@dataclass(frozen=True)
class AgentTurnResult:
    claims: tuple[AnalysisClaim, ...]
    usage: AnalysisUsage | None
    cost_usd: str
    rounds: int
    tool_evidence: tuple[FrozenEvidence, ...]
