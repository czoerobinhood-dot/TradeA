from __future__ import annotations

import html
import json
import math
import shutil
from dataclasses import asdict
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from ashare_screener.features import build_fund_proxy
from ashare_screener.models import Candidate, ScanOutcome


def _stock_links(code: str) -> dict[str, str]:
    if code.startswith("6"):
        eastmoney = f"https://quote.eastmoney.com/sh{code}.html"
    elif code.startswith(("4", "8", "9")):
        eastmoney = f"https://quote.eastmoney.com/bj/{code}.html"
    else:
        eastmoney = f"https://quote.eastmoney.com/sz{code}.html"
    return {
        "sohu": f"https://q.stock.sohu.com/cn/{code}/index.shtml",
        "eastmoney": eastmoney,
    }


def _finite(value: object) -> float | None:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return numeric if math.isfinite(numeric) else None


def _fmt(value: object, digits: int = 2, suffix: str = "") -> str:
    numeric = _finite(value)
    return "-" if numeric is None else f"{numeric:.{digits}f}{suffix}"


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def candidate_record(candidate: Candidate) -> dict[str, Any]:
    metrics = candidate.metrics
    links = _stock_links(candidate.code)
    return {
        "code": candidate.code,
        "name": candidate.name,
        "sohu_url": links["sohu"],
        "eastmoney_url": links["eastmoney"],
        "decision": metrics.get("decision"),
        "decision_reason": metrics.get("decision_reason"),
        "final_score": metrics.get("final_score"),
        "stage": metrics.get("stage"),
        "hot_rank": candidate.hot_rank,
        "source_tags": candidate.source_tags,
        "calibration_role": candidate.calibration_role,
        "concepts": candidate.concepts,
        "technical_score": metrics.get("technical_score"),
        "similarity_score": metrics.get("similarity_score"),
        "similar_reference": metrics.get("similar_reference"),
        "similar_reference_date": metrics.get("similar_reference_date"),
        "fund_score": metrics.get("fund_score"),
        "fund_super_on_top": metrics.get("fund_super_on_top"),
        "fund_recent_cross": metrics.get("fund_recent_cross"),
        "as_of": metrics.get("as_of"),
        "history_as_of": metrics.get("history_as_of"),
        "close": metrics.get("close"),
        "current_change_pct": metrics.get("current_change_pct"),
        "current_status_source": metrics.get("current_status_source"),
        "quote_latest": metrics.get("quote_latest"),
        "quote_change_pct": metrics.get("quote_change_pct"),
        "quote_source": metrics.get("quote_source"),
        "quote_cache_stale": metrics.get("quote_cache_stale"),
        "quote_cache_saved_at": metrics.get("quote_cache_saved_at"),
        "universe_turnover": metrics.get("universe_turnover"),
        "universe_volume_ratio": metrics.get("universe_volume_ratio"),
        "universe_return_5d": metrics.get("universe_return_5d"),
        "universe_return_20d": metrics.get("universe_return_20d"),
        "universe_return_60d": metrics.get("universe_return_60d"),
        "ma20": metrics.get("ma20"),
        "max_drawdown_250": metrics.get("max_drawdown_250"),
        "decline_peak_date": metrics.get("decline_peak_date"),
        "decline_peak_price": metrics.get("decline_peak_price"),
        "trough_date": metrics.get("trough_date"),
        "trough_price": metrics.get("trough_price"),
        "current_drawdown_from_decline_peak": metrics.get(
            "current_drawdown_from_decline_peak"
        ),
        "recovery_from_trough": metrics.get("recovery_from_trough"),
        "ma_spread": metrics.get("ma_spread"),
        "breakout_ratio": metrics.get("breakout_ratio"),
        "volume_ratio": metrics.get("volume_ratio"),
        "return_20d": metrics.get("return_20d"),
        "return_1d": metrics.get("return_1d"),
        "return_5d": metrics.get("return_5d"),
        "extension_ma20": metrics.get("extension_ma20"),
        "bottom_volume_ratio": metrics.get("bottom_volume_ratio"),
        "bottom_high_volume_days": metrics.get("bottom_high_volume_days"),
        "latest_volume_ratio": metrics.get("latest_volume_ratio"),
        "recent_3d_volume_ratio": metrics.get("recent_3d_volume_ratio"),
        "right_edge_volume_ratio": metrics.get("right_edge_volume_ratio"),
        "right_edge_volume_expanded": metrics.get("right_edge_volume_expanded"),
        "volume_as_of": metrics.get("volume_as_of"),
        "gap_date": metrics.get("gap_date"),
        "gap_floor": metrics.get("gap_floor"),
        "gap_ceiling": metrics.get("gap_ceiling"),
        "gap_size_pct": metrics.get("gap_size_pct"),
        "gap_bars_since": metrics.get("gap_bars_since"),
        "gap_close_unfilled": metrics.get("gap_close_unfilled"),
        "gap_intraday_unfilled": metrics.get("gap_intraday_unfilled"),
        "gap_hold_confirmed": metrics.get("gap_hold_confirmed"),
        "gap_close_range": metrics.get("gap_close_range"),
        "gap_post_volume_ratio": metrics.get("gap_post_volume_ratio"),
        "gap_post_volume_active_fraction": metrics.get(
            "gap_post_volume_active_fraction"
        ),
        "gap_setup_match": metrics.get("gap_setup_match"),
        "gap_setup_near": metrics.get("gap_setup_near"),
        "gap_condition_count": metrics.get("gap_condition_count"),
        "gap_condition_total": metrics.get("gap_condition_total"),
        "gap_preference_score": metrics.get("gap_preference_score"),
        "gap_conditions": metrics.get("gap_conditions", {}),
        "preference_conditions": metrics.get("preference_conditions", {}),
        "limit_up_count_120": metrics.get("limit_up_count_120"),
        "limit_down_count_120": metrics.get("limit_down_count_120"),
        "limit_up_today": metrics.get("limit_up_today"),
        "limit_up_price": metrics.get("limit_up_price"),
        "pre_quote_as_of": metrics.get("pre_quote_as_of"),
        "pre_quote_primary_structure_match": metrics.get(
            "pre_quote_primary_structure_match"
        ),
        "pre_quote_price_condition_count": metrics.get(
            "pre_quote_price_condition_count"
        ),
        "pre_quote_price_condition_total": metrics.get(
            "pre_quote_price_condition_total"
        ),
        "turnover_10d_avg": metrics.get("turnover_10d_avg"),
        "turnover": metrics.get("turnover"),
        "turnover_5d_avg": metrics.get("turnover_5d_avg"),
        "turnover_signal": metrics.get("turnover_signal"),
        "entry_late": metrics.get("entry_late"),
        "price_conditions": metrics.get("price_conditions", {}),
        "price_condition_count": metrics.get("price_condition_count"),
        "price_condition_total": metrics.get("price_condition_total"),
        "fund_conditions": metrics.get("fund_conditions", {}),
        "fund_condition_count": metrics.get("fund_condition_count"),
        "fund_condition_total": metrics.get("fund_condition_total"),
        "fund_tightness_ratio": metrics.get("fund_tightness_ratio"),
        "fund_cross_count_12d": metrics.get("fund_cross_count_12d"),
        "fund_red_cross_count": metrics.get("fund_red_cross_count"),
        "fund_shape_match": metrics.get("fund_shape_match"),
        "criteria_passed": metrics.get("criteria_passed"),
        "criteria_total": metrics.get("criteria_total"),
        "strict_match": metrics.get("strict_match"),
        "primary_structure_match": metrics.get("primary_structure_match"),
        "data_completeness": metrics.get("data_completeness"),
        "history_source": metrics.get("history_source"),
        "history_cache_stale": metrics.get("history_cache_stale"),
        "history_cache_saved_at": metrics.get("history_cache_saved_at"),
        "fund_cache_stale": metrics.get("fund_cache_stale"),
        "fund_cache_saved_at": metrics.get("fund_cache_saved_at"),
        "reasons": list(dict.fromkeys(candidate.reasons)),
        "risks": list(dict.fromkeys(candidate.risks)),
    }


def write_reports(
    outcome: ScanOutcome, output_dir: str | Path, report_limit: int
) -> dict[str, Path]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    timestamp = outcome.finished_at.strftime("%Y%m%d_%H%M%S")
    stem = output / f"scan_{timestamp}"
    records = [candidate_record(item) for item in outcome.candidates]

    csv_rows = []
    for rank, record in enumerate(records, start=1):
        csv_rows.append(
            {
                "排名": rank,
                "代码": record["code"],
                "名称": record["name"],
                "搜狐K线": record["sohu_url"],
                "东方财富行情": record["eastmoney_url"],
                "观察结论": record["decision"],
                "当前状态": "已涨停" if record["limit_up_today"] else "未涨停",
                "行情日期": record["as_of"],
                "当前价": record["close"],
                "当前涨跌幅": record["current_change_pct"],
                "当前状态来源": record["current_status_source"],
                "涨停前价格条件": (
                    f"{record['pre_quote_price_condition_count'] or 0}/"
                    f"{record['pre_quote_price_condition_total'] or 0}"
                ),
                "实时行情来源": record["quote_source"],
                "实时行情缓存时间": record["quote_cache_saved_at"],
                "实时行情使用旧缓存": record["quote_cache_stale"],
                "全市场快照换手": record["universe_turnover"],
                "全市场快照量比": record["universe_volume_ratio"],
                "全市场快照5日涨幅": record["universe_return_5d"],
                "全市场快照20日涨幅": record["universe_return_20d"],
                "全市场快照60日涨幅": record["universe_return_60d"],
                "总分": record["final_score"],
                "阶段": record["stage"],
                "人气排名": record["hot_rank"],
                "热点来源": "、".join(record["source_tags"]),
                "热点概念": "、".join(record["concepts"]),
                "技术形态分": record["technical_score"],
                "日线相似度": record["similarity_score"],
                "最相似样本": record["similar_reference"],
                "资金代理分": record["fund_score"],
                "严格条件": f"{record['criteria_passed'] or 0}/{record['criteria_total'] or 0}",
                "高低回撤": record["max_drawdown_250"],
                "当前距该轮高点": record["current_drawdown_from_decline_peak"],
                "低点后反弹": record["recovery_from_trough"],
                "底部量能倍数": record["bottom_volume_ratio"],
                "最新一日量比": record["latest_volume_ratio"],
                "近3日均量比": record["recent_3d_volume_ratio"],
                "右侧量比": record["right_edge_volume_ratio"],
                "右侧放量": record["right_edge_volume_expanded"],
                "成交量日期": record["volume_as_of"],
                "缺口日期": record["gap_date"],
                "缺口幅度": record["gap_size_pct"],
                "缺口收盘未补": record["gap_close_unfilled"],
                "缺口盘中未补": record["gap_intraday_unfilled"],
                "缺口后交易日": record["gap_bars_since"],
                "缺口横盘振幅": record["gap_close_range"],
                "缺口后量能倍数": record["gap_post_volume_ratio"],
                "缺口趋势条件": (
                    f"{record['gap_condition_count'] or 0}/"
                    f"{record['gap_condition_total'] or 0}"
                ),
                "缺口趋势优选": record["gap_setup_match"],
                "近120日涨停触及": record["limit_up_count_120"],
                "近120日跌停触及": record["limit_down_count_120"],
                "有效换手": record["turnover_signal"],
                "最新换手": record["turnover"],
                "近10日平均换手": record["turnover_10d_avg"],
                "资金线收敛比": record["fund_tightness_ratio"],
                "近12日资金线交叉": record["fund_cross_count_12d"],
                "红线上穿次数": record["fund_red_cross_count"],
                "数据完整度": record["data_completeness"],
                "日线来源": record["history_source"],
                "日线使用旧缓存": record["history_cache_stale"],
                "资金使用旧缓存": record["fund_cache_stale"],
                "加分理由": "；".join(record["reasons"]),
                "风险项": "；".join(record["risks"]),
            }
        )
    csv_path = stem.with_suffix(".csv")
    pd.DataFrame(csv_rows).to_csv(csv_path, index=False, encoding="utf-8-sig")

    json_path = stem.with_suffix(".json")
    payload = {
        "status": outcome.status,
        "started_at": outcome.started_at.isoformat(),
        "finished_at": outcome.finished_at.isoformat(),
        "source_summary": outcome.source_summary,
        "templates": [asdict(item) for item in outcome.templates],
        "issues": [item.to_dict() for item in outcome.issues],
        "candidates": records,
    }
    json_path.write_text(
        json.dumps(_json_safe(payload), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    html_path = stem.with_suffix(".html")
    html_path.write_text(
        render_html(outcome, outcome.candidates, detail_limit=report_limit),
        encoding="utf-8",
    )
    latest_path = output / "latest.html"
    shutil.copyfile(html_path, latest_path)
    return {"csv": csv_path, "json": json_path, "html": html_path, "latest": latest_path}


def _polyline(values: pd.Series, x_values: list[float], y_map: Any) -> str:
    points = []
    for x, value in zip(x_values, values, strict=True):
        numeric = _finite(value)
        if numeric is not None:
            points.append(f"{x:.1f},{y_map(numeric):.1f}")
    return " ".join(points)


def candlestick_svg(history: pd.DataFrame, width: int = 920, height: int = 330) -> str:
    frame = history.sort_values("date").tail(60).copy()
    if frame.empty:
        return ""
    for length in (5, 10, 20):
        frame[f"ma{length}"] = frame["close"].rolling(length).mean()
    left, right, top, bottom = 52, 18, 16, 30
    volume_height = 48
    gap = 12
    price_bottom = height - bottom - volume_height - gap
    plot_width = width - left - right
    price_height = price_bottom - top
    low = float(frame["low"].min())
    high = float(frame["high"].max())
    padding = max((high - low) * 0.08, high * 0.005)
    low -= padding
    high += padding
    span = max(high - low, 1e-9)
    y_map = lambda value: top + (high - value) / span * price_height
    step = plot_width / len(frame)
    x_values = [left + (index + 0.5) * step for index in range(len(frame))]
    candle_width = max(2.0, min(8.0, step * 0.58))
    max_volume = max(float(frame["volume"].max()), 1.0)

    fragments = [
        f'<svg class="chart" viewBox="0 0 {width} {height}" role="img" aria-label="近60日K线">',
        '<rect width="100%" height="100%" fill="#ffffff"/>',
    ]
    for index in range(5):
        value = low + span * index / 4
        y = y_map(value)
        fragments.append(
            f'<line x1="{left}" y1="{y:.1f}" x2="{width-right}" y2="{y:.1f}" stroke="#e6e9ee" stroke-width="1"/>'
        )
        fragments.append(
            f'<text x="{left-7}" y="{y+4:.1f}" text-anchor="end" class="axis">{value:.2f}</text>'
        )
    for index, (_, row) in enumerate(frame.iterrows()):
        x = x_values[index]
        open_price = float(row["open"])
        close_price = float(row["close"])
        color = "#c43d4b" if close_price >= open_price else "#168579"
        fragments.append(
            f'<line x1="{x:.1f}" y1="{y_map(float(row["high"])):.1f}" x2="{x:.1f}" y2="{y_map(float(row["low"])):.1f}" stroke="{color}" stroke-width="1.2"/>'
        )
        body_y = min(y_map(open_price), y_map(close_price))
        body_height = max(abs(y_map(open_price) - y_map(close_price)), 1.2)
        fragments.append(
            f'<rect x="{x-candle_width/2:.1f}" y="{body_y:.1f}" width="{candle_width:.1f}" height="{body_height:.1f}" fill="{color}"/>'
        )
        volume_height_px = float(row["volume"]) / max_volume * volume_height
        fragments.append(
            f'<rect x="{x-candle_width/2:.1f}" y="{height-bottom-volume_height_px:.1f}" width="{candle_width:.1f}" height="{volume_height_px:.1f}" fill="{color}" opacity="0.42"/>'
        )
    colors = {"ma5": "#23262d", "ma10": "#d39b23", "ma20": "#9c4dcc"}
    for column, color in colors.items():
        points = _polyline(frame[column], x_values, y_map)
        fragments.append(
            f'<polyline points="{points}" fill="none" stroke="{color}" stroke-width="1.8"/>'
        )
    first_date = pd.Timestamp(frame["date"].iloc[0]).strftime("%Y-%m-%d")
    last_date = pd.Timestamp(frame["date"].iloc[-1]).strftime("%Y-%m-%d")
    fragments.extend(
        [
            f'<text x="{left}" y="{height-5}" class="axis">{first_date}</text>',
            f'<text x="{width-right}" y="{height-5}" text-anchor="end" class="axis">{last_date}</text>',
            '<g class="legend"><text x="62" y="14">MA5</text><text x="105" y="14" fill="#d39b23">MA10</text><text x="157" y="14" fill="#9c4dcc">MA20</text></g>',
            "</svg>",
        ]
    )
    return "".join(fragments)


def fund_svg(fund_flow: pd.DataFrame, width: int = 920, height: int = 210) -> str:
    try:
        frame = build_fund_proxy(fund_flow).tail(60)
    except Exception:
        return ""
    columns = {
        "proxy_super_large_pct": ("超大单", "#c43d4b"),
        "proxy_large_pct": ("大单", "#d39b23"),
        "proxy_medium_pct": ("中单", "#3576ba"),
        "proxy_small_pct": ("小单", "#168579"),
    }
    left, right, top, bottom = 52, 18, 28, 24
    values = frame[list(columns)].to_numpy(dtype=float)
    low = float(np.nanmin(values))
    high = float(np.nanmax(values))
    padding = max((high - low) * 0.08, 0.5)
    low -= padding
    high += padding
    span = max(high - low, 1e-9)
    y_map = lambda value: top + (high - value) / span * (height - top - bottom)
    plot_width = width - left - right
    x_values = [left + index * plot_width / max(1, len(frame) - 1) for index in range(len(frame))]
    fragments = [
        f'<svg class="chart" viewBox="0 0 {width} {height}" role="img" aria-label="资金博弈代理线">',
        '<rect width="100%" height="100%" fill="#ffffff"/>',
    ]
    if low <= 0 <= high:
        zero_y = y_map(0)
        fragments.append(
            f'<line x1="{left}" y1="{zero_y:.1f}" x2="{width-right}" y2="{zero_y:.1f}" stroke="#aeb4bd" stroke-dasharray="4 4"/>'
        )
    legend_x = left
    for column, (label, color) in columns.items():
        points = _polyline(frame[column], x_values, y_map)
        fragments.append(
            f'<polyline points="{points}" fill="none" stroke="{color}" stroke-width="2"/>'
        )
        fragments.append(
            f'<text x="{legend_x}" y="17" fill="{color}" class="legend">{label}</text>'
        )
        legend_x += 70
    fragments.extend(
        [
            f'<text x="{left-7}" y="{y_map(high-padding)+4:.1f}" text-anchor="end" class="axis">{high-padding:.1f}</text>',
            f'<text x="{left-7}" y="{y_map(low+padding)+4:.1f}" text-anchor="end" class="axis">{low+padding:.1f}</text>',
            "</svg>",
        ]
    )
    return "".join(fragments)


def _sort_value(value: object, *, scale: float = 1.0) -> str:
    numeric = _finite(value)
    return "" if numeric is None else f"{numeric * scale:.12g}"


def _signed_pct(value: object) -> str:
    numeric = _finite(value)
    return "-" if numeric is None else f"{numeric:+.2f}%"


def _sortable_header(
    column: int,
    label: str,
    *,
    kind: str = "number",
    first_direction: str = "desc",
    initial: bool = False,
) -> str:
    aria_sort = "ascending" if initial else "none"
    icon = "↑" if initial else "↕"
    return (
        f'<th aria-sort="{aria_sort}"><button type="button" class="sort-button" '
        f'data-column="{column}" data-type="{kind}" data-first="{first_direction}" '
        f'title="按{html.escape(label)}排序">{html.escape(label)} '
        f'<span class="sort-icon" aria-hidden="true">{icon}</span></button></th>'
    )


def render_html(
    outcome: ScanOutcome,
    candidates: list[Candidate],
    *,
    detail_limit: int | None = None,
) -> str:
    summary = outcome.source_summary
    detail_limit = len(candidates) if detail_limit is None else max(0, detail_limit)
    detail_count = min(len(candidates), detail_limit)
    detail_codes = {item.code for item in candidates[:detail_count]}
    status_text = {"ok": "数据完整", "partial": "部分数据缺失", "failed": "扫描失败"}.get(
        outcome.status, outcome.status
    )
    table_rows = []
    details = []
    not_limit_candidates: list[dict[str, Any]] = []
    limit_up_candidates: list[dict[str, Any]] = []
    right_volume_candidates: list[dict[str, Any]] = []
    gap_setup_candidates: list[dict[str, Any]] = []
    condition_labels = {
        "decline_into_current_base": "大跌后仍在底部",
        "drawdown_around_half": "回撤约50%",
        "bottom_volume_expanded": "底部放量",
        "right_edge_volume_expanded": "右侧放量",
        "recent_gap_up": "近期向上跳空",
        "gap_close_unfilled": "收盘未补缺口",
        "gap_sideways_holding": "缺口上方横盘",
        "gap_uptrend": "向上趋势",
        "gap_sustained_volume": "缺口后持续放量",
        "repeated_limit_activity": "涨跌停反复",
        "turnover_near_target": "换手接近10%",
        "bottom_consolidated": "底部收敛",
        "entry_not_late": "尚未大涨",
        "four_lines_compact": "四线靠近",
        "crossings_orderly": "交织不乱",
        "red_line_on_top": "红线在上",
        "red_line_recent_cross": "红线上穿",
    }
    for rank, candidate in enumerate(candidates, start=1):
        record = candidate_record(candidate)
        decision = str(record["decision"])
        limit_up_today = bool(record["limit_up_today"])
        if not limit_up_today and decision in {"严格匹配", "接近标准", "待资金数据"}:
            view_group = "not-limit"
            not_limit_candidates.append(record)
        elif limit_up_today and decision == "已启动/错过低位":
            view_group = "limit-up"
            limit_up_candidates.append(record)
        else:
            view_group = "other"
        right_edge_volume_expanded = bool(record["right_edge_volume_expanded"])
        if view_group == "not-limit" and right_edge_volume_expanded:
            right_volume_candidates.append(record)
        gap_setup_near = bool(record["gap_setup_near"])
        if view_group == "not-limit" and gap_setup_near:
            gap_setup_candidates.append(record)
        decision_class = {
            "严格匹配": "positive",
            "接近标准": "watch",
            "待资金数据": "muted",
            "已启动/错过低位": "danger",
            "数据不足": "muted",
        }.get(decision, "muted")
        status_class = "positive" if limit_up_today else "clear"
        limit_status_text = "已涨停" if limit_up_today else "未涨停"
        status_source_text = str(record["current_status_source"] or "日线")
        if record["quote_cache_stale"] and status_source_text == "实时行情":
            status_source_text = "实时行情缓存"
        status_source = html.escape(status_source_text)
        favorite_button = (
            f'<button type="button" class="favorite-button" data-favorite-code="{record["code"]}" '
            f'data-favorite-name="{html.escape(record["name"], quote=True)}" aria-pressed="false" '
            f'aria-label="收藏 {html.escape(record["name"], quote=True)}" title="收藏">☆</button>'
        )
        local_detail_link = (
            f'<a href="#stock-{record["code"]}" title="查看本页K线和筛选明细">本页K线</a>'
            if record["code"] in detail_codes
            else ""
        )
        action_links = (
            '<div class="stock-actions">'
            f'{local_detail_link}'
            f'<a href="{html.escape(record["sohu_url"], quote=True)}" target="_blank" rel="noopener noreferrer" title="打开搜狐日/周/月K线、盘口和公司资料">搜狐K线</a>'
            f'<a href="{html.escape(record["eastmoney_url"], quote=True)}" target="_blank" rel="noopener noreferrer" title="打开东方财富K线、资金、F10和公告">东财K线/F10</a>'
            f'<button type="button" class="copy-code-button" data-copy-code="{record["code"]}" title="复制后在华泰客户端输入代码">复制代码</button>'
            '</div>'
        )
        cells = [
            (str(rank), str(rank), "numeric rank-cell"),
            (
                html.escape(record["name"]),
                f'<div class="stock-cell">{favorite_button}<div><strong><a class="stock-name-link" href="{html.escape(record["sohu_url"], quote=True)}" target="_blank" rel="noopener noreferrer" title="打开搜狐完整行情与K线">{html.escape(record["name"])}</a></strong>'
                f'<br><span class="subtle">{record["code"]}</span></div></div>',
                "",
            ),
            (
                html.escape(decision),
                f'<span class="state {decision_class}">{html.escape(decision)}</span>',
                "",
            ),
            (
                "1" if limit_up_today else "0",
                f'<span class="state {status_class}">{limit_status_text}</span><br><span class="subtle">{status_source}</span>',
                "",
            ),
            (_sort_value(record["current_change_pct"]), _signed_pct(record["current_change_pct"]), "numeric"),
            (
                _sort_value(record["criteria_passed"]),
                f'{record["criteria_passed"] or 0}/{record["criteria_total"] or 0}',
                "numeric",
            ),
            (
                _sort_value(record["max_drawdown_250"], scale=-100),
                f'{_fmt(-(_finite(record["max_drawdown_250"]) or 0) * 100, 0, "%")} / {_fmt((_finite(record["current_drawdown_from_decline_peak"]) or 0) * 100, 0, "%")}',
                "numeric",
            ),
            (_sort_value(record["bottom_volume_ratio"]), f'{_fmt(record["bottom_volume_ratio"], 2)}x', "numeric"),
            (
                _sort_value(record["right_edge_volume_ratio"]),
                f'{_fmt(record["right_edge_volume_ratio"], 2)}x',
                "numeric",
            ),
            (
                _sort_value(record["gap_condition_count"]),
                (
                    f'{html.escape(str(record["gap_date"] or "-"))}<br>'
                    f'<span class="subtle">{_fmt((_finite(record["gap_size_pct"]) or 0) * 100, 1, "%")} · '
                    f'{record["gap_condition_count"] or 0}/{record["gap_condition_total"] or 0}</span>'
                ),
                "numeric",
            ),
            (
                _sort_value(record["gap_post_volume_ratio"]),
                f'{_fmt(record["gap_post_volume_ratio"], 2)}x',
                "numeric",
            ),
            (
                _sort_value(record["limit_up_count_120"]),
                f'{record["limit_up_count_120"] or 0}/{record["limit_down_count_120"] or 0}',
                "numeric",
            ),
            (_sort_value(record["turnover_signal"]), _fmt(record["turnover_signal"], 1, "%"), "numeric"),
            (_sort_value(record["fund_tightness_ratio"]), _fmt(record["fund_tightness_ratio"], 2), "numeric"),
            (
                _sort_value(record["fund_cross_count_12d"]),
                str(record["fund_cross_count_12d"] if record["fund_cross_count_12d"] is not None else "-"),
                "numeric",
            ),
            (
                "" if record["fund_super_on_top"] is None else ("1" if record["fund_super_on_top"] else "0"),
                "是" if record["fund_super_on_top"] else "否" if record["fund_super_on_top"] is not None else "-",
                "",
            ),
            (_sort_value(record["final_score"]), _fmt(record["final_score"], 1), "numeric"),
            ("", action_links, "actions-cell"),
        ]
        cell_html = "".join(
            f'<td class="{class_name}" data-sort-value="{html.escape(sort_value, quote=True)}">{display}</td>'
            for sort_value, display, class_name in cells
        )
        table_rows.append(
            f'<tr data-view-group="{view_group}" data-right-volume="{str(right_edge_volume_expanded).lower()}" '
            f'data-gap-setup="{str(gap_setup_near).lower()}" '
            f'data-code="{record["code"]}" data-favorite="false" '
            f'data-original-rank="{rank}">{cell_html}</tr>'
        )
        if rank > detail_count:
            continue
        all_conditions = (
            record["price_conditions"]
            | record["preference_conditions"]
            | record["fund_conditions"]
        )
        condition_items = "".join(
            f'<span class="condition {"pass" if passed else "fail"}">{"✓" if passed else "×"} {html.escape(condition_labels.get(key, key))}</span>'
            for key, passed in all_conditions.items()
        )
        reason_items = "".join(
            f"<li>{html.escape(item)}</li>" for item in record["reasons"][:12]
        ) or "<li>暂无明确加分项</li>"
        risk_items = "".join(
            f"<li>{html.escape(item)}</li>" for item in record["risks"][:12]
        ) or "<li>未识别到规则内的额外风险项</li>"
        fund_chart = fund_svg(candidate.fund_flow) if candidate.fund_flow is not None else ""
        fund_block = (
            f'<h4>资金博弈代理</h4>{fund_chart}'
            if fund_chart
            else '<p class="notice">该候选没有取得四档历史资金流，最终得分已按可用组件重新加权。</p>'
        )
        details.append(
            f'<section class="candidate" id="stock-{candidate.code}">'
            f'<div class="candidate-head"><div class="candidate-identity">{favorite_button}<div>'
            f'<h3>{rank}. {html.escape(candidate.name)} <span>{candidate.code}</span></h3>'
            f'<p>{html.escape(str(record["decision_reason"]))}</p>{action_links}</div></div>'
            f'<div class="score">{_fmt(record["final_score"], 1)}<small>综合分</small></div></div>'
            f'<div class="metrics"><span>阶段 <b>{html.escape(str(record["stage"]))}</b></span>'
            f'<span>当前状态 <b>{"已涨停" if record["limit_up_today"] else "未涨停"}</b></span>'
            f'<span>当前价/涨幅 {_fmt(record["close"], 2)} / {_signed_pct(record["current_change_pct"])}</span>'
            f'<span>行情日期 {html.escape(str(record["as_of"] or "-"))}</span>'
            f'<span>状态口径 {html.escape(str(record["current_status_source"] or "日线"))}</span>'
            f'<span>涨停前条件 {record["pre_quote_price_condition_count"] or 0}/{record["pre_quote_price_condition_total"] or 0}</span>'
            f'<span>严格条件 {record["criteria_passed"] or 0}/{record["criteria_total"] or 0}</span>'
            f'<span>回撤 {_fmt(-(_finite(record["max_drawdown_250"]) or 0) * 100, 0, "%")}</span>'
            f'<span>当前距该轮高点 {_fmt((_finite(record["current_drawdown_from_decline_peak"]) or 0) * 100, 0, "%")}</span>'
            f'<span>低点后反弹 {_fmt((_finite(record["recovery_from_trough"]) or 0) * 100, 0, "%")}</span>'
            f'<span>底部量能 {_fmt(record["bottom_volume_ratio"], 2)}x</span>'
            f'<span>右侧量 {_fmt(record["right_edge_volume_ratio"], 2)}x（最新 {_fmt(record["latest_volume_ratio"], 2)}x / 3日 {_fmt(record["recent_3d_volume_ratio"], 2)}x）</span>'
            f'<span>成交量日期 {html.escape(str(record["volume_as_of"] or "-"))}</span>'
            f'<span>缺口日期/幅度 {html.escape(str(record["gap_date"] or "-"))} / {_fmt((_finite(record["gap_size_pct"]) or 0) * 100, 1, "%")}</span>'
            f'<span>缺口条件 {record["gap_condition_count"] or 0}/{record["gap_condition_total"] or 0}</span>'
            f'<span>缺口收盘/盘中未补 {"是" if record["gap_close_unfilled"] else "否"} / {"是" if record["gap_intraday_unfilled"] else "否"}</span>'
            f'<span>缺口后横盘 {record["gap_bars_since"] or 0} 日 / {_fmt((_finite(record["gap_close_range"]) or 0) * 100, 1, "%")}</span>'
            f'<span>缺口后持续量 {_fmt(record["gap_post_volume_ratio"], 2)}x</span>'
            f'<span>涨/跌停触及 {record["limit_up_count_120"] or 0}/{record["limit_down_count_120"] or 0}</span>'
            f'<span>有效换手 {_fmt(record["turnover_signal"], 1, "%")}</span>'
            f'<span>最新/10日换手 {_fmt(record["turnover"], 1, "%")}/{_fmt(record["turnover_10d_avg"], 1, "%")}</span>'
            f'<span>技术 {_fmt(record["technical_score"], 1)}</span>'
            f'<span>相似 {_fmt(record["similarity_score"], 1)}</span>'
            f'<span>资金 {_fmt(record["fund_score"], 1)}</span>'
            f'<span>热点来源 {html.escape("、".join(record["source_tags"]) or "未知")}</span>'
            f'<span>实时行情 {html.escape(str(record["quote_source"] or "缺失"))}</span>'
            f'<span>实时缓存 {html.escape(str(record["quote_cache_saved_at"] or "-"))}</span>'
            f'<span>全市场快照换手/量比 {_fmt(record["universe_turnover"], 1, "%")} / {_fmt(record["universe_volume_ratio"], 2)}x</span>'
            f'<span>日线来源 {html.escape(str(record["history_source"] or "未知"))}</span>'
            f'<span>日线数据 {"最近缓存" if record["history_cache_stale"] else "本轮/有效缓存"}</span>'
            f'<span>资金数据 {"最近缓存" if record["fund_cache_stale"] else "本轮/有效缓存" if record["fund_score"] is not None else "缺失"}</span>'
            f'<span>相似样本 {html.escape(str(record["similar_reference"] or "-"))}</span></div>'
            f'<div class="conditions">{condition_items}</div>'
            f'<h4>近 60 日日线</h4>{candlestick_svg(candidate.history)}'
            f'{fund_block}<div class="notes"><div><h4>加分证据</h4><ul>{reason_items}</ul></div>'
            f'<div><h4>风险与反证</h4><ul>{risk_items}</ul></div></div></section>'
        )

    issue_rows = "".join(
        f"<tr><td>{html.escape(issue.scope)}</td><td>{html.escape(issue.code or '-')}</td><td>{html.escape(issue.message)}</td></tr>"
        for issue in outcome.issues
    ) or '<tr><td colspan="3">无</td></tr>'
    template_text = "、".join(
        f"{item.name}（{item.signal_date}）" for item in outcome.templates
    ) or "未生成"
    generated = outcome.finished_at.strftime("%Y-%m-%d %H:%M:%S")
    decision_counts: dict[str, int] = {}
    for candidate in outcome.candidates:
        decision = str(candidate.metrics.get("decision", "未知"))
        decision_counts[decision] = decision_counts.get(decision, 0) + 1
    decision_summary = "".join(
        f"<span>{html.escape(name)} <b>{count}</b></span>"
        for name, count in decision_counts.items()
    )
    if summary.get("selection_mode") == "all":
        scope_summary = (
            f'<span>全市场 <b>{summary.get("universe_supported_rows", 0)}</b></span>'
            f'<span>快照通过 <b>{summary.get("universe_prefilter_pass", 0)}</b></span>'
        )
        quote_scope_notice = "全市场实时快照"
        scope_footer = (
            f'本轮先检查全市场快照 {summary.get("universe_supported_rows", 0)} 只，'
            f'其中 {summary.get("universe_prefilter_pass", 0)} 只进入300日日线精筛；'
            "未通过快照门槛的股票没有执行完整历史形态计算。"
        )
    else:
        scope_summary = f'<span>候选池 <b>{summary.get("candidate_pool", 0)}</b></span>'
        quote_scope_notice = "热点候选实时行情"
        scope_footer = "本轮使用热点候选池模式。"

    def status_list(records: list[dict[str, Any]], empty_text: str) -> str:
        if not records:
            return f'<p class="subtle">{html.escape(empty_text)}</p>'
        items = []
        for record in records[:12]:
            local_link = (
                f'<a class="status-detail-link" href="#stock-{record["code"]}" title="查看本页K线和筛选明细">本页</a>'
                if record["code"] in detail_codes
                else ""
            )
            identity = (
                '<div class="status-identity">'
                f'<a class="status-stock-link" href="{html.escape(record["sohu_url"], quote=True)}" '
                f'target="_blank" rel="noopener noreferrer" title="打开搜狐完整行情与K线">'
                f'<strong>{html.escape(record["name"])}</strong> <span>{record["code"]}</span></a>'
                f'{local_link}</div>'
            )
            items.append(
                f'<li>{identity}<span>{_signed_pct(record["current_change_pct"])} · '
                f'右侧量 {_fmt(record["right_edge_volume_ratio"], 2)}x · '
                f'缺口 {record["gap_condition_count"] or 0}/{record["gap_condition_total"] or 0} · '
                f'{html.escape(str(record["decision"]))}</span></li>'
            )
        if len(records) > 12:
            items.append(
                f'<li class="status-more"><span>其余 {len(records) - 12} 只见下方筛选表</span></li>'
            )
        return '<ul class="status-list">' + "".join(items) + "</ul>"

    status_groups = (
        '<div class="status-groups">'
        f'<section class="status-group"><h3>尚未涨停候选 <span>{len(not_limit_candidates)}</span></h3>'
        f'{status_list(not_limit_candidates, "本轮没有尚未涨停的形态候选")}</section>'
        f'<section class="status-group limit-group"><h3>已涨停形态候选 <span>{len(limit_up_candidates)}</span></h3>'
        f'{status_list(limit_up_candidates, "本轮没有已涨停的形态候选")}</section></div>'
    )
    table_headers = "".join(
        [
            _sortable_header(0, "#", first_direction="asc", initial=True),
            _sortable_header(1, "股票", kind="text", first_direction="asc"),
            _sortable_header(2, "结论", kind="text", first_direction="asc"),
            _sortable_header(3, "当前状态"),
            _sortable_header(4, "当前涨幅"),
            _sortable_header(5, "条件"),
            _sortable_header(6, "高到低 / 当前距高"),
            _sortable_header(7, "底部量"),
            _sortable_header(8, "右侧量"),
            _sortable_header(9, "缺口平台"),
            _sortable_header(10, "缺口后量"),
            _sortable_header(11, "近120日涨/跌停"),
            _sortable_header(12, "有效换手"),
            _sortable_header(13, "资金收敛比"),
            _sortable_header(14, "资金交叉"),
            _sortable_header(15, "红线在上"),
            _sortable_header(16, "总分"),
        ]
    ) + '<th>查看</th>'
    table_script = """<script>
(() => {
  const table = document.getElementById("candidate-table");
  if (!table) return;
  const tbody = table.tBodies[0];
  const rows = Array.from(tbody.rows);
  const sortButtons = Array.from(table.querySelectorAll(".sort-button"));
  const filterButtons = Array.from(document.querySelectorAll("[data-table-filter]"));
  const favoriteButtons = Array.from(document.querySelectorAll("[data-favorite-code]"));
  const copyCodeButtons = Array.from(document.querySelectorAll("[data-copy-code]"));
  const favoriteFilterButton = document.querySelector('[data-table-filter="favorites"]');
  const visibleCount = document.getElementById("visible-count");
  const emptyState = document.getElementById("table-empty");
  const favoriteStorageKey = "ashare-screener:favorites:v1";
  rows.forEach((row, index) => { row.dataset.stableIndex = String(index); });
  let activeFilter = "all";
  let sortState = { column: 0, direction: "asc", type: "number" };

  const loadFavorites = () => {
    try {
      const parsed = JSON.parse(localStorage.getItem(favoriteStorageKey) || "[]");
      const valid = Array.isArray(parsed)
        ? parsed.filter((code) => typeof code === "string" && /^\\d{6}$/.test(code))
        : [];
      return new Set(valid);
    } catch (error) {
      return new Set();
    }
  };
  let favorites = loadFavorites();

  const saveFavorites = () => {
    try {
      localStorage.setItem(favoriteStorageKey, JSON.stringify(Array.from(favorites).sort()));
    } catch (error) {
      // The buttons still work for this page when browser storage is unavailable.
    }
  };

  const syncFavorites = () => {
    rows.forEach((row) => {
      row.dataset.favorite = String(favorites.has(row.dataset.code));
    });
    favoriteButtons.forEach((button) => {
      const selected = favorites.has(button.dataset.favoriteCode);
      const action = selected ? "取消收藏" : "收藏";
      button.textContent = selected ? "★" : "☆";
      button.setAttribute("aria-pressed", String(selected));
      button.setAttribute("aria-label", `${action} ${button.dataset.favoriteName}`);
      button.title = action;
    });
    const currentFavoriteCount = rows.filter((row) => row.dataset.favorite === "true").length;
    favoriteFilterButton.textContent = `收藏 ${currentFavoriteCount}`;
  };

  const compareRows = (left, right) => {
    const leftRaw = left.cells[sortState.column].dataset.sortValue || "";
    const rightRaw = right.cells[sortState.column].dataset.sortValue || "";
    const leftMissing = leftRaw === "";
    const rightMissing = rightRaw === "";
    if (leftMissing !== rightMissing) return leftMissing ? 1 : -1;
    if (leftMissing && rightMissing) {
      return Number(left.dataset.stableIndex) - Number(right.dataset.stableIndex);
    }
    let result;
    if (sortState.type === "number") {
      result = Number(leftRaw) - Number(rightRaw);
    } else {
      result = leftRaw.localeCompare(rightRaw, "zh-CN", { numeric: true });
    }
    if (result === 0) {
      result = Number(left.dataset.stableIndex) - Number(right.dataset.stableIndex);
    }
    return sortState.direction === "asc" ? result : -result;
  };

  const render = () => {
    rows.sort(compareRows).forEach((row) => tbody.appendChild(row));
    let shown = 0;
    rows.forEach((row) => {
      const visible = activeFilter === "all"
        || row.dataset.viewGroup === activeFilter
        || (activeFilter === "favorites" && row.dataset.favorite === "true")
        || (activeFilter === "right-volume"
          && row.dataset.viewGroup === "not-limit"
          && row.dataset.rightVolume === "true")
        || (activeFilter === "gap-setup"
          && row.dataset.viewGroup === "not-limit"
          && row.dataset.gapSetup === "true");
      row.hidden = !visible;
      if (visible) {
        shown += 1;
        row.querySelector(".rank-cell").textContent = String(shown);
      }
    });
    visibleCount.textContent = `显示 ${shown} / ${rows.length}`;
    emptyState.hidden = shown !== 0;
  };

  sortButtons.forEach((button) => {
    button.addEventListener("click", () => {
      const column = Number(button.dataset.column);
      if (sortState.column === column) {
        sortState.direction = sortState.direction === "asc" ? "desc" : "asc";
      } else {
        sortState = {
          column,
          direction: button.dataset.first || "desc",
          type: button.dataset.type || "number",
        };
      }
      sortButtons.forEach((item) => {
        const selected = item === button;
        item.closest("th").setAttribute(
          "aria-sort",
          selected ? (sortState.direction === "asc" ? "ascending" : "descending") : "none",
        );
        item.querySelector(".sort-icon").textContent = selected
          ? (sortState.direction === "asc" ? "↑" : "↓")
          : "↕";
      });
      render();
    });
  });

  filterButtons.forEach((button) => {
    button.addEventListener("click", () => {
      activeFilter = button.dataset.tableFilter;
      filterButtons.forEach((item) => {
        const selected = item === button;
        item.classList.toggle("active", selected);
        item.setAttribute("aria-pressed", String(selected));
      });
      render();
    });
  });

  favoriteButtons.forEach((button) => {
    button.addEventListener("click", () => {
      const code = button.dataset.favoriteCode;
      if (favorites.has(code)) {
        favorites.delete(code);
      } else {
        favorites.add(code);
      }
      saveFavorites();
      syncFavorites();
      render();
    });
  });

  copyCodeButtons.forEach((button) => {
    button.addEventListener("click", async () => {
      const code = button.dataset.copyCode;
      try {
        await navigator.clipboard.writeText(code);
      } catch (error) {
        const input = document.createElement("textarea");
        input.value = code;
        input.style.position = "fixed";
        input.style.opacity = "0";
        document.body.appendChild(input);
        input.select();
        document.execCommand("copy");
        input.remove();
      }
      const original = button.textContent;
      button.textContent = "已复制";
      window.setTimeout(() => { button.textContent = original; }, 1200);
    });
  });

  window.addEventListener("storage", (event) => {
    if (event.key !== favoriteStorageKey) return;
    favorites = loadFavorites();
    syncFavorites();
    render();
  });

  syncFavorites();
  render();
})();
</script>"""
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>A股全市场形态筛选报告</title>
<style>
:root {{ color-scheme: light; --ink:#20242b; --muted:#68717d; --line:#dfe3e8; --soft:#f5f7f9; --red:#b83243; --green:#0e776d; --amber:#9a6700; --blue:#315f98; }}
* {{ box-sizing:border-box; }}
body {{ margin:0; color:var(--ink); background:#fff; font-family:"Microsoft YaHei","PingFang SC",Arial,sans-serif; font-size:14px; letter-spacing:0; }}
header {{ border-bottom:1px solid var(--line); padding:22px 28px 16px; }}
.header-row {{ display:flex; align-items:flex-start; justify-content:space-between; gap:20px; }}
.refresh {{ display:inline-block; padding:8px 12px; color:#fff; background:var(--blue); text-decoration:none; border-radius:4px; white-space:nowrap; }}
h1 {{ margin:0 0 8px; font-size:24px; font-weight:700; }}
h2 {{ font-size:19px; margin:28px 0 12px; }}
h3 {{ font-size:17px; margin:0 0 5px; }}
h3 span {{ color:var(--muted); font-size:13px; font-weight:500; }}
h4 {{ font-size:14px; margin:18px 0 8px; }}
p {{ margin:5px 0; line-height:1.6; }}
main {{ max-width:1220px; margin:0 auto; padding:0 24px 40px; }}
.summary {{ display:flex; flex-wrap:wrap; gap:18px; margin-top:10px; color:var(--muted); }}
.summary b {{ color:var(--ink); }}
.notice {{ padding:10px 12px; border-left:3px solid var(--amber); background:#fff9e8; color:#654a00; }}
.source-note {{ margin:10px 0; padding:10px 12px; border-left:3px solid var(--blue); background:#f2f6fb; color:#34495e; line-height:1.7; }}
.strategy-note {{ margin:16px 0; padding:12px 14px; border-top:1px solid var(--line); border-bottom:1px solid var(--line); background:#f7fafc; line-height:1.7; }}
.strategy-note strong {{ display:block; margin-bottom:3px; }}
.status-groups {{ display:grid; grid-template-columns:1fr 1fr; gap:32px; margin:18px 0 4px; border-top:1px solid var(--line); border-bottom:1px solid var(--line); }}
.status-group {{ padding:16px 0; min-width:0; }}
.status-group h3 span {{ display:inline-block; min-width:22px; text-align:center; color:#fff; background:var(--green); border-radius:3px; padding:1px 5px; }}
.status-group.limit-group h3 span {{ background:var(--red); }}
.status-list {{ list-style:none; margin:8px 0 0; padding:0; }}
.status-list li {{ display:flex; justify-content:space-between; gap:12px; padding:6px 0; border-top:1px solid #edf0f3; font-variant-numeric:tabular-nums; }}
.status-list li > span {{ color:var(--muted); text-align:right; }}
.status-list a {{ color:var(--ink); text-decoration:none; }}
.status-list a:hover {{ color:var(--blue); text-decoration:underline; }}
.status-list a span,.status-stock span {{ color:var(--muted); font-size:12px; }}
.status-identity {{ display:flex; align-items:baseline; gap:7px; min-width:0; }}
.status-detail-link {{ color:var(--blue) !important; font-size:12px; white-space:nowrap; }}
.status-more {{ justify-content:flex-end !important; color:var(--muted); }}
.table-toolbar {{ display:flex; justify-content:space-between; align-items:center; gap:16px; margin:0 0 8px; }}
.segmented {{ display:inline-flex; border:1px solid #bfc7d1; border-radius:4px; overflow:hidden; }}
.segmented button {{ min-height:34px; padding:6px 11px; border:0; border-right:1px solid #bfc7d1; color:#3f4853; background:#fff; font:inherit; cursor:pointer; }}
.segmented button:last-child {{ border-right:0; }}
.segmented button.active {{ color:#fff; background:#315f98; }}
.segmented button:focus-visible, .sort-button:focus-visible, .favorite-button:focus-visible {{ outline:2px solid #1b66b1; outline-offset:2px; }}
.table-wrap {{ overflow-x:auto; border:1px solid var(--line); }}
 table {{ border-collapse:collapse; width:100%; min-width:1640px; }}
th,td {{ padding:9px 10px; border-bottom:1px solid var(--line); text-align:left; vertical-align:top; }}
th {{ background:var(--soft); color:#4c5560; font-size:12px; position:sticky; top:0; }}
.sort-button {{ display:flex; align-items:center; justify-content:space-between; gap:5px; width:100%; min-height:24px; padding:0; border:0; color:inherit; background:transparent; font:inherit; font-weight:600; text-align:inherit; white-space:nowrap; cursor:pointer; }}
.sort-icon {{ width:12px; color:#66717e; text-align:center; }}
.stock-cell,.candidate-identity {{ display:flex; align-items:flex-start; gap:7px; min-width:0; }}
.stock-cell > div,.candidate-identity > div {{ min-width:0; }}
.stock-name-link {{ color:var(--ink); text-decoration:none; }}
.stock-name-link:hover {{ color:var(--blue); text-decoration:underline; }}
.favorite-button {{ display:inline-grid; place-items:center; flex:0 0 28px; width:28px; height:28px; padding:0; border:0; border-radius:3px; color:#7a828c; background:transparent; font:20px/1 Arial,sans-serif; cursor:pointer; }}
.favorite-button:hover {{ color:#9a6700; background:#fff5cf; }}
.favorite-button[aria-pressed="true"] {{ color:#b77900; }}
.stock-actions {{ display:flex; flex-wrap:wrap; gap:5px; margin-top:5px; }}
.stock-actions a,.copy-code-button {{ display:inline-flex; align-items:center; min-height:27px; padding:3px 7px; border:1px solid #c8ced6; border-radius:3px; color:#315f98; background:#fff; font:12px/1.2 "Microsoft YaHei","PingFang SC",Arial,sans-serif; text-decoration:none; cursor:pointer; white-space:nowrap; }}
.stock-actions a:hover,.copy-code-button:hover {{ border-color:#315f98; background:#f2f6fb; }}
.actions-cell {{ min-width:230px; }}
tbody tr:hover {{ background:#fafbfc; }}
[hidden] {{ display:none !important; }}
.numeric {{ text-align:right; font-variant-numeric:tabular-nums; }}
.numeric .sort-button {{ justify-content:flex-end; }}
.subtle {{ color:var(--muted); font-size:12px; }}
.state {{ display:inline-block; padding:2px 6px; border-radius:3px; font-size:12px; white-space:nowrap; }}
.state.positive {{ color:#fff; background:var(--red); }}
.state.clear {{ color:#075e54; background:#e3f4f0; }}
.state.watch {{ color:#fff; background:var(--blue); }}
.state.danger {{ color:#fff; background:var(--amber); }}
.state.muted {{ color:#4d5661; background:#e9edf1; }}
.candidate {{ padding:24px 0 30px; border-bottom:1px solid var(--line); }}
.candidate-head {{ display:flex; justify-content:space-between; gap:20px; align-items:flex-start; }}
.candidate-head p {{ color:var(--muted); }}
.score {{ font-size:28px; font-weight:700; text-align:right; min-width:76px; font-variant-numeric:tabular-nums; }}
.score small {{ display:block; color:var(--muted); font-size:11px; font-weight:500; }}
.metrics {{ display:flex; flex-wrap:wrap; gap:8px 18px; padding:9px 0; color:#4d5661; border-top:1px solid var(--line); border-bottom:1px solid var(--line); }}
.conditions {{ display:flex; flex-wrap:wrap; gap:6px; padding:10px 0 2px; }}
.condition {{ padding:3px 7px; border-radius:3px; font-size:12px; }}
.condition.pass {{ color:#075e54; background:#e3f4f0; }}
.condition.fail {{ color:#7a3039; background:#f8e8ea; }}
.chart {{ width:100%; height:auto; border:1px solid var(--line); display:block; }}
.chart .axis {{ font-size:10px; fill:#707985; }}
.chart .legend, .chart text.legend {{ font-size:11px; fill:#2d333b; }}
.notes {{ display:grid; grid-template-columns:1fr 1fr; gap:28px; }}
ul {{ margin:6px 0 0; padding-left:20px; line-height:1.7; }}
.issues td {{ font-size:12px; }}
footer {{ color:var(--muted); border-top:1px solid var(--line); padding:16px 0; margin-top:30px; line-height:1.7; }}
@media (max-width:720px) {{ header {{ padding:18px 16px 13px; }} main {{ padding:0 12px 28px; }} .status-groups,.notes {{ grid-template-columns:1fr; gap:4px; }} .status-group + .status-group {{ border-top:1px solid var(--line); }} .table-toolbar {{ align-items:flex-start; flex-direction:column; }} .segmented {{ display:grid; grid-template-columns:repeat(6,minmax(0,1fr)); width:100%; gap:1px; background:#bfc7d1; }} .segmented button {{ grid-column:span 2; min-width:0; min-height:46px; padding:6px 7px; border:0; }} .segmented button:nth-last-child(-n+2) {{ grid-column:span 3; }} .candidate-head {{ align-items:center; }} }}
</style>
</head>
<body>
<header>
  <div class="header-row"><div><h1>A股严格形态筛选报告</h1><p class="subtle">浏览器刷新本页会重新抓取数据并执行筛选</p></div><a class="refresh" href="/?force=1">重新扫描</a></div>
  <div class="summary"><span>生成时间 <b>{generated}</b></span><span>状态 <b>{status_text}</b></span>{scope_summary}<span>实时行情 <b>{summary.get('current_quote_rows', 0)}</b></span><span>有效日线 <b>{summary.get('history_success', 0)}</b></span><span>模板 <b>{len(outcome.templates)}</b></span></div>
  <div class="summary">{decision_summary}</div>
</header>
<main>
  <p class="notice">这是基于公开行情的研究候选清单，不是收益承诺或买入建议。当前状态优先使用{quote_scope_notice}的价格与涨跌幅，历史形态继续使用前复权日线；“待资金数据”不是完整匹配。</p>
  <div class="source-note"><strong>行情复核：</strong>点击股票名称打开搜狐日/周/月K线、盘口与公司资料；“东财K线/F10”提供指标K线、资金、公告和F10。当前选股数据来自 AkShare 封装的腾讯与东方财富接口，不是华泰证券数据；也可复制六位代码后在华泰客户端直接输入。</div>
  <div class="strategy-note"><strong>整体筛选提示</strong>第一门槛仍是高位大跌后处于底部；资金侧优先四线靠近、交织不乱且红线上穿。在此前提下，再优选近期向上跳空、收盘不回补、缺口上方横盘、均线转为向上且缺口后持续放量的形态。平潭发展仅作为“缺口平台”样本，不能覆盖第一门槛；光迅科技作为底部结构样本，其新缺口仍需等待横盘确认。</div>
  {status_groups}
  <h2>候选排序</h2>
  <div class="table-toolbar"><div class="segmented" role="group" aria-label="候选分组">
    <button type="button" class="active" data-table-filter="all" aria-pressed="true">全部 {len(candidates)}</button>
    <button type="button" data-table-filter="favorites" aria-pressed="false">收藏 0</button>
    <button type="button" data-table-filter="not-limit" aria-pressed="false">尚未涨停 {len(not_limit_candidates)}</button>
    <button type="button" data-table-filter="right-volume" aria-pressed="false">右侧放量 {len(right_volume_candidates)}</button>
    <button type="button" data-table-filter="gap-setup" aria-pressed="false">缺口趋势 {len(gap_setup_candidates)}</button>
    <button type="button" data-table-filter="limit-up" aria-pressed="false">已涨停形态 {len(limit_up_candidates)}</button>
  </div><span id="visible-count" class="subtle"></span></div>
  <div class="table-wrap"><table id="candidate-table"><thead><tr>{table_headers}</tr></thead><tbody>{''.join(table_rows)}</tbody></table></div>
  <p id="table-empty" class="subtle" hidden>该分组本轮没有候选。</p>
  <p class="subtle">参考模板：{html.escape(template_text)}</p>
  <h2>逐股复核（前 {detail_count} 只）</h2>
  {''.join(details)}
  <h2>数据问题</h2>
  <div class="table-wrap"><table class="issues"><thead><tr><th>环节</th><th>代码</th><th>信息</th></tr></thead><tbody>{issue_rows}</tbody></table></div>
  <footer>{html.escape(scope_footer)} 排序只比较进入日线精筛的股票，并会随行情变化。第三方接口字段或访问限制变化时，报告会显示失败项；数据不足的股票不会被静默当作低分股票处理。</footer>
</main>
{table_script}
</body>
</html>"""
