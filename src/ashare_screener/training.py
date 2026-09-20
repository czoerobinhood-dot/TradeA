"""Reproducible, offline preference calibration; never treats favorites as returns."""
from __future__ import annotations

from copy import deepcopy
from datetime import date
import hashlib
import json
import math
from pathlib import Path
from typing import Any

from ashare_screener.features import calculate_price_metrics
from ashare_screener.preferences import (
    ALGORITHM_VERSION, MAX_ADJUSTMENT, SHRINKAGE_PRIOR, WINDOWS,
    NEIGHBOR_COUNT, MIN_REFERENCE_STOCKS, build_preference_model, score_preference,
    PRIORITY_POLICY, PREFERENCE_BAND_WIDTH, QUALITY_BAND_WIDTH,
)
from ashare_screener.scoring import apply_preference_adjustment, candidate_sort_key


def _digest(payload: dict) -> str:
    content = {key: value for key, value in payload.items() if key != "model_id"}
    encoded = json.dumps(content, sort_keys=True, ensure_ascii=False, allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def load_preference_model(path: str | Path) -> dict:
    model = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(model, dict) or model.get("schema_version") != 1:
        raise ValueError("偏好模型格式无效")
    if model.get("model_id") != _digest(model):
        raise ValueError("偏好模型校验失败，请重新训练")
    if model.get("algorithm_version") != ALGORITHM_VERSION or model.get("model_version") != ALGORITHM_VERSION:
        raise ValueError("偏好模型算法版本不兼容")
    try:
        date.fromisoformat(model.get("available_from", ""))
    except (TypeError, ValueError) as exc:
        raise ValueError("偏好模型缺少有效导入日期") from exc
    if not isinstance(model.get("samples"), list) or len(model["samples"]) < 4:
        raise ValueError("至少需要 4 只独立股票，才能排除自身后保留 3 个参考")
    fixed = {"max_adjustment": MAX_ADJUSTMENT, "shrinkage_prior": SHRINKAGE_PRIOR,
             "windows": list(WINDOWS), "neighbor_count": NEIGHBOR_COUNT,
             "min_reference_stocks": MIN_REFERENCE_STOCKS,
             "priority_policy": PRIORITY_POLICY,
             "preference_band_width": PREFERENCE_BAND_WIDTH,
             "quality_band_width": QUALITY_BAND_WIDTH}
    if any(model.get(key) != value for key, value in fixed.items()):
        raise ValueError("偏好模型固定参数被修改，请重新训练")
    codes = [sample.get("code") if isinstance(sample, dict) else None for sample in model["samples"]]
    if any(not isinstance(code, str) or len(code) != 6 or not code.isdigit() for code in codes):
        raise ValueError("偏好模型股票代码无效")
    if len(set(codes)) != len(codes) or len(set(codes)) < 4:
        raise ValueError("偏好模型包含重复股票或独立股票不足")
    return model


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    temporary.replace(path)


def _rank_key(record: dict) -> tuple:
    return candidate_sort_key(record)


def _finite(value: Any) -> float | None:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return numeric if math.isfinite(numeric) else None


def _ranking_comparison(before: list[dict], after: list[dict]) -> dict:
    """Compare the same stock roster, preserving the supplied reference order."""
    old_positions = {record["code"]: index for index, record in enumerate(before, 1)}
    new_positions = {record["code"]: index for index, record in enumerate(after, 1)}
    if (len(old_positions) != len(before) or len(new_positions) != len(after)
            or old_positions.keys() != new_positions.keys()):
        raise ValueError("排序验证必须使用相同且不重复的股票集合")
    changes = [
        {"code": record["code"], "name": record.get("name"),
         "before_rank": old_positions[record["code"]],
         "after_rank": new_positions[record["code"]],
         "rank_gain": old_positions[record["code"]] - new_positions[record["code"]]}
        for record in after
        if old_positions[record["code"]] != new_positions[record["code"]]
    ]
    inversions = 0
    max_base_deficit = max_fund_deficit = 0.0
    max_base_pair = max_fund_pair = None
    fund_pairs = 0
    for index, overtaken in enumerate(before):
        for promoted in before[index + 1:]:
            if new_positions[promoted["code"]] >= new_positions[overtaken["code"]]:
                continue
            inversions += 1
            pair = {"promoted_code": promoted["code"], "overtaken_code": overtaken["code"]}
            old_base = _finite(overtaken.get("base_final_score", overtaken.get("final_score")))
            new_base = _finite(promoted.get("base_final_score", promoted.get("final_score")))
            if old_base is not None and new_base is not None and old_base - new_base > max_base_deficit:
                max_base_deficit = old_base - new_base
                max_base_pair = pair
            old_fund, new_fund = _finite(overtaken.get("fund_score")), _finite(promoted.get("fund_score"))
            if old_fund is not None and new_fund is not None:
                fund_pairs += 1
                if old_fund - new_fund > max_fund_deficit:
                    max_fund_deficit = old_fund - new_fund
                    max_fund_pair = pair
    top_n = min(20, len(before))
    old_top = [record["code"] for record in before[:top_n]]
    new_top = [record["code"] for record in after[:top_n]]
    return {
        "comparison_count": len(before), "rank_changed_count": len(changes),
        "max_rank_movement": max((abs(item["rank_gain"]) for item in changes), default=0),
        "rank_changes": changes, "inversion_count": inversions,
        "max_promoted_base_score_deficit": round(max_base_deficit, 4),
        "max_base_deficit_pair": max_base_pair,
        "max_promoted_fund_score_deficit": round(max_fund_deficit, 4),
        "max_fund_deficit_pair": max_fund_pair,
        "inversions_with_both_fund_scores": fund_pairs,
        "before_top20": old_top, "after_top20": new_top,
        "top20_retained": len(set(old_top) & set(new_top)), "top_n": top_n,
    }


def _similarity_sensitivity(ranked: list[dict]) -> dict:
    """Predeclared uniform +/-1 point shifts; never searches for a good threshold."""
    scenarios = []
    shifted_records = {}
    for shift in (-1.0, 1.0):
        changed = []
        for original in ranked:
            record = deepcopy(original)
            score = _finite(record.get("preference_score"))
            effective = _finite(record.get("preference_effective_samples"))
            if score is not None and effective is not None and record.get("preference_status") == "active":
                record["preference_score"] = min(100.0, max(0.0, score + shift))
                record["preference_adjustment"] = (
                    MAX_ADJUSTMENT * effective / (effective + SHRINKAGE_PRIOR)
                    * (record["preference_score"] - 50) / 50
                )
            apply_preference_adjustment(record)
            changed.append(record)
        changed.sort(key=_rank_key)
        shifted_records[shift] = {record["code"]: record for record in changed}
        scenarios.append({"score_shift": shift, **_ranking_comparison(ranked, changed),
                          "priority_tier_changed_count": sum(
                              shifted_records[shift][r["code"]]["preference_priority_tier"]
                              != r["preference_priority_tier"] for r in ranked)})
    boundaries = []
    thresholds = list(range(60, 101, PREFERENCE_BAND_WIDTH))
    for record in ranked:
        score = _finite(record.get("preference_score"))
        if not record.get("preference_priority_enabled") or score is None:
            continue
        nearest = min(thresholds, key=lambda value: abs(value - score))
        boundaries.append({
            "code": record["code"], "preference_score": score,
            "nearest_priority_boundary": nearest,
            "distance_to_boundary": round(abs(nearest - score), 4),
            "within_one_point": abs(nearest - score) <= 1,
            "priority_tier": record["preference_priority_tier"],
            "minus_one_tier": shifted_records[-1.0][record["code"]]["preference_priority_tier"],
            "plus_one_tier": shifted_records[1.0][record["code"]]["preference_priority_tier"],
        })
    return {"method": "fixed_uniform_minus_one_plus_one_similarity_points",
            "thresholds_optimized": False, "scenarios": scenarios,
            "boundary_checks": boundaries,
            "within_one_point_count": sum(item["within_one_point"] for item in boundaries)}


def validate_preferences(model: dict, histories: dict, *, scan_records: list[dict],
                         as_of: str, rules: dict, gap_rules: dict) -> dict:
    """Stock-held-out and deletion stability checks, not a financial backtest."""
    sample_codes = {sample["code"] for sample in model["samples"]}
    sample_checks = []
    for sample in model["samples"]:
        history = histories[sample["code"]]
        history = history.loc[history["date"] <= sample["as_of"]]
        shape = score_preference(history, model, code=sample["code"], as_of=as_of)
        metrics = calculate_price_metrics(history, code=sample["code"], rules=rules, gap_rules=gap_rules)
        sample_checks.append({
            "code": sample["code"], "name": sample["name"], "as_of": sample["as_of"],
            "source": sample["source"], "confidence": sample["confidence"],
            "leave_one_stock_out_score": shape.get("preference_score"),
            "support": shape.get("preference_support"),
            "neighbors": shape.get("preference_neighbors"),
            "self_excluded": all(n["code"] != sample["code"] for n in shape.get("preference_neighbors", [])),
            "max_drawdown": metrics.get("max_drawdown_250"),
            "recovery_from_trough": metrics.get("recovery_from_trough"),
            "bottom_volume_confirmed": metrics.get("bottom_volume_confirmed"),
            "bottom_red_volume_confirmed": metrics.get("bottom_red_volume_confirmed"),
            "early_bottom_match": metrics.get("early_bottom_match"),
            "entry_late": metrics.get("entry_late"),
        })
    observation_only = {item["code"] for item in model.get("excluded_samples", [])
                        if isinstance(item, dict) and item.get("code")}
    controls, excluded = [], []
    for record in scan_records:
        if record["code"] in sample_codes:
            continue
        history = histories.get(record["code"])
        if history is None:
            excluded.append({"code": record["code"], "reason": "日线缓存不可用"})
            continue
        candidate = deepcopy(record)
        # Earlier preference runs must never become this run's baseline.
        stored_base = candidate.get("base_final_score")
        candidate["base_final_score"] = float(candidate["final_score"] if stored_base is None else stored_base)
        candidate["final_score"] = candidate["base_final_score"]
        candidate["preference_control_role"] = (
            "observation_only_not_training_or_return_negative"
            if record["code"] in observation_only else "unlabelled_control"
        )
        candidate.update(score_preference(history, model, code=record["code"], as_of=as_of))
        apply_preference_adjustment(candidate)
        controls.append(candidate)
    baseline = sorted(controls, key=lambda r: candidate_sort_key(r, use_preference=False))
    ranked = sorted(controls, key=_rank_key)
    disabled_comparison = _ranking_comparison(baseline, ranked)
    # controls retains the supplied report order: never reconstruct the old ranking
    # using the new policy when comparing an eight-sample report with this model.
    prior_report_comparison = _ranking_comparison(controls, ranked)
    top_n = min(20, len(controls))
    baseline_top = [r["code"] for r in baseline[:top_n]]
    new_top = [r["code"] for r in ranked[:top_n]]
    ablations = []
    for removed in sorted(sample_codes):
        reduced = {**model, "samples": [s for s in model["samples"] if s["code"] != removed]}
        changed = []
        maximum = 0.0
        for original in controls:
            record = deepcopy(original)
            record.update(score_preference(histories[record["code"]], reduced, code=record["code"], as_of=as_of))
            apply_preference_adjustment(record)
            maximum = max(maximum, abs(record["final_score"] - original["final_score"]))
            changed.append(record)
        changed.sort(key=_rank_key)
        ablations.append({"removed_code": removed, "max_final_score_change": round(maximum, 4),
                          **_ranking_comparison(ranked, changed),
                          "priority_tier_changed_count": sum(
                              original["preference_priority_tier"] != record["preference_priority_tier"]
                              for original, record in zip(sorted(controls, key=lambda r: r["code"]),
                                                          sorted(changed, key=lambda r: r["code"])))})
    return {
        "validation_type": "stock_held_out_shape_and_ablation_stability_only",
        "profitability_validated": False,
        "limitations": [
            "只有已确认形态偏好，未训练股票是未标注对照；单独标记的观察样本也不是收益负例。",
            "留一股票验证只检查形态与稳定性，不证明胜率或收益提升。",
            "收藏及本次截图在导入后才可使用，不能倒填为历史已知标签。",
            "对照来自当前候选池与缓存，存在选样偏差，不是全市场时间外回测。",
            "前复权日线是本次抓取版本，并非历史时点快照；未来收益尚未成熟。",
            "原有历史最佳上涨片段仍属启发式参考，不作为本次训练验证证据。",
            "偏好档只在相同条件与基础综合分档内排序，名次影响不受总分微调上限约束。",
            "固定正负1分敏感性检查是整体同向扰动，不是逐股最坏情形或收益验证。",
        ],
        "model_id": model["model_id"], "available_from": model["available_from"],
        "sample_count": len(sample_codes), "sample_checks": sample_checks,
        "control_count": len(controls), "excluded_controls": excluded,
        "classification_changes": sum(r.get("decision") != next(x.get("decision") for x in scan_records if x["code"] == r["code"]) for r in controls),
        "adjusted_count": sum(bool(r["preference_applied_adjustment"]) for r in controls),
        "max_absolute_applied_adjustment": max((abs(r["preference_applied_adjustment"]) for r in controls), default=0),
        "baseline_top20": baseline_top, "preference_top20": new_top,
        "top20_retained": len(set(baseline_top) & set(new_top)), "top_n": top_n,
        "disabled_preference_comparison": disabled_comparison,
        "prior_report_order_comparison": prior_report_comparison,
        "single_sample_ablations": ablations,
        "similarity_sensitivity": _similarity_sensitivity(ranked),
        "observation_only_controls": [r["code"] for r in controls if r["code"] in observation_only],
        "excluded_samples": deepcopy(model.get("excluded_samples", [])),
        "top_preference_controls": [{key: r.get(key) for key in (
            "code", "name", "decision", "preference_score", "base_final_score", "final_score",
            "fund_score", "preference_applied_adjustment", "preference_neighbors",
            "preference_control_role", "preference_priority_enabled", "preference_priority_tier")}
            for r in sorted(controls, key=lambda r: -(r.get("preference_score") or 0))[:20]],
    }


def train_preferences(*, samples_path: str | Path, output_path: str | Path,
                      validation_path: str | Path, baseline_path: str | Path | None,
                      provider: Any, config: Any, available_from: str) -> dict:
    available_day = date.fromisoformat(available_from)
    payload = json.loads(Path(samples_path).read_text(encoding="utf-8"))
    try:
        observed_day = date.fromisoformat(payload["observed_at"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("样本清单必须提供真实的 observed_at 导入日期") from exc
    if available_day < observed_day:
        raise ValueError("模型可用日期不能早于偏好实际导入日期，禁止回填历史")
    samples = payload["samples"]
    baseline_payload = (json.loads(Path(baseline_path).read_text(encoding="utf-8"))
                        if baseline_path else {})
    records = baseline_payload.get("candidates", [])
    histories, load_failures = {}, []
    for code in sorted({s["code"] for s in samples} | {r["code"] for r in records}):
        try:
            histories[code] = provider.history(code, config.history_days)
        except Exception as exc:
            load_failures.append({"code": code, "reason": type(exc).__name__})
    model = build_preference_model(samples, histories, available_from=available_from)
    if len(model["samples"]) < 4:
        raise ValueError("可用独立样本不足 4，只生成至少 3 个异股邻居所需样本足够时才能启用")
    model["data_sources"] = {code: str(history.attrs.get("source", "unknown"))
                             for code, history in histories.items() if code in {s["code"] for s in model["samples"]}}
    model["purpose"] = "daily_shape_preference_only_not_return_prediction"
    model["excluded_samples"] = deepcopy(payload.get("excluded_samples", []))
    model["manifest_sha256"] = hashlib.sha256(Path(samples_path).read_bytes()).hexdigest()
    model["model_id"] = _digest(model)
    validation = validate_preferences(model, histories, scan_records=records,
                                     as_of=available_from, rules=config.strict_rules, gap_rules=config.gap_rules)
    validation["load_failures"] = load_failures
    validation["skipped_samples"] = model.get("skipped_samples", [])
    validation["prior_report_source"] = {
        "path": str(baseline_path) if baseline_path else None,
        "finished_at": baseline_payload.get("finished_at"),
        "preference_model": baseline_payload.get("source_summary", {}).get("preference_model"),
        "order_method": "original_file_order_filtered_to_same_comparable_stocks",
    }
    if validation["classification_changes"] or validation["max_absolute_applied_adjustment"] > 2:
        raise ValueError("偏好更新突破分类或调分限制，模型未保存")
    _write_json(Path(output_path).parent / "versions" / f"{model['model_id']}.json", model)
    _write_json(Path(output_path), model)
    _write_json(Path(validation_path), validation)
    return validation
