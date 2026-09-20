import copy
import json

import numpy as np
import pandas as pd
import pytest

from ashare_screener.preferences import (
    ALGORITHM_VERSION,
    PREFERENCE_BAND_WIDTH,
    PRIORITY_POLICY,
    QUALITY_BAND_WIDTH,
    _cached_reference_signature,
    build_preference_model,
    score_preference,
)


AS_OF = "2026-09-18"
AVAILABLE = "2026-09-20"


def history(*, amplitude=1.0, price_unit=1.0, volume_unit=1.0):
    path = np.concatenate((np.linspace(0, -0.6, 60), np.linspace(-0.6, -0.42, 40)))
    close = 100 * np.exp(path * amplitude) * price_unit
    opened = close * np.where(np.arange(100) % 4 == 0, 1.006, 0.992)
    return pd.DataFrame(
        {
            "date": pd.bdate_range(end=AS_OF, periods=100),
            "open": opened,
            "high": np.maximum(opened, close) * 1.015,
            "low": np.minimum(opened, close) * 0.985,
            "close": close,
            "volume": np.where(np.arange(100) >= 70, 1700, 1000)
            * (1 + 0.1 * np.sin(np.arange(100)))
            * volume_unit,
        }
    )


def examples(count=4):
    return [
        {
            "code": f"{index + 1:06d}",
            "name": f"Example {index + 1}",
            "as_of": AS_OF,
            "source": "favorite",
            "confidence": 0.5,
        }
        for index in range(count)
    ]


def build(samples=None, frames=None):
    samples = examples() if samples is None else samples
    frames = {sample["code"]: history() for sample in samples} if frames is None else frames
    return build_preference_model(samples, frames, available_from=AVAILABLE)


def score(frame, model, *, code="600000", as_of=AVAILABLE):
    return score_preference(frame, model, code=code, as_of=as_of)


def test_freezes_only_last_90_bars_at_label_date_and_is_json_serializable():
    frame = history()
    frozen = build(examples(1), {"000001": frame})
    bars = frozen["samples"][0]["bars"]
    assert len(bars) == 90
    assert bars[0]["date"] == frame.iloc[10]["date"].strftime("%Y-%m-%d")
    assert bars[-1]["date"] == AS_OF
    frame.loc[99, "close"] = 999
    assert bars[-1]["close"] != 999
    assert json.loads(json.dumps(frozen, allow_nan=False)) == frozen


def test_future_bars_cannot_change_model_or_candidate_score():
    original = history()
    future = original.iloc[-1:].copy()
    future["date"] = pd.Timestamp("2026-09-21")
    future[list(("open", "high", "low", "close", "volume"))] = np.nan
    extended = pd.concat((original, future), ignore_index=True)
    samples = examples()
    clean = build(samples, {item["code"]: original for item in samples})
    added = build(samples, {item["code"]: extended for item in samples})
    assert added == clean
    assert score(extended, clean) == score(original, clean)


def test_preference_is_disabled_before_actual_model_availability():
    result = score(history(), build(), as_of=AS_OF)
    assert result["preference_status"] == "before_model_available"
    assert result["preference_adjustment"] == 0
    assert result["preference_score"] is None
    assert result["preference_neighbors"] == []


def test_self_stock_is_excluded_even_when_dates_differ():
    model = build(examples(3))
    duplicate = copy.deepcopy(model["samples"][0])
    duplicate["as_of"] = "2026-09-19"
    model["samples"].append(duplicate)
    result = score(history(), model, code="000001")
    assert result["preference_support"] == 2
    assert result["preference_adjustment"] == 0
    assert "000001" not in {item["code"] for item in result["preference_neighbors"]}


def test_repeated_favorites_do_not_increase_score_or_weight():
    samples = examples()
    unique = build(samples)
    duplicated = build(samples + [samples[0]] * 20)
    assert unique["samples"] == duplicated["samples"]
    assert len(duplicated["skipped_samples"]) == 20
    assert score(history(), unique) == score(history(), duplicated)
    duplicated["samples"].extend([duplicated["samples"][0]] * 20)
    assert score(history(), unique) == score(history(), duplicated)


def test_explicit_chart_replaces_favorite_without_adding_a_stock():
    samples = examples(1)
    chart = {**samples[0], "source": "user_chart", "confidence": 1.0}
    forward = build(samples + [chart])
    reverse = build([chart] + samples)
    assert forward["samples"] == reverse["samples"]
    assert len(forward["samples"]) == 1
    assert forward["samples"][0]["source"] == "user_chart"
    assert forward["samples"][0]["confidence"] == 1


def test_two_distinct_stocks_can_show_similarity_but_never_adjust_rank():
    result = score(history(), build(examples(2)))
    assert result["preference_score"] == 100
    assert result["preference_support"] == 2
    assert result["preference_status"] == "insufficient_reference_stocks"
    assert result["preference_adjustment"] == 0


def test_matching_outline_with_different_fall_amplitude_scores_lower():
    model = build()
    identical = score(history(), model)
    shallow = score(history(amplitude=0.2), model)
    assert identical["preference_score"] == 100
    assert shallow["preference_score"] < identical["preference_score"] - 5


def test_price_and_volume_units_do_not_change_similarity():
    model = build()
    expected = score(history(amplitude=0.8), model)
    scaled = score(history(amplitude=0.8, price_unit=7.3, volume_unit=10000), model)
    assert scaled == expected
    samples = examples()
    scaled_model = build(
        samples,
        {
            item["code"]: history(price_unit=3.9, volume_unit=100)
            for item in samples
        },
    )
    assert score(history(amplitude=0.8), scaled_model) == expected


@pytest.mark.parametrize("field", ["open", "high", "low", "close", "volume"])
@pytest.mark.parametrize("value", [0, -1, float("nan"), float("inf")])
def test_invalid_ohlcv_rejects_sample_and_candidate(field, value):
    invalid = history()
    invalid.loc[99, field] = value
    model = build(examples(1), {"000001": invalid})
    assert model["samples"] == []
    assert model["skipped_samples"][0]["reason"] == "invalid_ohlcv"
    assert model["skipped_samples"][0]["source"] == "favorite"
    result = score(invalid, build())
    assert result["preference_status"] == "invalid_history"
    assert result["preference_adjustment"] == 0


@pytest.mark.parametrize("kind", ["missing_column", "short", "duplicate_date", "inconsistent"])
def test_incomplete_or_inconsistent_daily_history_is_rejected(kind):
    invalid = history()
    if kind == "missing_column":
        invalid = invalid.drop(columns="volume")
    elif kind == "short":
        invalid = invalid.tail(89)
    elif kind == "duplicate_date":
        invalid.loc[99, "date"] = invalid.loc[98, "date"]
    else:
        invalid.loc[99, "high"] = invalid.loc[99, "close"] / 2
    result = score(invalid, build())
    assert result["preference_status"] == "invalid_history"
    assert result["preference_adjustment"] == 0


def test_future_label_is_skipped_and_cannot_be_smuggled_into_scoring():
    samples = examples()
    samples[0]["as_of"] = "2026-09-22"
    model = build(samples)
    assert len(model["samples"]) == 3
    assert model["skipped_samples"][0]["reason"] == "sample_after_model_available"
    model = build(examples(3))
    model["samples"][0]["as_of"] = "2026-09-22"
    result = score(history(), model)
    assert result["preference_support"] == 2
    assert result["preference_adjustment"] == 0


def test_confidence_weights_neighbors_and_shrinks_effective_sample_size():
    samples = examples(3)
    samples[0].update(source="user_chart", confidence=1.0)
    frames = {item["code"]: history(amplitude=1 - i * 0.1) for i, item in enumerate(samples)}
    result = score(history(), build(samples, frames))
    neighbors = result["preference_neighbors"]
    weighted = sum(item["score"] * item["confidence"] for item in neighbors) / 2
    assert result["preference_score"] == pytest.approx(weighted, abs=0.0001)
    assert result["preference_effective_samples"] == 2
    assert result["preference_adjustment"] == pytest.approx(
        2 * 2 / (2 + 8) * (result["preference_score"] - 50) / 50, abs=0.000001
    )
    assert result["preference_score"] < max(item["score"] for item in neighbors)


def test_only_three_distinct_neighbors_and_strict_adjustment_cap():
    samples = examples(20)
    for item in samples:
        item.update(source="user_chart", confidence=1.0)
    result = score(history(), build(samples))
    assert len(result["preference_neighbors"]) == 3
    assert result["preference_support"] == 3
    assert result["preference_effective_samples"] == 3
    assert result["preference_adjustment"] == pytest.approx(2 * 3 / 11, abs=0.000001)
    for amplitude in (0.01, 1, 10):
        adjustment = score(history(amplitude=amplitude), build(samples))["preference_adjustment"]
        assert -2 <= adjustment <= 2
    tampered = build(samples)
    tampered["max_adjustment"] = 100
    assert score(history(), tampered)["preference_status"] == "invalid_model"


def test_invalid_frozen_sample_is_not_used_in_similarity():
    model = build(examples(3))
    model["samples"][0]["bars"][-1]["close"] = float("nan")
    result = score(history(), model)
    assert result["preference_support"] == 2
    assert result["preference_adjustment"] == 0


def test_candidate_actual_last_day_cannot_use_later_reference_bars():
    model = build(examples(3))
    old_candidate = history().iloc[:-1]
    result = score(old_candidate, model, as_of="2026-09-23")
    assert result["preference_support"] == 0
    assert result["preference_neighbors"] == []
    assert result["preference_adjustment"] == 0


def test_sample_records_actual_day_and_compares_that_day_to_candidate():
    samples = examples(3)
    frames = {item["code"]: history().iloc[:-1] for item in samples}
    model = build(samples, frames)
    assert all(item["as_of"] == AS_OF for item in model["samples"])
    assert all(item["actual_as_of"] == "2026-09-17" for item in model["samples"])
    result = score(history().iloc[:-1], model)
    assert result["preference_status"] == "active"
    assert result["preference_support"] == 3
    assert all(item["actual_as_of"] == "2026-09-17" for item in result["preference_neighbors"])


def test_stale_samples_are_rejected_with_their_actual_day_recorded():
    stale = history()
    stale["date"] = pd.bdate_range(end="2026-09-01", periods=100)
    model = build(examples(1), {"000001": stale})
    assert model["samples"] == []
    skipped = model["skipped_samples"][0]
    assert skipped["reason"] == "sample_history_stale"
    assert skipped["as_of"] == AS_OF
    assert skipped["actual_as_of"] == "2026-09-01"


def test_seven_calendar_day_lag_is_allowed_but_eight_is_rejected():
    at_limit = history()
    at_limit["date"] = pd.bdate_range(end="2026-09-11", periods=100)
    assert len(build(examples(1), {"000001": at_limit})["samples"]) == 1
    past_limit = at_limit.copy()
    past_limit["date"] = pd.bdate_range(end="2026-09-10", periods=100)
    assert build(examples(1), {"000001": past_limit})["samples"] == []


def test_score_rechecks_stale_bars_and_does_not_trust_actual_date_metadata():
    model = build(examples(3))
    for sample in model["samples"]:
        dates = pd.bdate_range(end="2026-09-01", periods=90)
        for bar, date in zip(sample["bars"], dates):
            bar["date"] = date.strftime("%Y-%m-%d")
    result = score(history(), model)
    assert result["preference_support"] == 0
    assert result["preference_adjustment"] == 0


def test_reference_cache_reuses_content_across_model_copies_without_changing_score():
    model = build(examples(3))
    _cached_reference_signature.cache_clear()
    initial = score(history(), model)
    first_cache = _cached_reference_signature.cache_info()
    repeat = score(history(), copy.deepcopy(model))
    repeat_cache = _cached_reference_signature.cache_info()
    assert repeat == initial
    assert repeat_cache.misses == first_cache.misses
    assert repeat_cache.hits > first_cache.hits
    assert repeat_cache.maxsize == 128


def test_mutating_frozen_bars_invalidates_cache_and_changes_similarity():
    model = build(examples(3))
    initial = score(history(), model)
    changed = history(amplitude=0.2).tail(90).to_dict("records")
    for old, replacement in zip(model["samples"][0]["bars"], changed):
        for column in ("open", "high", "low", "close", "volume"):
            old[column] = float(replacement[column])
    updated = score(history(), model)
    assert updated["preference_score"] < initial["preference_score"]
    assert updated["preference_adjustment"] < initial["preference_adjustment"]
    assert score(history(), model) == updated


@pytest.mark.parametrize("change", ["date", "volume", "cutoff", "missing_column"])
def test_cached_references_still_reject_invalid_content_and_changed_cutoffs(change):
    model = build(examples(3))
    assert score(history(), model)["preference_support"] == 3
    sample = model["samples"][0]
    if change == "date":
        sample["bars"][-1]["date"] = "invalid-date"
    elif change == "volume":
        sample["bars"][-1]["volume"] = 0
    elif change == "cutoff":
        sample["as_of"] = "2026-09-17"
    else:
        del sample["bars"][-1]["close"]
    result = score(history(), model)
    assert result["preference_support"] == 2
    assert result["preference_adjustment"] == 0


def test_v2_model_records_fixed_priority_policy_without_deciding_tiers():
    model = build()
    assert ALGORITHM_VERSION == "daily-preference-v2"
    assert model["model_version"] == ALGORITHM_VERSION
    assert model["algorithm_version"] == ALGORITHM_VERSION
    assert model["priority_policy"] == PRIORITY_POLICY == "confirmed-quality-bands-v1"
    assert model["preference_band_width"] == PREFERENCE_BAND_WIDTH == 10
    assert model["quality_band_width"] == QUALITY_BAND_WIDTH == 10
    result = score(history(), model)
    assert result["preference_priority_policy"] == PRIORITY_POLICY
    assert "preference_tier" not in result


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("priority_policy", None),
        ("priority_policy", "confirmed-quality-bands-v0"),
        ("preference_band_width", None),
        ("preference_band_width", 5),
        ("quality_band_width", None),
        ("quality_band_width", 5),
    ],
)
def test_unrecognised_policy_fields_cannot_activate_priority(field, value):
    model = build()
    initial = score(history(), model)
    if value is None:
        del model[field]
    else:
        model[field] = value
    result = score(history(), model)
    assert result["preference_priority_policy"] is None
    assert result["preference_score"] == initial["preference_score"]
    assert result["preference_adjustment"] == initial["preference_adjustment"]


@pytest.mark.parametrize(
    ("source", "expected"),
    [("user_confirmed", 1.0), ("user_chart", 1.0), ("favorite", 0.5)],
)
def test_default_confidence_distinguishes_confirmed_examples_from_favorites(source, expected):
    samples = examples(3)
    for sample in samples:
        sample["source"] = source
        del sample["confidence"]
    model = build(samples)
    assert all(sample["confidence"] == expected for sample in model["samples"])
    assert score(history(), model)["preference_effective_samples"] == 3 * expected


def test_confirmed_example_can_keep_an_explicit_lower_confidence():
    samples = examples(3)
    for sample in samples:
        sample.update(source="user_confirmed", confidence=0.25)
    model = build(samples)
    assert all(sample["confidence"] == 0.25 for sample in model["samples"])
    assert score(history(), model)["preference_effective_samples"] == 0.75


def test_confirmation_does_not_bypass_invalid_confidence_validation():
    samples = examples(1)
    samples[0].update(source="user_confirmed", confidence=1.1)
    model = build(samples)
    assert model["samples"] == []
    assert model["skipped_samples"][0]["reason"] == "invalid_confidence"


def test_old_algorithm_cannot_claim_v2_priority_policy():
    model = build()
    model["model_version"] = "daily-preference-v1"
    model["algorithm_version"] = "daily-preference-v1"
    result = score(history(), model)
    assert result["preference_priority_policy"] is None
    assert result["preference_status"] == "invalid_model"
