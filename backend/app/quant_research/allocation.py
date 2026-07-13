from __future__ import annotations

import math
from typing import Any

import pandas as pd


TARGET_WEIGHT_COLUMNS = ("ts_code", "industry", "target_weight")
CURRENT_WEIGHT_COLUMNS = ("ts_code", "industry", "current_weight")
TOLERANCE = 1e-12


def validate_allocation_policy(policy: Any) -> dict[str, Any]:
    required = {
        "method",
        "singleNameCap",
        "industryCap",
        "minimumCashWeight",
        "maxOneWayTurnover",
    }
    if not isinstance(policy, dict) or set(policy) != required:
        raise ValueError("allocation policy 字段无效")
    method = policy["method"]
    if method not in {"equal_weight", "inverse_volatility"}:
        raise ValueError("allocation method 只允许 equal_weight 或 inverse_volatility")
    numeric: dict[str, float] = {}
    for field in required - {"method"}:
        value = policy[field]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"allocation policy {field} 必须是有限数值")
        numeric[field] = float(value)
        if not math.isfinite(numeric[field]):
            raise ValueError(f"allocation policy {field} 必须是有限数值")
    if not 0 < numeric["singleNameCap"] <= 1:
        raise ValueError("singleNameCap 必须在 (0, 1] 内")
    if not 0 < numeric["industryCap"] <= 1:
        raise ValueError("industryCap 必须在 (0, 1] 内")
    if not 0 <= numeric["minimumCashWeight"] < 1:
        raise ValueError("minimumCashWeight 必须在 [0, 1) 内")
    if not 0 <= numeric["maxOneWayTurnover"] <= 1:
        raise ValueError("maxOneWayTurnover 必须在 [0, 1] 内")
    return {"method": method, **numeric}


def allocate_target_weights(
    candidates: pd.DataFrame,
    current_weights: pd.DataFrame,
    *,
    policy: dict[str, Any],
) -> pd.DataFrame:
    normalized_policy = validate_allocation_policy(policy)
    candidate_frame = _normalize_candidates(
        candidates,
        method=normalized_policy["method"],
    )
    current = _normalize_weight_frame(
        current_weights,
        weight_column="current_weight",
        allow_empty=True,
        label="当前权重",
    )
    _require_consistent_industries(candidate_frame, current)
    _validate_weight_constraints(
        current,
        weight_column="current_weight",
        policy=normalized_policy,
        label="当前组合",
    )
    desired = _allocate_before_turnover(candidate_frame, normalized_policy)
    current_series = current.set_index("ts_code")["current_weight"]
    desired_series = desired.set_index("ts_code")["target_weight"]
    symbols = sorted(set(current_series.index) | set(desired_series.index))
    current_aligned = current_series.reindex(symbols, fill_value=0.0)
    desired_aligned = desired_series.reindex(symbols, fill_value=0.0)
    delta = desired_aligned - current_aligned
    one_way_turnover = _one_way_turnover(delta)
    maximum = normalized_policy["maxOneWayTurnover"]
    scale = 1.0 if one_way_turnover <= maximum + TOLERANCE else maximum / one_way_turnover
    final_weights = current_aligned + delta * scale
    final_weights = final_weights.where(final_weights.abs() > TOLERANCE, 0.0)

    industry_map = {
        row.ts_code: row.industry
        for row in pd.concat(
            [
                candidate_frame[["ts_code", "industry"]],
                current[["ts_code", "industry"]],
            ],
            ignore_index=True,
        ).drop_duplicates("ts_code").itertuples(index=False)
    }
    result = pd.DataFrame(
        [
            {
                "ts_code": symbol,
                "industry": industry_map[symbol],
                "target_weight": float(final_weights[symbol]),
            }
            for symbol in symbols
            if final_weights[symbol] > TOLERANCE
        ],
        columns=TARGET_WEIGHT_COLUMNS,
    ).sort_values("ts_code", kind="stable").reset_index(drop=True)
    validate_target_weights(result, current, policy=normalized_policy)
    return result


def validate_target_weights(
    targets: pd.DataFrame,
    current_weights: pd.DataFrame,
    *,
    policy: dict[str, Any],
) -> None:
    normalized_policy = validate_allocation_policy(policy)
    target_frame = _normalize_weight_frame(
        targets,
        weight_column="target_weight",
        allow_empty=True,
        label="目标权重",
        exact_columns=True,
    )
    current = _normalize_weight_frame(
        current_weights,
        weight_column="current_weight",
        allow_empty=True,
        label="当前权重",
    )
    _require_consistent_industries(target_frame, current)
    _validate_weight_constraints(
        current,
        weight_column="current_weight",
        policy=normalized_policy,
        label="当前组合",
    )
    _validate_weight_constraints(
        target_frame,
        weight_column="target_weight",
        policy=normalized_policy,
        label="目标组合",
    )
    current_series = current.set_index("ts_code")["current_weight"]
    target_series = target_frame.set_index("ts_code")["target_weight"]
    symbols = sorted(set(current_series.index) | set(target_series.index))
    delta = target_series.reindex(symbols, fill_value=0.0) - current_series.reindex(
        symbols, fill_value=0.0
    )
    if _one_way_turnover(delta) > normalized_policy["maxOneWayTurnover"] + TOLERANCE:
        raise ValueError("目标组合超过单次换手上限")


def _allocate_before_turnover(
    candidates: pd.DataFrame,
    policy: dict[str, Any],
) -> pd.DataFrame:
    budget = 1.0 - policy["minimumCashWeight"]
    single_cap = policy["singleNameCap"]
    industry_cap = policy["industryCap"]
    counts = candidates.groupby("industry", sort=True)["ts_code"].size()
    total_capacity = float(
        sum(min(float(count) * single_cap, industry_cap) for count in counts)
    )
    if total_capacity < budget - TOLERANCE:
        raise ValueError("单票、行业和现金约束无法同时形成目标组合")

    if policy["method"] == "equal_weight":
        scores = pd.Series(1.0, index=candidates["ts_code"])
    else:
        scores = pd.Series(
            1.0 / candidates["volatility"].to_numpy(),
            index=candidates["ts_code"],
        )
    industries = candidates.set_index("ts_code")["industry"].to_dict()
    weights = pd.Series(0.0, index=candidates["ts_code"])
    remaining = budget
    for _iteration in range(len(candidates) * 2 + len(counts) + 1):
        if remaining <= TOLERANCE:
            break
        industry_totals = weights.groupby(weights.index.map(industries)).sum()
        active = [
            symbol
            for symbol in weights.index
            if weights[symbol] < single_cap - TOLERANCE
            and industry_totals.get(industries[symbol], 0.0) < industry_cap - TOLERANCE
        ]
        if not active:
            raise ValueError("约束裁剪后没有剩余容量")
        active_scores = scores.loc[active]
        proposal = remaining * active_scores / float(active_scores.sum())
        proposal = pd.Series(
            {
                symbol: min(float(proposal[symbol]), single_cap - weights[symbol])
                for symbol in active
            }
        )
        for industry in sorted({industries[symbol] for symbol in active}):
            members = [symbol for symbol in active if industries[symbol] == industry]
            proposed_total = float(proposal.loc[members].sum())
            capacity = industry_cap - float(industry_totals.get(industry, 0.0))
            if proposed_total > capacity + TOLERANCE:
                proposal.loc[members] *= capacity / proposed_total
        allocated = float(proposal.sum())
        if allocated <= TOLERANCE:
            raise ValueError("约束裁剪无法继续分配剩余权重")
        weights.loc[proposal.index] += proposal
        remaining = max(budget - float(weights.sum()), 0.0)
    if remaining > TOLERANCE:
        raise ValueError("确定性约束分配未能分配完整目标预算")
    return pd.DataFrame(
        [
            {
                "ts_code": symbol,
                "industry": industries[symbol],
                "target_weight": float(weights[symbol]),
            }
            for symbol in sorted(weights.index)
        ],
        columns=TARGET_WEIGHT_COLUMNS,
    )


def _normalize_candidates(frame: pd.DataFrame, *, method: str) -> pd.DataFrame:
    required = {"ts_code", "industry"}
    if method == "inverse_volatility":
        required.add("volatility")
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"候选集缺少字段：{', '.join(missing)}")
    columns = ["ts_code", "industry"] + (
        ["volatility"] if "volatility" in required else []
    )
    normalized = frame[columns].copy()
    if normalized.empty:
        raise ValueError("候选集不能为空")
    _normalize_identity_columns(normalized, "候选集")
    if normalized.duplicated("ts_code").any():
        raise ValueError("候选集 ts_code 重复")
    if method == "inverse_volatility":
        normalized["volatility"] = pd.to_numeric(
            normalized["volatility"], errors="coerce"
        )
        if normalized["volatility"].isna().any() or not normalized["volatility"].map(
            lambda value: math.isfinite(float(value)) and float(value) > 0
        ).all():
            raise ValueError("逆波动率分配要求正且有限的 volatility")
    return normalized.sort_values("ts_code", kind="stable").reset_index(drop=True)


def _normalize_weight_frame(
    frame: pd.DataFrame,
    *,
    weight_column: str,
    allow_empty: bool,
    label: str,
    exact_columns: bool = False,
) -> pd.DataFrame:
    required = {"ts_code", "industry", weight_column}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"{label}缺少字段：{', '.join(missing)}")
    if exact_columns and set(frame.columns) != required:
        raise ValueError(f"{label}只能包含 ts_code、industry 和 {weight_column}")
    normalized = frame[["ts_code", "industry", weight_column]].copy()
    if not allow_empty and normalized.empty:
        raise ValueError(f"{label}不能为空")
    _normalize_identity_columns(normalized, label)
    if normalized.duplicated("ts_code").any():
        raise ValueError(f"{label} ts_code 重复")
    normalized[weight_column] = pd.to_numeric(
        normalized[weight_column], errors="coerce"
    )
    if normalized[weight_column].isna().any() or not normalized[weight_column].map(
        lambda value: math.isfinite(float(value)) and float(value) >= 0
    ).all():
        raise ValueError(f"{label}权重必须非负且有限")
    return normalized.sort_values("ts_code", kind="stable").reset_index(drop=True)


def _normalize_identity_columns(frame: pd.DataFrame, label: str) -> None:
    frame["ts_code"] = frame["ts_code"].astype(str).str.strip().str.upper()
    frame["industry"] = frame["industry"].astype(str).str.strip()
    if frame["ts_code"].eq("").any() or frame["industry"].eq("").any():
        raise ValueError(f"{label}的 ts_code 和 industry 不能为空")


def _require_consistent_industries(left: pd.DataFrame, right: pd.DataFrame) -> None:
    left_map = left.set_index("ts_code")["industry"].to_dict()
    right_map = right.set_index("ts_code")["industry"].to_dict()
    conflicts = sorted(
        symbol
        for symbol in set(left_map) & set(right_map)
        if left_map[symbol] != right_map[symbol]
    )
    if conflicts:
        raise ValueError(f"同一标的行业身份冲突：{', '.join(conflicts[:10])}")


def _validate_weight_constraints(
    frame: pd.DataFrame,
    *,
    weight_column: str,
    policy: dict[str, Any],
    label: str,
) -> None:
    if frame.empty:
        return
    if frame[weight_column].max() > policy["singleNameCap"] + TOLERANCE:
        raise ValueError(f"{label}超过单票权重上限")
    if (
        frame.groupby("industry", sort=True)[weight_column].sum().max()
        > policy["industryCap"] + TOLERANCE
    ):
        raise ValueError(f"{label}超过行业权重上限")
    invested = float(frame[weight_column].sum())
    if invested > 1.0 - policy["minimumCashWeight"] + TOLERANCE:
        raise ValueError(f"{label}不满足最低现金权重")


def _one_way_turnover(delta: pd.Series) -> float:
    buys = float(delta[delta > 0].sum())
    sells = float(-delta[delta < 0].sum())
    return max(buys, sells)
