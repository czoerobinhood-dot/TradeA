from ashare_screener.config import DEFAULT_WEIGHTS
from ashare_screener.scoring import classify_candidate, weighted_score


def test_weighted_score_reweights_missing_fund_data():
    metrics = {
        "hot_score": 80,
        "theme_score": 60,
        "technical_score": 70,
        "similarity_score": 75,
        "fund_score": None,
    }
    score, completeness, available = weighted_score(metrics, DEFAULT_WEIGHTS)
    available_weights = {
        key: value for key, value in DEFAULT_WEIGHTS.items() if key != "fund"
    }
    expected = (
        80 * available_weights["hot"]
        + 60 * available_weights["theme"]
        + 70 * available_weights["technical"]
        + 75 * available_weights["similarity"]
    ) / sum(available_weights.values())
    assert score == round(expected, 2)
    assert completeness == 0.60
    assert "fund" not in available


def test_classification_marks_late_setup_even_with_high_score():
    decision, _ = classify_candidate(
        {
            "final_score": 90,
            "technical_score": 80,
            "entry_late": True,
            "price_condition_count": 4,
            "price_condition_total": 6,
            "data_completeness": 1.0,
            "fund_score": 100,
            "primary_structure_match": True,
        }
    )
    assert decision == "已启动/错过低位"


def test_classification_keeps_pre_quote_setup_in_limit_up_bucket():
    decision, reason = classify_candidate(
        {
            "data_completeness": 0.55,
            "fund_score": None,
            "limit_up_today": True,
            "pre_quote_primary_structure_match": True,
            "pre_quote_price_condition_count": 7,
            "primary_structure_match": False,
            "price_condition_count": 3,
            "price_condition_total": 7,
        }
    )

    assert decision == "已启动/错过低位"
    assert "当前已封涨停" in reason


def test_classification_requires_fund_data_for_strict_result():
    decision, reason = classify_candidate(
        {
            "final_score": 82,
            "technical_score": 80,
            "stage": "启动",
            "data_completeness": 0.60,
            "fund_score": None,
            "price_condition_count": 5,
            "price_condition_total": 6,
            "primary_structure_match": True,
        }
    )
    assert decision == "待资金数据"
    assert "四档资金流" in reason


def test_classification_requires_all_strict_gates():
    decision, _ = classify_candidate(
        {
            "data_completeness": 0.95,
            "fund_score": 85,
            "entry_late": False,
            "price_condition_count": 6,
            "price_condition_total": 6,
            "strict_match": True,
            "near_match": True,
            "primary_structure_match": True,
        }
    )
    assert decision == "严格匹配"


def test_classification_rejects_historical_drawdown_after_full_recovery():
    decision, reason = classify_candidate(
        {
            "data_completeness": 0.95,
            "fund_score": None,
            "entry_late": False,
            "price_condition_count": 5,
            "price_condition_total": 7,
            "primary_structure_match": False,
        }
    )
    assert decision == "不符合"
    assert "第一门槛" in reason
