from copy import deepcopy

import pytest

from ashare_screener.preferences import PRIORITY_POLICY
from ashare_screener.scoring import apply_preference_adjustment, candidate_sort_key


def candidate(base=65, similarity=65, **changes):
    result = {
        "decision": "接近标准", "early_bottom_match": True,
        "bottom_red_volume_confirmed": True, "entry_late": False,
        "criteria_passed": 10, "final_score": base,
        "preference_status": "active", "preference_score": similarity,
        "preference_priority_policy": PRIORITY_POLICY, "preference_adjustment": .1,
        "preference_neighbors": [{"code": f"60000{i}", "source": "user_confirmed",
                                  "confidence": 1.0} for i in range(3)],
        **changes,
    }
    apply_preference_adjustment(result)
    return result


def test_confirmed_shape_has_priority_only_within_equal_conditions_and_quality_band():
    preferred = candidate(base=62, similarity=65)
    neutral = candidate(base=68, similarity=55)
    assert sorted([neutral, preferred], key=candidate_sort_key) == [preferred, neutral]
    higher_quality = candidate(base=72, similarity=55)
    more_conditions = candidate(base=61, similarity=55, criteria_passed=11)
    assert candidate_sort_key(higher_quality) < candidate_sort_key(preferred)
    assert candidate_sort_key(more_conditions) < candidate_sort_key(preferred)
    strict = candidate(base=51, similarity=55, decision="严格匹配")
    assert candidate_sort_key(strict) < candidate_sort_key(preferred)


def test_same_shape_band_keeps_composite_score_order_not_decimal_similarity():
    assert candidate_sort_key(candidate(base=68, similarity=60.1)) < candidate_sort_key(
        candidate(base=62, similarity=69.9))


@pytest.mark.parametrize("changes", [
    {"decision": "不符合"}, {"decision": "已启动/错过低位"},
    {"decision": "数据不足"}, {"early_bottom_match": False}, {"entry_late": True},
    {"preference_status": "invalid_history"}, {"preference_status": "before_model_available"},
    {"preference_priority_policy": None}, {"preference_score": None},
    {"preference_score": float("nan")}, {"preference_score": float("inf")},
    {"preference_score": 101},
])
def test_stale_priority_is_cleared_when_candidate_becomes_ineligible(changes):
    record = candidate(similarity=85)
    assert record["preference_priority_tier"] > 0
    record.update(changes)
    apply_preference_adjustment(record)
    assert record["preference_priority_tier"] == 0
    assert record["preference_priority_enabled"] is False


@pytest.mark.parametrize("source,confidence,duplicate", [
    ("favorite", 1, False), ("user_chart", 1, False),
    ("user_confirmed", .5, False), ("user_confirmed", 1, True),
])
def test_weak_unconfirmed_or_duplicate_neighbors_cannot_enable_priority(source, confidence, duplicate):
    record = candidate()
    for item in record["preference_neighbors"]:
        item.update(source=source, confidence=confidence)
        if duplicate:
            item["code"] = "600001"
    apply_preference_adjustment(record)
    assert not record["preference_priority_enabled"]
    assert record["preference_priority_tier"] == 0


def test_disabled_baseline_restores_original_order_even_with_stale_tier_and_adjustment():
    records = [candidate(base=62, similarity=75), candidate(base=68, similarity=55)]
    assert sorted(records, key=candidate_sort_key) == records
    assert sorted(records, key=lambda r: candidate_sort_key(r, use_preference=False)) == records[::-1]
    copy = deepcopy(records[0])
    copy["preference_priority_tier"] = 999
    copy["preference_priority_policy"] = None
    assert candidate_sort_key(copy) > candidate_sort_key(records[1])


def test_neutral_band_does_not_claim_priority():
    record = candidate(similarity=59.99)
    assert record["preference_priority_enabled"]
    assert record["preference_priority_tier"] == 0
