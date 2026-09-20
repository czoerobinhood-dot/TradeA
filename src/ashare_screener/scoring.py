from __future__ import annotations

import math
from typing import Any

from ashare_screener.preferences import PRIORITY_POLICY, PREFERENCE_BAND_WIDTH, QUALITY_BAND_WIDTH


COMPONENT_KEYS = {
    "hot": "hot_score",
    "theme": "theme_score",
    "technical": "technical_score",
    "similarity": "similarity_score",
    "fund": "fund_score",
}


def _preference_eligible(metrics: dict[str, Any]) -> bool:
    return bool(
        metrics.get("early_bottom_match")
        and not metrics.get("entry_late")
        and metrics.get("decision") in {"严格匹配", "接近标准", "待资金数据"}
        and metrics.get("preference_status") == "active"
    )


def preference_priority(metrics: dict[str, Any]) -> tuple[bool, int]:
    """Only explicitly confirmed, independent references can change priority."""
    if not _preference_eligible(metrics) or metrics.get("preference_priority_policy") != PRIORITY_POLICY:
        return False, 0
    confirmed = {item.get("code") for item in metrics.get("preference_neighbors", [])
                 if isinstance(item, dict) and item.get("source") == "user_confirmed"
                 and item.get("confidence") == 1.0 and item.get("code")}
    try:
        score = float(metrics.get("preference_score"))
    except (TypeError, ValueError):
        return False, 0
    if len(confirmed) < 3 or not math.isfinite(score) or not 0 <= score <= 100:
        return False, 0
    # Scores under 60 stay neutral. A fixed broad band avoids decimal tie-breaking.
    return True, max(0, math.floor(score / PREFERENCE_BAND_WIDTH) - 5)


def candidate_sort_key(metrics: dict[str, Any], *, use_preference: bool = True) -> tuple:
    """Use the same ordering in live ranking and frozen preference validation."""
    decision_order = {"严格匹配": 0, "接近标准": 1, "待资金数据": 2,
                      "已启动/错过低位": 3, "数据不足": 4, "不符合": 5}
    rise = metrics.get("rise_pressure", 99.0)
    base = metrics.get("base_final_score")
    base = float(metrics.get("final_score") or 0) if base is None else float(base)
    score = float(metrics.get("final_score") or 0) if use_preference else base
    tier = preference_priority(metrics)[1] if use_preference else 0
    return (
        decision_order.get(str(metrics.get("decision")), 9),
        -int(bool(metrics.get("early_bottom_match"))),
        -int(bool(metrics.get("bottom_red_volume_confirmed"))),
        -int(metrics.get("criteria_passed") or 0),
        -math.floor(base / QUALITY_BAND_WIDTH),
        -tier,
        -score,
        float(99.0 if rise is None else rise),
        -int(bool(metrics.get("right_edge_volume_expanded"))),
        -int(bool(metrics.get("gap_setup_match"))),
        -int(metrics.get("gap_condition_count") or 0),
    )


def apply_preference_adjustment(metrics: dict[str, Any]) -> None:
    """Bound preference influence within the existing eligible decision group."""
    baseline = float(metrics.get("base_final_score", metrics.get("final_score", 0)))
    metrics["base_final_score"] = baseline
    raw = metrics.get("preference_adjustment", 0.0)
    try:
        adjustment = float(raw or 0.0)
    except (TypeError, ValueError):
        adjustment = 0.0
    eligible = _preference_eligible(metrics)
    if not eligible or not math.isfinite(adjustment):
        adjustment = 0.0
    adjustment = max(-2.0, min(2.0, adjustment))
    adjusted = round(max(0.0, min(100.0, baseline + adjustment)), 2)
    metrics["preference_applied_adjustment"] = round(adjusted - baseline, 2)
    metrics["preference_ranking_eligible"] = eligible
    metrics["final_score"] = adjusted
    enabled, tier = preference_priority(metrics)
    metrics["preference_priority_enabled"] = enabled
    metrics["preference_priority_tier"] = tier


def weighted_score(
    metrics: dict[str, Any], weights: dict[str, float]
) -> tuple[float, float, list[str]]:
    weighted_total = 0.0
    available_weight = 0.0
    available: list[str] = []
    for component, metric_key in COMPONENT_KEYS.items():
        value = metrics.get(metric_key)
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(numeric):
            continue
        weight = float(weights[component])
        if weight <= 0:
            continue
        weighted_total += max(0.0, min(100.0, numeric)) * weight
        available_weight += weight
        available.append(component)
    if available_weight <= 0:
        return 0.0, 0.0, []
    configured_weight = sum(max(0.0, float(value)) for value in weights.values())
    completeness = available_weight / configured_weight if configured_weight else 0.0
    return round(weighted_total / available_weight, 2), round(completeness, 3), available


def classify_candidate(metrics: dict[str, Any]) -> tuple[str, str]:
    completeness = float(metrics.get("data_completeness", 0.0))
    price_count = int(metrics.get("price_condition_count", 0))
    pre_quote_price_count = int(metrics.get("pre_quote_price_condition_count", 0))

    if (
        metrics.get("limit_up_today")
        and pre_quote_price_count >= 2
        and (
            metrics.get("pre_quote_primary_structure_match")
            or (
                metrics.get("price_conditions", {}).get("drawdown_around_half")
                and metrics.get("bottom_volume_confirmed")
            )
        )
    ):
        return (
            "已启动/错过低位",
            "涨停前的底部结构成立，但当前已封涨停，不列入尚未启动候选",
        )

    if not metrics.get("primary_structure_match"):
        return (
            "不符合",
            "第一门槛未通过：当前不是从高位大跌后仍停留在底部的结构",
        )
    if metrics.get("entry_late") and price_count >= 2:
        return (
            "已启动/错过低位",
            "高位回落结构成立，但股价已经明显上涨，不列入低位放量候选",
        )
    if not metrics.get("bottom_volume_confirmed"):
        return (
            "不符合",
            "高位回落结构成立，但底部量能尚未形成持续放量确认",
        )
    if not metrics.get("bottom_red_volume_confirmed"):
        return (
            "不符合",
            "底部虽有放量，但日K红量占比或红色放量柱不足，绿量仍偏多",
        )
    if metrics.get("fund_score") is None:
        if metrics.get("early_bottom_match"):
            return (
                "待资金数据",
                "高位回落、日K底部红量占优且股价尚未大涨；等待四档资金流确认",
            )
        return "数据不足", "资金数据缺失，价格侧也未达到低位放量门槛"
    if completeness < 0.75:
        return "数据不足", "有效评分数据不足 75%，不形成筛选结论"
    if metrics.get("strict_match"):
        return (
            "严格匹配",
            "高位回落、日K底部红量、尚未大涨和资金四线均通过严格条件",
        )
    if metrics.get("near_match"):
        return (
            "接近标准",
            "日K低位红量价格条件成立，资金四线也接近目标结构",
        )
    if metrics.get("early_bottom_match"):
        return (
            "接近标准",
            "高位回落、日K底部红量占优且股价尚未大涨；资金四线尚未匹配",
        )
    return "不符合", "未同时满足价格底部结构与资金四线结构"
