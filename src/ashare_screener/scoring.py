from __future__ import annotations

import math
from typing import Any


COMPONENT_KEYS = {
    "hot": "hot_score",
    "theme": "theme_score",
    "technical": "technical_score",
    "similarity": "similarity_score",
    "fund": "fund_score",
}


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
    price_total = int(metrics.get("price_condition_total", 6))
    pre_quote_price_count = int(metrics.get("pre_quote_price_condition_count", 0))

    if (
        metrics.get("limit_up_today")
        and metrics.get("pre_quote_primary_structure_match")
        and pre_quote_price_count >= 2
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
            "第一门槛成立，但当日或近5日已经明显拉升，不列入低位候选",
        )
    if metrics.get("fund_score") is None:
        if price_count >= max(4, price_total - 2):
            return (
                "待资金数据",
                "价格形态接近标准，但没有四档资金流，不能验证四线粘连和红线上穿",
            )
        return "数据不足", "资金数据缺失，价格条件也未达到近似门槛"
    if completeness < 0.75:
        return "数据不足", "有效评分数据不足 75%，不形成筛选结论"
    if metrics.get("strict_match"):
        return (
            "严格匹配",
            "回撤、底部量能、极端博弈、换手和资金四线均通过严格条件",
        )
    if metrics.get("near_match"):
        return (
            "接近标准",
            "大部分价格条件成立，资金四线也接近目标结构，仍缺少一至两项确认",
        )
    return "不符合", "未同时满足价格底部结构与资金四线结构"
