from __future__ import annotations

from datetime import date
from typing import Any


def build_evaluation_windows(spec: dict[str, Any], analysis: dict[str, Any], today: date | None = None) -> list[dict[str, Any]]:
    current_date = today or date.today()
    windows = [
        {
            "id": "train-2020-2024",
            "label": "第一轮",
            "title": "2020-01-01 至 2024-12-31",
            "startDate": "2020-01-01",
            "endDate": "2024-12-31",
            "role": "qualification",
            "qualifiesStrategy": True,
            "objective": "策略定型与主评估窗口，收益、回撤、盈亏比必须同时达标。",
        },
        {
            "id": "oos-2025-now",
            "label": "第二轮",
            "title": f"2025-01-01 至 {current_date.isoformat()}",
            "startDate": "2025-01-01",
            "endDate": current_date.isoformat(),
            "role": "out_of_sample",
            "qualifiesStrategy": True,
            "objective": "样本外复核，通过后才允许进入当前适用性讨论。",
        },
        {
            "id": "bear-market-observe",
            "label": "最终观察",
            "title": "标志性熊市压力段",
            "startDate": None,
            "endDate": None,
            "role": "observation_only",
            "qualifiesStrategy": False,
            "objective": "只观察熊市韧性、流动性和交易纪律，不作为策略达标判定。",
        },
    ]
    for window in windows:
        window["status"] = classify_window_status(window, spec, analysis)
    return windows


def classify_window_status(window: dict[str, Any], spec: dict[str, Any], analysis: dict[str, Any]) -> str:
    if not window["qualifiesStrategy"]:
        return "observation_pending"
    spec_window = spec.get("window", {})
    if not covers_window(spec_window.get("startDate"), spec_window.get("endDate"), window["startDate"], window["endDate"]):
        return "missing"
    return "pass" if bool(analysis.get("targetMet") or analysis.get("strictTargetMet")) else "fail"


def covers_window(spec_start: str | None, spec_end: str | None, required_start: str | None, required_end: str | None) -> bool:
    if not spec_start or not spec_end or not required_start or not required_end:
        return False
    return spec_start <= required_start and spec_end >= required_end
