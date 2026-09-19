from __future__ import annotations

import math
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Iterable

import numpy as np
import pandas as pd

from ashare_screener.config import DEFAULT_GAP_RULES, DEFAULT_STRICT_RULES
from ashare_screener.models import ReferenceTemplate


FLOW_COLUMNS = ("super_large_pct", "large_pct", "medium_pct", "small_pct")


def _number(value: object, default: float = float("nan")) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def _clip(value: float, lower: float, upper: float) -> float:
    return float(np.clip(value, lower, upper))


def board_limit_rate(code: str | None) -> float:
    code = str(code or "")
    if code.startswith(("300", "301", "302", "688", "689")):
        return 0.20
    if code.startswith(("4", "8", "9")):
        return 0.30
    return 0.10


def daily_limit_up_price(previous_close: object, code: str | None) -> float:
    """Return the exchange price cap rounded to the A-share price tick."""
    numeric = _number(previous_close)
    if not math.isfinite(numeric) or numeric <= 0:
        return float("nan")
    try:
        price = Decimal(str(numeric)) * (
            Decimal("1") + Decimal(str(board_limit_rate(code)))
        )
        return float(price.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
    except (InvalidOperation, ValueError):
        return float("nan")


def _quote_day(value: object) -> pd.Timestamp | None:
    if value is None:
        return None
    try:
        timestamp = pd.Timestamp(value)
    except (TypeError, ValueError):
        return None
    if pd.isna(timestamp):
        return None
    return timestamp.normalize()


def _overlay_current_quote(
    history: pd.DataFrame,
    *,
    quote_latest: object,
    quote_date: object,
) -> tuple[pd.DataFrame, bool]:
    """Overlay a newer quote without inventing volume or turnover data."""
    frame = history.sort_values("date").copy()
    latest = _number(quote_latest)
    day = _quote_day(quote_date)
    if not math.isfinite(latest) or latest <= 0 or day is None or frame.empty:
        return frame, False

    history_day = pd.Timestamp(frame.iloc[-1]["date"]).normalize()
    if day < history_day:
        return frame, False
    if day == history_day:
        index = frame.index[-1]
        frame.loc[index, "close"] = latest
        frame.loc[index, "high"] = max(_number(frame.loc[index, "high"]), latest)
        frame.loc[index, "low"] = min(_number(frame.loc[index, "low"]), latest)
        return frame, True

    quote_row = {
        "date": day,
        "open": latest,
        "close": latest,
        "high": latest,
        "low": latest,
        "volume": float("nan"),
        "amount": float("nan"),
        "turnover": float("nan"),
    }
    return pd.concat([frame, pd.DataFrame([quote_row])], ignore_index=True), True


def calculate_gap_setup_metrics(
    history: pd.DataFrame,
    *,
    current_close: float,
    ma5: float,
    ma10: float,
    ma20: float,
    ma20_slope_5d: float,
    rules: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Measure a recent gap-up platform without overriding the primary gate."""
    gap_rules = DEFAULT_GAP_RULES | (rules or {})
    frame = (
        history.sort_values("date")
        .dropna(subset=["date", "high", "low", "close"])
        .reset_index(drop=True)
    )
    empty_conditions = {
        "recent_gap_up": False,
        "gap_close_unfilled": False,
        "gap_sideways_holding": False,
        "gap_uptrend": False,
        "gap_sustained_volume": False,
    }
    empty_result: dict[str, Any] = {
        "gap_date": None,
        "gap_floor": float("nan"),
        "gap_ceiling": float("nan"),
        "gap_size_pct": float("nan"),
        "gap_bars_since": 0,
        "gap_close_unfilled": False,
        "gap_intraday_unfilled": False,
        "gap_hold_confirmed": False,
        "gap_close_range": float("nan"),
        "gap_post_volume_ratio": float("nan"),
        "gap_post_volume_active_fraction": float("nan"),
        "gap_setup_match": False,
        "gap_setup_near": False,
        "gap_condition_count": 0,
        "gap_condition_total": len(empty_conditions),
        "gap_preference_score": 0.0,
        "gap_conditions": empty_conditions,
    }
    if len(frame) < 3:
        return empty_result

    lookback = int(gap_rules["lookback_days"])
    start = max(1, len(frame) - lookback)
    gap_index: int | None = None
    for index in range(len(frame) - 1, start - 1, -1):
        previous_high = _number(frame.iloc[index - 1]["high"])
        gap_low = _number(frame.iloc[index]["low"])
        if previous_high <= 0:
            continue
        if gap_low / previous_high - 1 >= gap_rules["minimum_gap_pct"]:
            gap_index = index
            break
    if gap_index is None:
        return empty_result

    previous_high = _number(frame.iloc[gap_index - 1]["high"])
    gap_low = _number(frame.iloc[gap_index]["low"])
    gap_size = gap_low / previous_high - 1
    post = frame.iloc[gap_index:].copy()
    closing_values = post["close"].to_numpy(dtype=float)
    if math.isfinite(current_close) and current_close > 0:
        closing_values = np.append(closing_values, current_close)
    fill_floor = previous_high * (1 - gap_rules["close_fill_tolerance"])
    close_unfilled = bool(np.nanmin(closing_values) >= fill_floor)
    intraday_unfilled = bool(_number(post["low"].min()) >= fill_floor)
    bars_since = len(frame) - 1 - gap_index
    hold_confirmed = bool(
        close_unfilled and bars_since >= int(gap_rules["hold_min_days"])
    )
    minimum_close = float(np.nanmin(closing_values))
    close_range = (
        float(np.nanmax(closing_values)) / minimum_close - 1
        if minimum_close > 0
        else float("nan")
    )
    sideways_holding = bool(
        hold_confirmed
        and close_range <= gap_rules["consolidation_close_range_max"]
    )

    uptrend = bool(
        all(math.isfinite(value) for value in (current_close, ma5, ma10, ma20))
        and current_close >= ma5 * 0.995
        and ma5 >= ma10 * 0.995
        and ma10 >= ma20 * 0.995
        and ma20_slope_5d > 0
    )

    pre_volume = pd.to_numeric(
        frame.iloc[max(0, gap_index - 20) : gap_index]["volume"],
        errors="coerce",
    ).dropna()
    post_volume = pd.to_numeric(post["volume"], errors="coerce").dropna()
    pre_volume_median = _number(pre_volume.median())
    post_volume_median = _number(post_volume.median())
    post_volume_ratio = (
        post_volume_median / pre_volume_median
        if pre_volume_median > 0
        else float("nan")
    )
    active_fraction = (
        _number((post_volume >= pre_volume_median * 1.2).mean())
        if pre_volume_median > 0 and not post_volume.empty
        else float("nan")
    )
    sustained_volume = bool(
        len(post_volume) >= int(gap_rules["post_volume_min_days"])
        and post_volume_ratio >= gap_rules["post_volume_ratio_min"]
        and active_fraction >= gap_rules["post_volume_active_fraction_min"]
    )

    conditions = {
        "recent_gap_up": True,
        "gap_close_unfilled": close_unfilled,
        "gap_sideways_holding": sideways_holding,
        "gap_uptrend": uptrend,
        "gap_sustained_volume": sustained_volume,
    }
    condition_count = int(sum(conditions.values()))
    setup_match = all(conditions.values())
    setup_near = bool(
        close_unfilled and uptrend and condition_count >= 3
    )
    return {
        "gap_date": pd.Timestamp(frame.iloc[gap_index]["date"]).date().isoformat(),
        "gap_floor": previous_high,
        "gap_ceiling": gap_low,
        "gap_size_pct": gap_size,
        "gap_bars_since": bars_since,
        "gap_close_unfilled": close_unfilled,
        "gap_intraday_unfilled": intraday_unfilled,
        "gap_hold_confirmed": hold_confirmed,
        "gap_close_range": close_range,
        "gap_post_volume_ratio": post_volume_ratio,
        "gap_post_volume_active_fraction": active_fraction,
        "gap_setup_match": setup_match,
        "gap_setup_near": setup_near,
        "gap_condition_count": condition_count,
        "gap_condition_total": len(conditions),
        "gap_preference_score": round(condition_count / len(conditions) * 100, 2),
        "gap_conditions": conditions,
    }


def calculate_price_metrics(
    history: pd.DataFrame,
    *,
    code: str | None = None,
    rules: dict[str, float] | None = None,
    gap_rules: dict[str, float] | None = None,
    quote_latest: object = None,
    quote_change_pct: object = None,
    quote_date: object = None,
) -> dict[str, Any]:
    if len(history) < 90:
        raise ValueError("至少需要 90 根有效日线")
    strict_rules = DEFAULT_STRICT_RULES | (rules or {})
    history_frame = history.sort_values("date").copy()
    history_as_of = pd.Timestamp(history_frame.iloc[-1]["date"]).date().isoformat()
    frame, quote_price_applied = _overlay_current_quote(
        history_frame,
        quote_latest=quote_latest,
        quote_date=quote_date,
    )
    for length in (5, 10, 20, 60):
        frame[f"ma{length}"] = frame["close"].rolling(length).mean()

    previous_close = frame["close"].shift(1)
    true_range = pd.concat(
        [
            frame["high"] - frame["low"],
            (frame["high"] - previous_close).abs(),
            (frame["low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    frame["atr14"] = true_range.rolling(14).mean()

    recent = frame.tail(min(250, len(frame))).copy()
    running_peak = recent["high"].cummax()
    drawdowns = recent["low"] / running_peak - 1
    trough_position = int(np.nanargmin(drawdowns.to_numpy(dtype=float)))
    pre_trough = recent.iloc[: trough_position + 1]
    peak_position = int(np.nanargmax(pre_trough["high"].to_numpy(dtype=float)))
    peak_row = pre_trough.iloc[peak_position]
    trough_row = recent.iloc[trough_position]
    decline_peak_price = _number(peak_row["high"])
    trough_price = _number(trough_row["low"])
    max_drawdown = (
        trough_price / decline_peak_price - 1
        if decline_peak_price > 0
        else 0.0
    )
    decline_peak_date = pd.Timestamp(peak_row["date"]).date().isoformat()
    trough_date = pd.Timestamp(trough_row["date"]).date().isoformat()
    bars_since_trough = len(recent) - 1 - trough_position

    current = frame.iloc[-1]
    current_close = _number(current["close"])
    previous_high_20 = _number(frame["high"].iloc[-21:-1].max())
    previous_volume_20 = _number(frame["volume"].iloc[-21:-1].mean())
    recent_high_250 = _number(frame["high"].tail(250).max())

    ma_values = np.array(
        [_number(current["ma5"]), _number(current["ma10"]), _number(current["ma20"])],
        dtype=float,
    )
    ma_spread = (
        _number((np.nanmax(ma_values) - np.nanmin(ma_values)) / current_close)
        if current_close > 0
        else float("nan")
    )

    base_slice = frame.iloc[-30:-3] if len(frame) >= 35 else frame.tail(25)
    base_low = _number(base_slice["low"].min())
    base_high = _number(base_slice["high"].max())
    base_range = base_high / base_low - 1 if base_low > 0 else float("nan")

    bottom_volume = frame["volume"].tail(20)
    prior_volume = frame["volume"].iloc[-90:-20]
    prior_volume_median = _number(prior_volume.median())
    bottom_volume_median = _number(bottom_volume.median())
    bottom_volume_ratio = (
        bottom_volume_median / prior_volume_median
        if prior_volume_median > 0
        else float("nan")
    )
    bottom_high_volume_days = (
        int((bottom_volume >= prior_volume_median * 1.5).sum())
        if prior_volume_median > 0
        else 0
    )

    valid_volume = frame[["date", "volume"]].dropna(subset=["volume"])
    if len(valid_volume) >= 23:
        right_edge_baseline = _number(valid_volume["volume"].iloc[-23:-3].median())
        latest_volume = _number(valid_volume["volume"].iloc[-1])
        recent_3d_volume = _number(valid_volume["volume"].iloc[-3:].mean())
        latest_volume_ratio = (
            latest_volume / right_edge_baseline
            if right_edge_baseline > 0
            else float("nan")
        )
        recent_3d_volume_ratio = (
            recent_3d_volume / right_edge_baseline
            if right_edge_baseline > 0
            else float("nan")
        )
        right_edge_volume_ratio = max(latest_volume_ratio, recent_3d_volume_ratio)
        volume_as_of = pd.Timestamp(valid_volume.iloc[-1]["date"]).date().isoformat()
    else:
        latest_volume_ratio = float("nan")
        recent_3d_volume_ratio = float("nan")
        right_edge_volume_ratio = float("nan")
        volume_as_of = None
    right_edge_volume_expanded = bool(
        math.isfinite(right_edge_volume_ratio)
        and right_edge_volume_ratio >= strict_rules["right_edge_volume_ratio_min"]
    )

    ma20_now = _number(current["ma20"])
    ma20_5d_ago = _number(frame["ma20"].iloc[-6])
    ma20_slope_5d = ma20_now / ma20_5d_ago - 1 if ma20_5d_ago > 0 else 0.0
    volume_ratio = (
        _number(current["volume"]) / previous_volume_20
        if previous_volume_20 > 0
        else float("nan")
    )
    gap_metrics = calculate_gap_setup_metrics(
        history_frame,
        current_close=current_close,
        ma5=_number(current["ma5"]),
        ma10=_number(current["ma10"]),
        ma20=ma20_now,
        ma20_slope_5d=ma20_slope_5d,
        rules=gap_rules,
    )

    daily_return = frame["close"].pct_change()
    previous_close_series = frame["close"].shift(1)
    rate = board_limit_rate(code)
    touch_threshold = rate * 0.92
    limit_window = frame.tail(120)
    limit_previous_close = previous_close_series.loc[limit_window.index]
    limit_up_count = int(
        ((limit_window["high"] / limit_previous_close - 1) >= touch_threshold).sum()
    )
    limit_down_count = int(
        ((limit_window["low"] / limit_previous_close - 1) <= -touch_threshold).sum()
    )
    return_1d = _number(daily_return.iloc[-1], 0.0)
    return_5d = current_close / _number(frame["close"].iloc[-6]) - 1
    return_20d = current_close / _number(frame["close"].iloc[-21]) - 1
    extension_ma20 = (
        current_close / ma20_now - 1 if ma20_now > 0 else float("nan")
    )
    turnover_5d_avg = _number(frame["turnover"].tail(5).mean())
    turnover_10d_avg = _number(frame["turnover"].tail(10).mean())
    latest_turnover = _number(current.get("turnover"))
    turnover_candidates = [
        value
        for value in (latest_turnover, turnover_5d_avg, turnover_10d_avg)
        if math.isfinite(value)
    ]
    turnover_signal = (
        min(turnover_candidates, key=lambda value: abs(value - 10.0))
        if turnover_candidates
        else float("nan")
    )
    recovery_from_trough = (
        current_close / trough_price - 1 if trough_price > 0 else float("nan")
    )
    current_drawdown_from_decline_peak = (
        current_close / decline_peak_price - 1
        if decline_peak_price > 0
        else float("nan")
    )
    limit_price = daily_limit_up_price(frame["close"].iloc[-2], code)
    limit_up_from_price = bool(
        math.isfinite(limit_price) and current_close >= limit_price - 0.005
    )
    quote_pct = _number(quote_change_pct)
    quote_is_current = bool(
        _quote_day(quote_date) is not None
        and _quote_day(quote_date)
        >= pd.Timestamp(history_frame.iloc[-1]["date"]).normalize()
        and math.isfinite(quote_pct)
    )
    # Percentage fallback covers adjusted-history mismatches and concept-only quotes.
    limit_up_from_quote = bool(
        quote_is_current and quote_pct >= rate * 100 - 0.5
    )
    limit_up_today = limit_up_from_quote if quote_is_current else limit_up_from_price
    entry_late = bool(
        limit_up_today
        or return_5d > strict_rules["max_return_5d"]
        or extension_ma20 > strict_rules["max_extension_ma20"]
        or recovery_from_trough > strict_rules["max_recovery_from_trough"]
        or current_drawdown_from_decline_peak
        > -strict_rules["current_below_decline_peak_min"]
    )

    drawdown_around_half = bool(
        strict_rules["drawdown_min"]
        <= -max_drawdown
        <= strict_rules["drawdown_max"]
    )
    bottom_consolidated = bool(
        base_range <= 0.35
        and recovery_from_trough <= strict_rules["max_recovery_from_trough"]
    )
    decline_into_current_base = bool(
        drawdown_around_half
        and bottom_consolidated
        and current_drawdown_from_decline_peak
        <= -strict_rules["current_below_decline_peak_min"]
    )
    price_conditions = {
        "decline_into_current_base": decline_into_current_base,
        "drawdown_around_half": drawdown_around_half,
        "bottom_volume_expanded": bool(
            bottom_volume_ratio >= strict_rules["bottom_volume_ratio_min"]
            or bottom_high_volume_days
            >= int(strict_rules["bottom_high_volume_days_min"])
        ),
        "repeated_limit_activity": bool(
            (
                limit_up_count >= int(strict_rules["limit_up_min"])
                and limit_down_count >= int(strict_rules["limit_down_min"])
            )
            or (limit_up_count + limit_down_count >= 4 and limit_up_count >= 1)
        ),
        "turnover_near_target": bool(
            strict_rules["turnover_avg_min"]
            <= turnover_signal
            <= strict_rules["turnover_avg_max"]
        ),
        "bottom_consolidated": bottom_consolidated,
        "entry_not_late": not entry_late,
    }
    core_names = (
        "decline_into_current_base",
        "bottom_volume_expanded",
        "repeated_limit_activity",
        "turnover_near_target",
        "bottom_consolidated",
    )
    price_core_match = all(price_conditions[name] for name in core_names)

    result: dict[str, Any] = {
        "as_of": pd.Timestamp(current["date"]).date().isoformat(),
        "history_as_of": history_as_of,
        "close": current_close,
        "current_change_pct": quote_pct if quote_is_current else return_1d * 100,
        "current_status_source": "实时行情" if quote_is_current else "日线",
        "quote_price_applied": quote_price_applied,
        "ma5": _number(current["ma5"]),
        "ma10": _number(current["ma10"]),
        "ma20": ma20_now,
        "ma60": _number(current["ma60"]),
        "ma_spread": ma_spread,
        "ma20_slope_5d": ma20_slope_5d,
        "max_drawdown_250": max_drawdown,
        "max_drawdown_180": max_drawdown,
        "current_drawdown_from_250d_high": (
            current_close / recent_high_250 - 1
            if recent_high_250 > 0
            else float("nan")
        ),
        "decline_peak_date": decline_peak_date,
        "decline_peak_price": decline_peak_price,
        "trough_date": trough_date,
        "trough_price": trough_price,
        "bars_since_trough": bars_since_trough,
        "current_drawdown_from_decline_peak": current_drawdown_from_decline_peak,
        "recovery_from_trough": recovery_from_trough,
        "base_range": base_range,
        "bottom_volume_ratio": bottom_volume_ratio,
        "bottom_high_volume_days": bottom_high_volume_days,
        "latest_volume_ratio": latest_volume_ratio,
        "recent_3d_volume_ratio": recent_3d_volume_ratio,
        "right_edge_volume_ratio": right_edge_volume_ratio,
        "right_edge_volume_expanded": right_edge_volume_expanded,
        "volume_as_of": volume_as_of,
        "preference_conditions": {
            "right_edge_volume_expanded": right_edge_volume_expanded,
            **gap_metrics["gap_conditions"],
        },
        **gap_metrics,
        "breakout_ratio": (
            current_close / previous_high_20 if previous_high_20 > 0 else float("nan")
        ),
        "volume_ratio": volume_ratio,
        "atr_pct": (
            _number(current["atr14"]) / current_close if current_close > 0 else float("nan")
        ),
        "return_1d": return_1d,
        "return_5d": return_5d,
        "return_20d": return_20d,
        "return_60d": current_close / _number(frame["close"].iloc[-61]) - 1,
        "extension_ma20": extension_ma20,
        "distance_120d_high": (
            current_close / recent_high_250 - 1
            if recent_high_250 > 0
            else float("nan")
        ),
        "turnover": latest_turnover,
        "turnover_5d_avg": turnover_5d_avg,
        "turnover_10d_avg": turnover_10d_avg,
        "turnover_signal": turnover_signal,
        "board_limit_rate": rate,
        "limit_up_price": limit_price,
        "limit_up_count_120": limit_up_count,
        "limit_down_count_120": limit_down_count,
        "limit_up_today": limit_up_today,
        "entry_late": entry_late,
        "price_conditions": price_conditions,
        "price_condition_count": int(sum(price_conditions.values())),
        "price_condition_total": len(price_conditions),
        "price_core_match": price_core_match,
        "primary_structure_match": decline_into_current_base,
    }
    score, stage, reasons, risks = score_price_pattern(result, strict_rules)
    result["technical_score"] = score
    result["stage"] = stage
    result["technical_reasons"] = reasons
    result["technical_risks"] = risks
    return result


def score_price_pattern(
    metrics: dict[str, Any],
    rules: dict[str, float] | None = None,
) -> tuple[float, str, list[str], list[str]]:
    strict_rules = DEFAULT_STRICT_RULES | (rules or {})
    reasons: list[str] = []
    risks: list[str] = []
    drawdown = -_number(metrics.get("max_drawdown_250"), 0.0)
    base_range = _number(metrics.get("base_range"), 1.0)
    bottom_volume_ratio = _number(metrics.get("bottom_volume_ratio"), 0.0)
    high_volume_days = int(_number(metrics.get("bottom_high_volume_days"), 0.0))
    latest_volume_ratio = _number(metrics.get("latest_volume_ratio"), 0.0)
    recent_3d_volume_ratio = _number(metrics.get("recent_3d_volume_ratio"), 0.0)
    right_edge_volume_ratio = _number(metrics.get("right_edge_volume_ratio"), 0.0)
    limit_up_count = int(_number(metrics.get("limit_up_count_120"), 0.0))
    limit_down_count = int(_number(metrics.get("limit_down_count_120"), 0.0))
    turnover_avg = _number(metrics.get("turnover_signal"), 0.0)
    ma20_slope = _number(metrics.get("ma20_slope_5d"), 0.0)
    breakout = _number(metrics.get("breakout_ratio"), 0.0)
    close = _number(metrics.get("close"), 0.0)
    ma20 = _number(metrics.get("ma20"), 0.0)
    recovery = _number(metrics.get("recovery_from_trough"), 1.0)
    conditions = metrics.get("price_conditions", {})
    entry_late = bool(metrics.get("entry_late"))
    gap_conditions = metrics.get("gap_conditions", {})
    gap_preference_score = _number(metrics.get("gap_preference_score"), 0.0)

    drawdown_midpoint = (
        strict_rules["drawdown_min"] + strict_rules["drawdown_max"]
    ) / 2
    drawdown_half_width = (
        strict_rules["drawdown_max"] - strict_rules["drawdown_min"]
    ) / 2
    decline_score = 18 * _clip(
        1 - abs(drawdown - drawdown_midpoint) / max(drawdown_half_width, 0.01),
        0,
        1,
    )
    if conditions.get("drawdown_around_half"):
        reasons.append(f"高位至低点回撤 {drawdown:.0%}，接近目标区间")
    else:
        risks.append(f"高低回撤 {drawdown:.0%}，不在 35%-65% 目标区间")

    current_vs_peak = _number(
        metrics.get("current_drawdown_from_decline_peak"), 0.0
    )
    if conditions.get("decline_into_current_base"):
        reasons.append(
            f"当前仍低于该轮高点 {-current_vs_peak:.0%}，处于下跌后的底部区"
        )
    else:
        risks.append(
            f"第一门槛未通过：当前距该轮高点 {current_vs_peak:+.0%}，"
            f"低点后已反弹 {recovery:.0%}"
        )

    base_score = 12 * _clip((0.40 - base_range) / 0.25, 0, 1)
    if conditions.get("bottom_consolidated"):
        reasons.append(
            f"底部区间 {base_range:.1%}，低点后反弹 {recovery:.1%}"
        )
    else:
        risks.append("价格尚未形成紧凑底部，或已离低点过远")

    volume_score = 8 * _clip((bottom_volume_ratio - 0.75) / 0.65, 0, 1)
    if conditions.get("bottom_volume_expanded"):
        reasons.append(
            f"底部量能为此前的 {bottom_volume_ratio:.2f} 倍，放量日 {high_volume_days} 天"
        )
    else:
        risks.append("底部量能没有持续扩张")

    right_edge_volume_score = 7 * _clip(
        (right_edge_volume_ratio - 0.8) / 1.0, 0, 1
    )
    if metrics.get("right_edge_volume_expanded"):
        reasons.append(
            f"右侧最新量 {latest_volume_ratio:.2f} 倍、3日均量 "
            f"{recent_3d_volume_ratio:.2f} 倍"
        )
    else:
        risks.append(
            f"右侧量能不足：最新 {latest_volume_ratio:.2f} 倍、"
            f"3日均量 {recent_3d_volume_ratio:.2f} 倍"
        )

    limit_score = 11 * _clip(limit_up_count / 2, 0, 1)
    limit_score += 7 * _clip(limit_down_count, 0, 1)
    if conditions.get("repeated_limit_activity"):
        reasons.append(
            f"近120日涨停触及 {limit_up_count} 次、跌停触及 {limit_down_count} 次"
        )
    else:
        risks.append(
            f"极端博弈不足：涨停触及 {limit_up_count} 次、跌停触及 {limit_down_count} 次"
        )

    turnover_score = 12 * _clip(1 - abs(turnover_avg - 10.0) / 10.0, 0, 1)
    if conditions.get("turnover_near_target"):
        reasons.append(f"近期有效换手 {turnover_avg:.1f}%，接近10%目标")
    else:
        risks.append(f"近期有效换手 {turnover_avg:.1f}%，偏离 5%-18% 区间")

    trend_score = 0.0
    if close >= ma20:
        trend_score += 4
    if ma20_slope > 0:
        trend_score += 4
    if trend_score >= 8:
        reasons.append("价格站上20日线且20日线抬升")
    elif ma20_slope < -0.02:
        risks.append("20日线仍明显向下")

    breakout_score = 7 * _clip(1 - abs(breakout - 1.0) / 0.18, 0, 1)
    if 0.94 <= breakout <= 1.08:
        reasons.append("价格靠近底部平台上沿")

    entry_score = 0.0 if entry_late else 10.0
    if entry_late:
        risks.append("当日或近5日已经明显拉升，低位介入窗口已滞后")
    else:
        reasons.append("当前尚未涨停或明显远离20日线")

    gap_bonus = 12 * _clip(gap_preference_score / 100, 0, 1)
    gap_date = metrics.get("gap_date")
    if gap_date:
        reasons.append(
            f"{gap_date}形成向上缺口 {_number(metrics.get('gap_size_pct'), 0.0):.1%}"
        )
        if gap_conditions.get("gap_close_unfilled"):
            reasons.append(
                f"缺口后 {_number(metrics.get('gap_bars_since'), 0.0):.0f} 个交易日收盘未回补"
            )
        else:
            risks.append("最近向上缺口已被收盘有效回补")
        if (
            gap_conditions.get("gap_close_unfilled")
            and not metrics.get("gap_intraday_unfilled")
        ):
            risks.append("缺口收盘仍守住，但盘中下影线曾进入缺口")
        if gap_conditions.get("gap_sideways_holding"):
            reasons.append(
                f"缺口上方横盘，收盘区间振幅 {_number(metrics.get('gap_close_range'), 0.0):.1%}"
            )
        if gap_conditions.get("gap_uptrend"):
            reasons.append("缺口平台保持向上均线趋势")
        if gap_conditions.get("gap_sustained_volume"):
            reasons.append(
                f"缺口后成交量中位数为此前 {_number(metrics.get('gap_post_volume_ratio'), 0.0):.2f} 倍"
            )

    score = sum(
        (
            decline_score,
            base_score,
            volume_score,
            right_edge_volume_score,
            limit_score,
            turnover_score,
            trend_score,
            breakout_score,
            entry_score,
            gap_bonus,
        )
    )
    if not conditions.get("decline_into_current_base"):
        score = min(score, 49.0)

    condition_count = int(metrics.get("price_condition_count", 0))
    if entry_late:
        stage = "已启动"
    elif bool(metrics.get("price_core_match")) and 0.94 <= breakout <= 1.08:
        stage = "启动前"
    elif condition_count >= 4:
        stage = "接近"
    elif conditions.get("drawdown_around_half") and conditions.get(
        "bottom_consolidated"
    ):
        stage = "筑底"
    else:
        stage = "不匹配"
    return round(_clip(score, 0, 100), 2), stage, reasons, risks


def build_fund_proxy(frame: pd.DataFrame) -> pd.DataFrame:
    if len(frame) < 8:
        raise ValueError("资金流数据至少需要 8 个交易日")
    missing = set(FLOW_COLUMNS) - set(frame.columns)
    if missing:
        raise ValueError(f"资金流缺少字段: {', '.join(sorted(missing))}")
    result = frame[["date", *FLOW_COLUMNS]].copy().sort_values("date")
    for column in FLOW_COLUMNS:
        values = pd.to_numeric(result[column], errors="coerce").fillna(0.0)
        result[f"proxy_{column}"] = (
            values.ewm(span=3, adjust=False).mean().rolling(5, min_periods=2).sum()
        )
    return result.dropna().reset_index(drop=True)


def calculate_fund_metrics(
    frame: pd.DataFrame, rules: dict[str, float] | None = None
) -> dict[str, Any]:
    strict_rules = DEFAULT_STRICT_RULES | (rules or {})
    proxy = build_fund_proxy(frame)
    names = [f"proxy_{column}" for column in FLOW_COLUMNS]
    latest = proxy.iloc[-1]
    super_line = proxy["proxy_super_large_pct"]
    others = proxy[[name for name in names if name != "proxy_super_large_pct"]]
    on_top = super_line > others.max(axis=1)
    rising = _number(super_line.iloc[-1]) > _number(super_line.iloc[-4])
    recent_cross = bool(on_top.iloc[-1] and (~on_top.tail(7).iloc[:-1]).any())
    persistence = int(on_top.tail(5).sum())

    daily_spread = proxy[names].max(axis=1) - proxy[names].min(axis=1)
    recent_spread = _number(daily_spread.tail(5).median())
    baseline_slice = daily_spread.tail(60).iloc[:-5]
    baseline_spread = _number(baseline_slice.median())
    tightness_ratio = (
        recent_spread / baseline_spread if baseline_spread > 0 else float("nan")
    )
    relative_compact = bool(
        math.isfinite(tightness_ratio)
        and tightness_ratio <= strict_rules["fund_tightness_ratio_max"]
    )
    absolute_compact = recent_spread <= strict_rules["fund_absolute_spread_max"]
    adhesion = relative_compact or absolute_compact

    crossing_window = proxy[names].tail(12)
    cross_count = 0
    for left_index, left_name in enumerate(names):
        for right_name in names[left_index + 1 :]:
            difference = crossing_window[left_name] - crossing_window[right_name]
            signs = np.sign(difference).replace(0, np.nan).ffill().bfill()
            cross_count += int((signs * signs.shift(1) < 0).sum())
    interwoven = bool(
        int(strict_rules["fund_cross_min"])
        <= cross_count
        <= int(strict_rules["fund_cross_max"])
    )
    chaotic = cross_count > int(strict_rules["fund_cross_max"])

    red_cross_count = 0
    red_window = proxy.tail(8)
    for other_name in names[1:]:
        difference = red_window["proxy_super_large_pct"] - red_window[other_name]
        red_cross_count += int(
            ((difference > 0) & (difference.shift(1) <= 0)).sum()
        )

    latest_super = _number(latest["proxy_super_large_pct"])
    latest_other_max = _number(others.iloc[-1].max())
    fund_shape_match = bool(
        adhesion
        and interwoven
        and bool(on_top.iloc[-1])
        and (recent_cross or red_cross_count >= 1)
    )
    fund_near_match = bool(
        adhesion
        and not chaotic
        and (bool(on_top.iloc[-1]) or rising or red_cross_count >= 1)
    )
    fund_conditions = {
        "four_lines_compact": adhesion,
        "crossings_orderly": interwoven,
        "red_line_on_top": bool(on_top.iloc[-1]),
        "red_line_recent_cross": bool(recent_cross or red_cross_count >= 1),
    }

    score = 0.0
    reasons: list[str] = []
    risks: list[str] = []
    if adhesion:
        score += 35
        if relative_compact:
            reasons.append(
                f"四线近期收敛，离散度为历史基准的 {tightness_ratio:.2f} 倍"
            )
        else:
            reasons.append(f"四线近期绝对离散度较低（{recent_spread:.1f}）")
    else:
        risks.append(
            f"四线仍较分散（近期离散 {recent_spread:.1f}，基准 {baseline_spread:.1f}）"
        )
    if interwoven:
        score += 15
        reasons.append(f"近12日四线交叉 {cross_count} 次，交织但不凌乱")
    elif chaotic:
        risks.append(f"近12日四线交叉 {cross_count} 次，变化过于凌乱")
    else:
        risks.append("四线靠近但缺少交织过程")
    if bool(on_top.iloc[-1]):
        score += 20
        reasons.append("超大单代理线位于四线顶部")
    else:
        risks.append("超大单代理线当前未处于顶部")
    if rising:
        score += 10
        reasons.append("超大单代理线近 4 日上升")
    else:
        risks.append("超大单代理线斜率转弱")
    if recent_cross or red_cross_count >= 1:
        score += 20
        reasons.append(f"红线近期向上交叉其他资金线 {red_cross_count} 次")
    else:
        risks.append("红线近期没有形成明确向上交叉")
    if persistence >= 3:
        reasons.append(f"近 5 日有 {persistence} 日保持领先")

    return {
        "fund_score": round(_clip(score, 0, 100), 2),
        "fund_super_latest": latest_super,
        "fund_top_margin": latest_super - latest_other_max,
        "fund_super_on_top": bool(on_top.iloc[-1]),
        "fund_super_rising": rising,
        "fund_recent_cross": recent_cross,
        "fund_prior_adhesion": adhesion,
        "fund_persistence_5d": persistence,
        "fund_recent_spread": recent_spread,
        "fund_baseline_spread": baseline_spread,
        "fund_tightness_ratio": tightness_ratio,
        "fund_cross_count_12d": cross_count,
        "fund_red_cross_count": red_cross_count,
        "fund_shape_match": fund_shape_match,
        "fund_near_match": fund_near_match,
        "fund_conditions": fund_conditions,
        "fund_condition_count": int(sum(fund_conditions.values())),
        "fund_condition_total": len(fund_conditions),
        "fund_reasons": reasons,
        "fund_risks": risks,
        "fund_proxy": proxy,
    }


def _reference_close_frame(
    history: pd.DataFrame, timeframe: str
) -> pd.DataFrame:
    frame = history.sort_values("date")[["date", "close"]].copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
    frame = frame.dropna(subset=["date", "close"])
    if timeframe == "daily":
        return frame.reset_index(drop=True)
    if timeframe != "weekly":
        raise ValueError(f"不支持的形态模板周期: {timeframe}")
    frame["week"] = frame["date"].dt.to_period("W-FRI")
    return (
        frame.groupby("week", sort=True, as_index=False)
        .agg(date=("date", "last"), close=("close", "last"))
        [["date", "close"]]
        .reset_index(drop=True)
    )


def extract_reference_template(
    history: pd.DataFrame,
    *,
    code: str,
    name: str,
    window: int = 40,
    forward_days: int = 10,
    search_tail: int = 180,
    timeframe: str = "daily",
) -> ReferenceTemplate | None:
    frame = _reference_close_frame(history, timeframe)
    if len(frame) < window + forward_days + 5:
        return None
    close = frame["close"].to_numpy(dtype=float)
    start = max(window - 1, len(frame) - search_tail)
    stop = len(frame) - forward_days
    best_index: int | None = None
    best_score = -float("inf")
    best_forward_return = 0.0
    for index in range(start, stop):
        current = close[index]
        if not math.isfinite(current) or current <= 0:
            continue
        future_return = float(np.nanmax(close[index + 1 : index + forward_days + 1]) / current - 1)
        past_return = close[index] / close[max(0, index - 10)] - 1
        selection_score = future_return - 0.35 * max(float(past_return), 0.0)
        if selection_score > best_score:
            best_score = selection_score
            best_index = index
            best_forward_return = future_return
    if best_index is None or best_forward_return < 0.12:
        return None
    segment = close[best_index - window + 1 : best_index + 1]
    if len(segment) != window or not np.isfinite(segment).all():
        return None
    return ReferenceTemplate(
        code=code,
        name=name,
        signal_date=pd.Timestamp(frame.loc[best_index, "date"]).date().isoformat(),
        forward_return=best_forward_return,
        path=segment.tolist(),
        timeframe=timeframe,
    )


def current_reference_template(
    history: pd.DataFrame,
    *,
    code: str,
    name: str,
    window: int = 40,
    timeframe: str = "daily",
) -> ReferenceTemplate | None:
    frame = _reference_close_frame(history, timeframe)
    if len(frame) < window:
        return None
    segment = frame["close"].tail(window).to_numpy(dtype=float)
    if len(segment) != window or not np.isfinite(segment).all():
        return None
    return ReferenceTemplate(
        code=code,
        name=name,
        signal_date=pd.Timestamp(frame["date"].iloc[-1]).date().isoformat(),
        forward_return=0.0,
        path=segment.tolist(),
        kind="current_target",
        timeframe=timeframe,
    )


def _resample(values: Iterable[float], size: int = 40) -> np.ndarray:
    array = np.asarray(list(values), dtype=float)
    if len(array) == size:
        return array
    old_axis = np.linspace(0.0, 1.0, len(array))
    new_axis = np.linspace(0.0, 1.0, size)
    return np.interp(new_axis, old_axis, array)


def path_similarity(left: Iterable[float], right: Iterable[float]) -> float:
    left_array = _resample(left)
    right_array = _resample(right)
    if (left_array <= 0).any() or (right_array <= 0).any():
        return 0.0
    left_log = np.log(left_array)
    right_log = np.log(right_array)
    left_std = float(left_log.std())
    right_std = float(right_log.std())
    if left_std < 1e-8 or right_std < 1e-8:
        return 0.0
    left_z = (left_log - left_log.mean()) / left_std
    right_z = (right_log - right_log.mean()) / right_std
    correlation = float(np.corrcoef(left_z, right_z)[0, 1])
    rmse = float(np.sqrt(np.mean((left_z - right_z) ** 2)))
    score = 100 * (0.65 * ((correlation + 1) / 2) + 0.35 * math.exp(-rmse))
    return round(_clip(score, 0, 100), 2)


def best_shape_similarity(
    history: pd.DataFrame,
    templates: list[ReferenceTemplate],
    window: int = 40,
    exclude_code: str | None = None,
) -> dict[str, Any]:
    empty_result = {
        "similarity_score": None,
        "similar_reference": None,
        "similar_reference_code": None,
        "similar_reference_date": None,
        "similar_reference_timeframe": None,
    }
    if not templates:
        return empty_result
    eligible = [item for item in templates if item.code != exclude_code]
    if not eligible:
        return empty_result
    candidate_paths: dict[tuple[str, int], list[float] | None] = {}
    scored: list[tuple[float, ReferenceTemplate]] = []
    for template in eligible:
        template_window = len(template.path) or window
        key = (template.timeframe, template_window)
        if key not in candidate_paths:
            frame = _reference_close_frame(history, template.timeframe)
            candidate_paths[key] = (
                frame["close"].tail(template_window).tolist()
                if len(frame) >= template_window
                else None
            )
        candidate_path = candidate_paths[key]
        if candidate_path is None or not template.path:
            continue
        scored.append((path_similarity(candidate_path, template.path), template))
    if not scored:
        return empty_result
    score, template = max(scored, key=lambda item: item[0])
    return {
        "similarity_score": score,
        "similar_reference": template.name,
        "similar_reference_code": template.code,
        "similar_reference_date": template.signal_date,
        "similar_reference_timeframe": template.timeframe,
    }
