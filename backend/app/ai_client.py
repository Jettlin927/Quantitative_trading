from __future__ import annotations

import json
import os
from math import isfinite
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


DEFAULT_DEEPSEEK_MODEL = "deepseek-v4-flash"
DEFAULT_DEEPSEEK_BASE_URL = "https://api.deepseek.com"


def analyze_with_deepseek(local_analysis: dict[str, Any], result: dict[str, Any], rows: list[dict[str, Any]], cfg: dict[str, Any]) -> dict[str, Any]:
    model = os.getenv("DEEPSEEK_MODEL", DEFAULT_DEEPSEEK_MODEL)
    token = os.getenv("DEEPSEEK_TOKEN") or os.getenv("DEEPSEEK_API_KEY")
    if not token:
        return with_local_status(local_analysis, model, "missing_token", "未配置 DEEPSEEK_TOKEN，已使用本地规则评价。")

    try:
        payload = build_request_payload(model, local_analysis, result, rows, cfg)
        response = post_json(deepseek_endpoint(), token, payload, int(os.getenv("DEEPSEEK_TIMEOUT_SECONDS", "25")))
        content = response["choices"][0]["message"]["content"]
        deepseek_analysis = normalize_deepseek_analysis(parse_json_object(content), local_analysis)
        deepseek_analysis["provider"] = "deepseek"
        deepseek_analysis["model"] = model
        deepseek_analysis["llmStatus"] = "ok"
        deepseek_analysis["localScore"] = local_analysis.get("score")
        deepseek_analysis["usage"] = response.get("usage")
        return deepseek_analysis
    except Exception as exc:
        return with_local_status(local_analysis, model, "error", f"DeepSeek 调用失败，已使用本地规则评价：{str(exc)[:180]}")


def build_request_payload(model: str, local_analysis: dict[str, Any], result: dict[str, Any], rows: list[dict[str, Any]], cfg: dict[str, Any]) -> dict[str, Any]:
    context = {
        "strategy": {
            "entryMode": cfg.get("entryMode"),
            "marketState": cfg.get("marketState"),
            "weeklyTradeLimit": cfg.get("weeklyTradeLimit"),
            "positionCapPct": cfg.get("positionCapPct"),
            "riskPct": cfg.get("riskPct"),
            "stopLossPct": cfg.get("stopLossPct"),
            "takeProfit1Pct": cfg.get("takeProfit1Pct"),
            "takeProfit2Pct": cfg.get("takeProfit2Pct"),
            "filters": {
                "useTrendFilter": cfg.get("useTrendFilter"),
                "useMacdFilter": cfg.get("useMacdFilter"),
                "useRsiFilter": cfg.get("useRsiFilter"),
                "useAtrStop": cfg.get("useAtrStop"),
                "blockWeakMarket": cfg.get("blockWeakMarket"),
                "blockSameDayReentry": cfg.get("blockSameDayReentry"),
            },
        },
        "backtest": {
            "sampleDays": len(rows),
            "startDate": rows[0].get("date") if rows else None,
            "endDate": rows[-1].get("date") if rows else None,
            "totalReturn": result.get("totalReturn"),
            "maxDrawdown": result.get("maxDrawdown"),
            "winRate": result.get("winRate"),
            "finalEquity": result.get("finalEquity"),
            "disciplineScore": result.get("disciplineScore"),
            "tradeCount": len(result.get("trades", [])),
            "completedTradeCount": len(result.get("completedTrades", [])),
            "blocked": result.get("blocked"),
        },
        "latestIndicators": compact_row(rows[-1]) if rows else {},
        "recentTrades": result.get("trades", [])[-16:],
        "localBaseline": local_analysis,
    }
    return {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "你是严格的量化策略审查助手，只根据用户提供的本地回测证据评价策略。"
                    "不要给真实交易指令，不要承诺收益，不要编造财务数据。"
                    "必须返回 JSON，不要 Markdown。"
                ),
            },
            {
                "role": "user",
                "content": (
                    "请客观评价这个 A 股日线回测策略是否有效，重点看样本量、收益/回撤、胜率、交易笔数、"
                    "纪律约束、过拟合风险、适用市场和下一步验证。"
                    "返回字段必须包含：score(0-100整数), verdict, marketFit, summary数组, strengths数组, "
                    "risks数组, nextChecks数组, factorRead数组。factorRead 每项包含 name,value,comment。"
                    f"\n\n回测证据JSON：{json.dumps(context, ensure_ascii=False)}"
                ),
            },
        ],
        "temperature": 0.2,
        "max_tokens": 1400,
        "response_format": {"type": "json_object"},
        "thinking": {"type": "disabled"},
        "stream": False,
    }


def post_json(url: str, token: str, payload: dict[str, Any], timeout: int) -> dict[str, Any]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = Request(
        url,
        data=data,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {detail[:240]}") from exc
    except URLError as exc:
        raise RuntimeError(str(exc.reason)) from exc


def deepseek_endpoint() -> str:
    base_url = os.getenv("DEEPSEEK_API_BASE", DEFAULT_DEEPSEEK_BASE_URL).rstrip("/")
    if base_url.endswith("/chat/completions"):
        return base_url
    return f"{base_url}/chat/completions"


def normalize_deepseek_analysis(raw: dict[str, Any], fallback: dict[str, Any]) -> dict[str, Any]:
    analysis = {**fallback, **raw}
    analysis["score"] = clamp_score(analysis.get("score", fallback.get("score", 0)))
    for key in ["summary", "strengths", "risks", "nextChecks"]:
        analysis[key] = string_list(analysis.get(key), fallback.get(key, []))
    analysis["factorRead"] = normalize_factor_read(analysis.get("factorRead"), fallback.get("factorRead", []))
    analysis["verdict"] = str(analysis.get("verdict") or fallback.get("verdict") or "需要扩大样本")
    analysis["marketFit"] = str(analysis.get("marketFit") or fallback.get("marketFit") or "需要更多样本判断。")
    return analysis


def normalize_factor_read(value: Any, fallback: list[dict[str, Any]]) -> list[dict[str, str]]:
    items = value if isinstance(value, list) else fallback
    normalized: list[dict[str, str]] = []
    for item in items[:6]:
        if isinstance(item, dict):
            normalized.append(
                {
                    "name": str(item.get("name") or "--"),
                    "value": str(item.get("value") or "--"),
                    "comment": str(item.get("comment") or ""),
                }
            )
    if normalized:
        return normalized
    if value is not fallback and fallback:
        return normalize_factor_read(fallback, [])
    return [{"name": "指标", "value": "--", "comment": "暂无足够指标证据。"}]


def string_list(value: Any, fallback: list[Any]) -> list[str]:
    source = value if isinstance(value, list) else fallback
    items = [str(item) for item in source if str(item).strip()]
    return items[:6] or ["暂无足够证据。"]


def clamp_score(value: Any) -> int:
    try:
        score = int(round(float(value)))
    except (TypeError, ValueError):
        score = 0
    return max(0, min(100, score))


def parse_json_object(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].strip()
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start < 0 or end <= start:
            raise
        value = json.loads(cleaned[start : end + 1])
    if not isinstance(value, dict):
        raise ValueError("DeepSeek 返回的 JSON 不是对象。")
    return value


def compact_row(row: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "date",
        "close",
        "ma5",
        "ma20",
        "ma60",
        "bollMid",
        "bollUpper",
        "bollLower",
        "bollBandwidthPct",
        "macdDif",
        "macdDea",
        "macdHist",
        "rsiStrategy",
        "kdjK",
        "kdjD",
        "kdjJ",
        "atrStrategy",
        "volume",
        "volMa",
    ]
    return {key: clean_number(row.get(key)) for key in keys if key in row}


def clean_number(value: Any) -> Any:
    if isinstance(value, float):
        return value if isfinite(value) else None
    return value


def with_local_status(local_analysis: dict[str, Any], model: str, status: str, message: str) -> dict[str, Any]:
    return {
        **local_analysis,
        "provider": "local",
        "model": model,
        "llmStatus": status,
        "llmError": message,
    }
