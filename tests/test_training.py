from datetime import date
import json
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from ashare_screener.config import ScreenConfig
from ashare_screener.preferences import PRIORITY_POLICY, build_preference_model
from ashare_screener.scoring import apply_preference_adjustment, candidate_sort_key
from ashare_screener.training import (
    _digest, _rank_key, _ranking_comparison, load_preference_model,
    train_preferences, validate_preferences,
)
import ashare_screener.training as training
from ashare_screener.pipeline import Screener


def fixture_model():
    close = np.linspace(20, 10, 120)
    history = pd.DataFrame({"date": pd.bdate_range(end="2026-09-18", periods=120),
                            "open": close * .99, "close": close, "high": close * 1.01,
                            "low": close * .98, "volume": np.full(120, 1000.)})
    samples = [{"code": f"60000{i}", "name": f"样本{i}", "as_of": "2026-09-18",
                "source": "favorite", "confidence": .5} for i in range(4)]
    model = build_preference_model(samples, {s["code"]: history for s in samples}, available_from="2026-09-20")
    model["model_id"] = _digest(model)
    return model


def test_model_integrity_prevents_silent_parameter_edits(tmp_path):
    model = fixture_model()
    path = tmp_path / "model.json"
    path.write_text(json.dumps(model), encoding="utf-8")
    assert load_preference_model(path)["model_id"] == model["model_id"]
    model["max_adjustment"] = 30
    path.write_text(json.dumps(model), encoding="utf-8")
    with pytest.raises(ValueError, match="校验失败"):
        load_preference_model(path)


@pytest.mark.parametrize("changes", [
    {"decision": "不符合"}, {"decision": "已启动/错过低位"},
    {"early_bottom_match": False}, {"entry_late": True},
    {"preference_status": "invalid_history"},
    {"preference_status": "before_model_available"},
])
def test_preference_cannot_promote_failed_or_late_stock(changes):
    metrics = {"final_score": 60, "decision": "接近标准", "early_bottom_match": True,
               "entry_late": False, "preference_status": "active", "preference_adjustment": 100,
               **changes}
    original_decision = metrics["decision"]
    apply_preference_adjustment(metrics)
    assert metrics["final_score"] == 60
    assert metrics["decision"] == original_decision
    assert metrics["preference_applied_adjustment"] == 0


def test_adjustment_is_capped_and_repeated_application_does_not_accumulate():
    metrics = {"final_score": 60, "decision": "接近标准", "early_bottom_match": True,
               "entry_late": False, "preference_status": "active", "preference_adjustment": 100}
    apply_preference_adjustment(metrics)
    apply_preference_adjustment(metrics)
    assert metrics["final_score"] == 62
    assert metrics["base_final_score"] == 60
    assert metrics["preference_applied_adjustment"] == 2


def test_training_refuses_to_backdate_when_favorites_became_known(tmp_path):
    path = tmp_path / "samples.json"
    path.write_text(json.dumps({"observed_at": "2026-09-20", "samples": []}), encoding="utf-8")
    output = tmp_path / "model.json"
    with pytest.raises(ValueError, match="禁止回填历史"):
        train_preferences(samples_path=path, output_path=output,
                          validation_path=tmp_path / "validation.json", baseline_path=None,
                          provider=SimpleNamespace(as_of=date(2026, 9, 18)), config=ScreenConfig(),
                          available_from="2026-09-18")
    assert not output.exists()


def test_config_resolves_model_relative_to_config_directory(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"preference_model_path": ".cache/model.json"}), encoding="utf-8")
    assert ScreenConfig.from_file(path).preference_model_path == str(tmp_path / ".cache" / "model.json")


@pytest.mark.parametrize("mutation", ["missing_date", "duplicate", "parameters", "algorithm",
                                      "priority_policy", "preference_band_width", "quality_band_width"])
def test_well_hashed_but_incompatible_models_fail_closed(tmp_path, mutation):
    model = fixture_model()
    if mutation == "missing_date":
        del model["available_from"]
    elif mutation == "duplicate":
        model["samples"][1] = model["samples"][0]
    elif mutation == "parameters":
        model["max_adjustment"] = 30
    elif mutation == "algorithm":
        model["algorithm_version"] = "future-algorithm"
    else:
        model[mutation] = "unapproved-policy" if mutation == "priority_policy" else 1
    model["model_id"] = _digest(model)
    path = tmp_path / "model.json"
    path.write_text(json.dumps(model), encoding="utf-8")
    with pytest.raises(ValueError):
        load_preference_model(path)


def test_invalid_hash_disables_preference_without_blocking_the_screener(tmp_path):
    path = tmp_path / "model.json"
    path.write_text(json.dumps({**fixture_model(), "model_id": "bad"}), encoding="utf-8")
    screener = Screener(SimpleNamespace(), ScreenConfig(preference_model_path=str(path)))
    assert screener.preference_model is None
    assert any(issue.scope == "preference_model" for issue in screener.issues)


def test_validation_and_production_share_ranking_including_zero_and_gap_ties():
    base = {"decision": "接近标准", "final_score": 60, "early_bottom_match": True,
            "bottom_red_volume_confirmed": True, "criteria_passed": 5}
    records = [{**base, "rise_pressure": 0}, {**base, "rise_pressure": 1},
               {**base, "rise_pressure": 0, "gap_condition_count": 3},
               {**base, "rise_pressure": 0, "right_edge_volume_expanded": True}]
    assert sorted(records, key=_rank_key) == sorted(records, key=candidate_sort_key)
    assert sorted(records, key=_rank_key)[0]["right_edge_volume_expanded"] is True
    assert sorted(records, key=_rank_key)[-1]["rise_pressure"] == 1


def priority_validation_fixture(monkeypatch):
    samples = [{"code": f"60000{i}", "name": f"样本{i}", "as_of": "2026-09-18",
                "source": "user_confirmed", "confidence": 1.0} for i in range(4)]
    model = {"samples": samples, "model_id": "fixture-model", "available_from": "2026-09-20",
             "excluded_samples": [{"code": "002138", "reason": "仅观察，不作收益负例"}]}
    common = {"decision": "接近标准", "early_bottom_match": True,
              "bottom_red_volume_confirmed": True, "criteria_passed": 5,
              "entry_late": False}
    # Deliberately preserve an old report order different from the disabled baseline.
    records = [{**common, "code": "002138", "name": "仅观察", "final_score": 61.4, "fund_score": 30},
               {**common, "code": "300001", "name": "较高基础分", "final_score": 66.4, "fund_score": 80}]
    histories = {code: pd.DataFrame({"date": pd.to_datetime(["2026-09-18"])})
                 for code in [s["code"] for s in samples] + [r["code"] for r in records]}

    def fake_score(history, current_model, *, code, as_of):
        pivot_present = any(s["code"] == "600000" for s in current_model["samples"])
        score = (60.2 if pivot_present else 59.0) if code == "002138" else 59.5
        neighbors = [{"code": sample["code"], "source": "user_confirmed", "confidence": 1.0}
                     for sample in current_model["samples"] if sample["code"] != code][:3]
        return {"preference_score": score, "preference_adjustment": 0.0,
                "preference_status": "active", "preference_support": len(neighbors),
                "preference_effective_samples": 3.0, "preference_neighbors": neighbors,
                "preference_priority_policy": PRIORITY_POLICY}

    monkeypatch.setattr(training, "score_preference", fake_score)
    monkeypatch.setattr(training, "calculate_price_metrics", lambda *args, **kwargs: {})
    return validate_preferences(model, histories, scan_records=records, as_of="2026-09-20",
                                rules={}, gap_rules={})


def test_validation_disables_priority_for_baseline_and_preserves_old_report_order(monkeypatch):
    result = priority_validation_fixture(monkeypatch)
    assert result["baseline_top20"] == ["300001", "002138"]
    assert result["preference_top20"] == ["002138", "300001"]
    comparison = result["disabled_preference_comparison"]
    assert comparison["rank_changed_count"] == 2
    assert comparison["max_rank_movement"] == 1
    assert comparison["inversion_count"] == 1
    assert comparison["max_promoted_base_score_deficit"] == 5
    assert comparison["max_promoted_fund_score_deficit"] == 50
    assert result["prior_report_order_comparison"]["rank_changed_count"] == 0
    assert result["observation_only_controls"] == ["002138"]
    observation = next(r for r in result["top_preference_controls"] if r["code"] == "002138")
    assert observation["preference_control_role"] == "observation_only_not_training_or_return_negative"


def test_ablation_recomputes_priority_and_reports_rank_changes_without_score_changes(monkeypatch):
    result = priority_validation_fixture(monkeypatch)
    removed = next(r for r in result["single_sample_ablations"] if r["removed_code"] == "600000")
    assert removed["max_final_score_change"] == 0
    assert removed["priority_tier_changed_count"] == 1
    assert removed["rank_changed_count"] == 2
    assert removed["after_top20"] == ["300001", "002138"]


def test_fixed_similarity_perturbations_expose_boundary_and_rank_sensitivity(monkeypatch):
    result = priority_validation_fixture(monkeypatch)["similarity_sensitivity"]
    assert result["thresholds_optimized"] is False
    assert [r["score_shift"] for r in result["scenarios"]] == [-1.0, 1.0]
    assert all(r["rank_changed_count"] == 2 for r in result["scenarios"])
    assert all(r["top20_retained"] == 2 for r in result["scenarios"])
    boundary = next(r for r in result["boundary_checks"] if r["code"] == "002138")
    assert boundary == {"code": "002138", "preference_score": 60.2,
                        "nearest_priority_boundary": 60, "distance_to_boundary": .2,
                        "within_one_point": True, "priority_tier": 1,
                        "minus_one_tier": 0, "plus_one_tier": 1}


def test_rank_comparison_handles_missing_fund_scores_and_rejects_different_rosters():
    first = {"code": "600001", "base_final_score": 66, "fund_score": None}
    second = {"code": "600002", "base_final_score": 61, "fund_score": 30}
    result = _ranking_comparison([first, second], [second, first])
    assert result["inversions_with_both_fund_scores"] == 0
    assert result["max_fund_deficit_pair"] is None
    with pytest.raises(ValueError, match="相同且不重复"):
        _ranking_comparison([first, second], [second])
    with pytest.raises(ValueError, match="相同且不重复"):
        _ranking_comparison([first, second], [first, second, second])


def test_training_persists_observation_notes_without_adding_them_as_samples(tmp_path, monkeypatch):
    model = fixture_model()
    samples = [{key: sample[key] for key in ("code", "name", "as_of", "source", "confidence")}
               for sample in model["samples"]]
    excluded = [{"code": "002138", "reason": "用户降级为观察，不是收益负例"}]
    manifest = tmp_path / "samples.json"
    manifest.write_text(json.dumps({"observed_at": "2026-09-20", "samples": samples,
                                    "excluded_samples": excluded}), encoding="utf-8")
    history = pd.DataFrame(model["samples"][0]["bars"])
    history["date"] = pd.to_datetime(history["date"])
    monkeypatch.setattr(training, "calculate_price_metrics", lambda *args, **kwargs: {})
    result = train_preferences(samples_path=manifest, output_path=tmp_path / "model.json",
                               validation_path=tmp_path / "validation.json", baseline_path=None,
                               provider=SimpleNamespace(history=lambda code, days: history),
                               config=ScreenConfig(), available_from="2026-09-20")
    saved = load_preference_model(tmp_path / "model.json")
    assert saved["excluded_samples"] == excluded
    assert "002138" not in {sample["code"] for sample in saved["samples"]}
    assert result["excluded_samples"] == excluded
    assert result["prior_report_source"]["path"] is None
