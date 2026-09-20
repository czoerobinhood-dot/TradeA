"""Frozen daily-chart preferences; similarities are not return predictions.

There is no fitted threshold or fitted weight in this module.  A few favourites
can only weakly adjust an otherwise eligible candidate's ranking.
"""

from __future__ import annotations

import math
from functools import lru_cache
from typing import Any

import numpy as np
import pandas as pd


SCHEMA_VERSION = 1
ALGORITHM_VERSION = "daily-preference-v2"
PRIORITY_POLICY = "confirmed-quality-bands-v1"
PREFERENCE_BAND_WIDTH = 10
QUALITY_BAND_WIDTH = 10
MAX_ADJUSTMENT = 2.0
SHRINKAGE_PRIOR = 8.0
WINDOWS = (90, 40, 20)
NEIGHBOR_COUNT = 3
MIN_REFERENCE_STOCKS = 3
MAX_SAMPLE_LAG_DAYS = 7
OHLCV = ("open", "high", "low", "close", "volume")


def _day(value: Any) -> pd.Timestamp:
    if value is None or isinstance(value, (int, float)):
        raise ValueError("invalid_date")
    try:
        day = pd.Timestamp(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("invalid_date") from exc
    if pd.isna(day):
        raise ValueError("invalid_date")
    if day.tzinfo is not None:
        day = day.tz_localize(None)
    return day.normalize()


def _code(value: Any) -> str:
    text = str(value or "").strip()
    if not text.isdigit() or len(text) > 6:
        raise ValueError("invalid_code")
    return text.zfill(6)


def _freeze(history: pd.DataFrame, cutoff: pd.Timestamp) -> list[dict[str, Any]]:
    if not isinstance(history, pd.DataFrame):
        raise ValueError("missing_history")
    if not {"date", *OHLCV}.issubset(history.columns):
        raise ValueError("missing_ohlcv_columns")
    frame = history.loc[:, ["date", *OHLCV]].copy()
    try:
        frame["date"] = frame["date"].map(_day)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("invalid_history_date") from exc
    # First remove future observations, before inspecting price/volume values.
    frame = frame.loc[frame["date"] <= cutoff].sort_values("date").tail(90)
    if len(frame) < 90:
        raise ValueError("insufficient_daily_history")
    if frame["date"].duplicated().any():
        raise ValueError("duplicate_daily_dates")
    try:
        values = frame.loc[:, list(OHLCV)].apply(pd.to_numeric, errors="coerce")
        numeric = values.to_numpy(dtype=float)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("invalid_ohlcv") from exc
    if not np.isfinite(numeric).all() or (numeric <= 0).any():
        raise ValueError("invalid_ohlcv")
    if (
        (values["high"] < values[["open", "close", "low"]].max(axis=1)).any()
        or (values["low"] > values[["open", "close"]].min(axis=1)).any()
    ):
        raise ValueError("inconsistent_ohlc")
    dates = frame["date"].dt.strftime("%Y-%m-%d").tolist()
    return [
        {"date": date, **dict(zip(OHLCV, map(float, row)))}
        for date, row in zip(dates, numeric)
    ]


def _confidence(sample: dict[str, Any]) -> float:
    default = 1.0 if sample.get("source") in ("user_chart", "user_confirmed") else 0.5
    try:
        value = float(sample.get("confidence", default))
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid_confidence") from exc
    if not math.isfinite(value) or not 0 < value <= 1:
        raise ValueError("invalid_confidence")
    return value


def build_preference_model(
    samples: list[dict],
    histories: dict[str, pd.DataFrame],
    *,
    available_from: str,
) -> dict:
    """Freeze at most one example per stock, never extending its labelled date.

    Explicit chart examples take precedence through their greater confidence.
    Ties select the latest already-known date, independent of input ordering.
    Invalid selected examples are recorded, never silently repaired or shifted.
    """
    available = _day(available_from)
    model: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "algorithm_version": ALGORITHM_VERSION,
        "model_version": ALGORITHM_VERSION,
        "priority_policy": PRIORITY_POLICY,
        "preference_band_width": PREFERENCE_BAND_WIDTH,
        "quality_band_width": QUALITY_BAND_WIDTH,
        "available_from": available.strftime("%Y-%m-%d"),
        "purpose": "daily_chart_preference_only",
        "max_adjustment": MAX_ADJUSTMENT,
        "shrinkage_prior": SHRINKAGE_PRIOR,
        "windows": list(WINDOWS),
        "neighbor_count": NEIGHBOR_COUNT,
        "min_reference_stocks": MIN_REFERENCE_STOCKS,
        "max_sample_lag_days": MAX_SAMPLE_LAG_DAYS,
        "samples": [],
        "skipped_samples": [],
    }
    unique: dict[str, dict] = {}

    def skipped(sample: dict, reason: str) -> None:
        model["skipped_samples"].append(
            {
                "code": str(sample.get("code", "")),
                "name": str(sample.get("name", "")),
                "as_of": str(sample.get("as_of", "")),
                "actual_as_of": sample.get("actual_as_of"),
                "source": str(sample.get("source", "")),
                "reason": reason,
            }
        )

    def priority(sample: dict) -> tuple:
        return (
            sample["confidence"], sample["as_of"], sample["source"], sample["name"]
        )

    for raw in samples:
        if not isinstance(raw, dict):
            skipped({}, "invalid_sample")
            continue
        try:
            code = _code(raw.get("code"))
            cutoff = _day(raw.get("as_of"))
            if cutoff > available:
                raise ValueError("sample_after_model_available")
            sample = {
                "code": code,
                "name": str(raw.get("name", code)),
                "as_of": cutoff.strftime("%Y-%m-%d"),
                "source": str(raw.get("source", "favorite")),
                "confidence": _confidence(raw),
            }
        except ValueError as exc:
            skipped(raw, str(exc))
            continue
        previous = unique.get(code)
        if previous is None:
            unique[code] = sample
        elif priority(sample) > priority(previous):
            skipped(previous, "duplicate_code")
            unique[code] = sample
        else:
            skipped(sample, "duplicate_code")

    for code, sample in sorted(unique.items()):
        try:
            cutoff = _day(sample["as_of"])
            bars = _freeze(histories.get(code), cutoff)
            sample["actual_as_of"] = bars[-1]["date"]
            if (cutoff - _day(sample["actual_as_of"])).days > MAX_SAMPLE_LAG_DAYS:
                raise ValueError("sample_history_stale")
        except ValueError as exc:
            skipped(sample, str(exc))
            continue
        model["samples"].append({**sample, "bars": bars})
    return model


def _signature(bars: list[dict]) -> list[tuple[np.ndarray, np.ndarray, float]]:
    data = pd.DataFrame(bars)
    close = data["close"].to_numpy(dtype=float)
    opened = data["open"].to_numpy(dtype=float)
    volume = data["volume"].to_numpy(dtype=float)
    signature = []
    for window in WINDOWS:
        prices = close[-window:]
        vols = volume[-window:]
        # Dividing by the first price preserves drawdown and rebound magnitude.
        # Median volume and clipping prevent a single outlier dominating shape.
        log_prices = np.log(prices)
        price_path = log_prices - log_prices[0]
        log_volumes = np.log(vols)
        median_log_volume = float(np.median(log_volumes))
        volume_path = np.clip(log_volumes - median_log_volume, -math.log(8), math.log(8))
        capped_log_volume = np.minimum(log_volumes, median_log_volume + math.log(8))
        robust_volume = np.exp(capped_log_volume - np.max(capped_log_volume))
        red_share = float(
            robust_volume[prices > opened[-window:]].sum() / robust_volume.sum()
        )
        signature.append((price_path, volume_path, red_share))
    return signature


def _similarity(left: list, right: list) -> float:
    price_distance = []
    volume_distance = []
    red_distance = []
    for a, b in zip(left, right):
        price_distance.append(float(np.sqrt(np.mean(np.square(a[0] - b[0])))) / 0.20)
        volume_distance.append(float(np.mean(np.abs(a[1] - b[1]))) / 0.70)
        red_distance.append(abs(a[2] - b[2]) / 0.35)
    # Fixed scales and equal window weights; no optimisation on favourites.
    distance = (
        0.65 * float(np.mean(price_distance))
        + 0.25 * float(np.mean(volume_distance))
        + 0.10 * float(np.mean(red_distance))
    )
    return float(np.clip(100 * math.exp(-distance), 0, 100))


def _reference_key(bars: Any) -> tuple[tuple[Any, ...], ...]:
    """Capture content, including dates, without trusting mutable model identity."""
    if not isinstance(bars, list):
        raise ValueError("invalid_frozen_bars")
    columns = ("date", *OHLCV)
    try:
        return tuple(tuple(bar[column] for column in columns) for bar in bars)
    except (KeyError, TypeError) as exc:
        raise ValueError("invalid_frozen_bars") from exc


@lru_cache(maxsize=128)
def _cached_reference_signature(
    bars_key: tuple[tuple[Any, ...], ...], as_of: str
) -> tuple[pd.Timestamp, tuple]:
    """Cache only a fully validated immutable content snapshot and its cutoff."""
    bars = _freeze(pd.DataFrame(bars_key, columns=("date", *OHLCV)), _day(as_of))
    signature = tuple(_signature(bars))
    for price_path, volume_path, _ in signature:
        price_path.setflags(write=False)
        volume_path.setflags(write=False)
    return _day(bars[-1]["date"]), signature


def score_preference(
    history: pd.DataFrame, model: dict, *, code: str, as_of: str
) -> dict:
    """Compare fixed windows and return a small, confidence-shrunk adjustment."""
    result: dict[str, Any] = {
        "preference_score": None,
        "preference_adjustment": 0.0,
        "preference_support": 0,
        "preference_effective_samples": 0.0,
        "preference_neighbors": [],
        "preference_status": "invalid_model",
        "preference_model_version": ALGORITHM_VERSION,
        "preference_priority_policy": None,
    }
    if not isinstance(model, dict):
        return result
    if (
        model.get("schema_version") != SCHEMA_VERSION
        or model.get("algorithm_version") != ALGORITHM_VERSION
        or model.get("model_version") != ALGORITHM_VERSION
        or model.get("max_adjustment") != MAX_ADJUSTMENT
        or model.get("shrinkage_prior") != SHRINKAGE_PRIOR
        or not isinstance(model.get("samples"), list)
    ):
        return result
    if (
        model.get("priority_policy") == PRIORITY_POLICY
        and model.get("preference_band_width") == PREFERENCE_BAND_WIDTH
        and model.get("quality_band_width") == QUALITY_BAND_WIDTH
    ):
        result["preference_priority_policy"] = PRIORITY_POLICY
    try:
        cutoff = _day(as_of)
        available = _day(model.get("available_from"))
        candidate_code = _code(code)
    except ValueError:
        return result
    if cutoff < available:
        result["preference_status"] = "before_model_available"
        return result
    try:
        candidate_bars = _freeze(history, cutoff)
        candidate_day = _day(candidate_bars[-1]["date"])
        candidate = _signature(candidate_bars)
    except ValueError as exc:
        result["preference_status"] = "invalid_history"
        result["preference_error"] = str(exc)
        return result

    neighbors: dict[str, dict] = {}
    for sample in model["samples"]:
        if not isinstance(sample, dict):
            continue
        try:
            sample_code = _code(sample.get("code"))
            sample_day = _day(sample.get("as_of"))
            if sample_code == candidate_code or sample_day > min(cutoff, available):
                continue
            confidence = _confidence(sample)
            actual_day, reference = _cached_reference_signature(
                _reference_key(sample.get("bars")), sample_day.strftime("%Y-%m-%d")
            )
            if (
                actual_day > candidate_day
                or (sample_day - actual_day).days > MAX_SAMPLE_LAG_DAYS
            ):
                continue
            score = _similarity(candidate, reference)
        except (TypeError, ValueError, OverflowError):
            continue
        neighbor = {
            "code": sample_code,
            "name": str(sample.get("name", sample_code)),
            "as_of": sample_day.strftime("%Y-%m-%d"),
            "actual_as_of": actual_day.strftime("%Y-%m-%d"),
            "score": score,
            "confidence": confidence,
            "source": str(sample.get("source", "favorite")),
        }
        existing = neighbors.get(sample_code)
        # A manually duplicated model also cannot grant extra sample weight.
        if existing is None or (confidence, neighbor["as_of"]) > (
            existing["confidence"], existing["as_of"]
        ):
            neighbors[sample_code] = neighbor
    selected = sorted(neighbors.values(), key=lambda item: (-item["score"], item["code"]))[
        :NEIGHBOR_COUNT
    ]
    support = len(selected)
    result["preference_support"] = support
    result["preference_status"] = "insufficient_reference_stocks"
    if not selected:
        return result
    effective = sum(item["confidence"] for item in selected)
    score = sum(item["score"] * item["confidence"] for item in selected) / effective
    result["preference_score"] = round(score, 4)
    result["preference_effective_samples"] = round(effective, 4)
    result["preference_neighbors"] = [
        {**item, "score": round(item["score"], 4)} for item in selected
    ]
    if support >= MIN_REFERENCE_STOCKS:
        result["preference_status"] = "active"
        adjustment = MAX_ADJUSTMENT * effective / (effective + SHRINKAGE_PRIOR) * (score - 50) / 50
        result["preference_adjustment"] = round(
            float(np.clip(adjustment, -MAX_ADJUSTMENT, MAX_ADJUSTMENT)), 6
        )
    return result
