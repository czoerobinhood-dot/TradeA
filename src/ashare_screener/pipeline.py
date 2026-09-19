from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Callable

import pandas as pd

from ashare_screener.config import ScreenConfig
from ashare_screener.features import (
    best_shape_similarity,
    board_limit_rate,
    calculate_fund_metrics,
    calculate_price_metrics,
    current_reference_template,
    extract_reference_template,
)
from ashare_screener.models import (
    Candidate,
    ReferenceTemplate,
    ScanIssue,
    ScanOutcome,
)
from ashare_screener.provider import (
    AkshareProvider,
    is_supported_a_share,
    market_prefix,
)
from ashare_screener.scoring import classify_candidate, weighted_score


Progress = Callable[[str], None]


class Screener:
    def __init__(
        self,
        provider: AkshareProvider,
        config: ScreenConfig,
        progress: Progress | None = None,
        *,
        use_concepts: bool = True,
        use_fund_flow: bool = True,
    ) -> None:
        self.provider = provider
        self.config = config
        self.progress = progress or (lambda _: None)
        self.use_concepts = use_concepts
        self.use_fund_flow = use_fund_flow
        self.issues: list[ScanIssue] = []
        self.source_summary: dict[str, object] = {}

    def run(self) -> ScanOutcome:
        started = datetime.now()
        candidates = self._collect_candidates()
        if not candidates:
            return self._outcome("failed", started, [], [])

        pool_label = "全市场快照初筛后" if self.config.universe_mode == "all" else "候选池"
        self.progress(f"{pool_label}共 {len(candidates)} 只，开始读取前复权日线")
        self._load_histories(candidates)
        candidates = [item for item in candidates if item.history is not None]
        self.source_summary["history_success"] = len(candidates)
        history_sources: dict[str, int] = {}
        for candidate in candidates:
            source = str(candidate.history.attrs.get("source", "未知"))
            history_sources[source] = history_sources.get(source, 0) + 1
        self.source_summary["history_sources"] = history_sources
        self.source_summary["history_stale_cache"] = sum(
            bool(item.metrics.get("history_cache_stale")) for item in candidates
        )
        if not candidates:
            self.issues.append(ScanIssue("history", "没有任何候选取得有效日线"))
            return self._outcome("failed", started, [], [])

        templates = self._build_templates(candidates)
        self.source_summary["template_success"] = len(templates)
        if not templates:
            self.issues.append(
                ScanIssue("template", "参考股票未能形成有效上涨前模板，相似度停用")
            )

        valid: list[Candidate] = []
        for candidate in candidates:
            try:
                baseline_price_metrics = calculate_price_metrics(
                    candidate.history,
                    code=candidate.code,
                    rules=self.config.strict_rules,
                    gap_rules=self.config.gap_rules,
                )
                current_price_metrics = calculate_price_metrics(
                    candidate.history,
                    code=candidate.code,
                    rules=self.config.strict_rules,
                    gap_rules=self.config.gap_rules,
                    quote_latest=candidate.metrics.get("quote_latest"),
                    quote_change_pct=candidate.metrics.get("quote_change_pct"),
                    quote_date=self._quote_date_for_scan(candidate),
                )
                current_price_metrics.update(
                    {
                        "pre_quote_as_of": baseline_price_metrics.get("as_of"),
                        "pre_quote_primary_structure_match": baseline_price_metrics.get(
                            "primary_structure_match"
                        ),
                        "pre_quote_price_condition_count": baseline_price_metrics.get(
                            "price_condition_count"
                        ),
                        "pre_quote_price_condition_total": baseline_price_metrics.get(
                            "price_condition_total"
                        ),
                        "pre_quote_entry_late": baseline_price_metrics.get("entry_late"),
                    }
                )
                candidate.metrics.update(current_price_metrics)
                candidate.metrics.update(
                    best_shape_similarity(
                        candidate.history, templates, exclude_code=candidate.code
                    )
                )
                self._add_source_scores(candidate)
                self._merge_explanations(candidate)
                valid.append(candidate)
            except Exception as exc:  # upstream numeric data can be malformed
                self.issues.append(
                    ScanIssue("features", str(exc), code=candidate.code)
                )
        candidates = valid
        if not candidates:
            return self._outcome("failed", started, [], templates)

        for candidate in candidates:
            preliminary, _, _ = weighted_score(candidate.metrics, self.config.weights)
            candidate.metrics["preliminary_score"] = preliminary
        candidates.sort(
            key=lambda item: float(item.metrics.get("preliminary_score", 0)),
            reverse=True,
        )

        if self.use_fund_flow and self.config.fund_flow_limit > 0:
            eligible = [
                item
                for item in candidates
                if int(item.metrics.get("price_condition_count", 0)) >= 3
            ]
            eligible_codes = {item.code for item in eligible}
            remaining = [item for item in candidates if item.code not in eligible_codes]
            fund_targets = (eligible + remaining)[: self.config.fund_flow_limit]
            self.progress(
                f"对初筛前 {len(fund_targets)} 只读取四档资金流并构造资金博弈代理"
            )
            self._load_fund_flow(fund_targets)
        else:
            self.source_summary["fund_flow"] = "disabled"

        for candidate in candidates:
            candidate.metrics["strict_match"] = bool(
                candidate.metrics.get("price_core_match")
                and not candidate.metrics.get("entry_late")
                and candidate.metrics.get("fund_shape_match")
            )
            candidate.metrics["near_match"] = bool(
                candidate.metrics.get("primary_structure_match")
                and
                int(candidate.metrics.get("price_condition_count", 0)) >= 4
                and not candidate.metrics.get("entry_late")
                and candidate.metrics.get("fund_near_match")
            )
            candidate.metrics["criteria_passed"] = int(
                candidate.metrics.get("price_condition_count", 0)
            ) + int(candidate.metrics.get("fund_condition_count", 0))
            candidate.metrics["criteria_total"] = int(
                candidate.metrics.get("price_condition_total", 0)
            ) + int(candidate.metrics.get("fund_condition_total", 0))
            final_score, completeness, available = weighted_score(
                candidate.metrics, self.config.weights
            )
            candidate.metrics["final_score"] = final_score
            candidate.metrics["data_completeness"] = completeness
            candidate.metrics["available_components"] = available
            decision, decision_reason = classify_candidate(candidate.metrics)
            candidate.metrics["decision"] = decision
            candidate.metrics["decision_reason"] = decision_reason
        decision_order = {
            "严格匹配": 0,
            "接近标准": 1,
            "待资金数据": 2,
            "已启动/错过低位": 3,
            "数据不足": 4,
            "不符合": 5,
        }
        candidates.sort(
            key=lambda item: (
                decision_order.get(str(item.metrics.get("decision")), 9),
                -int(bool(item.metrics.get("gap_setup_match"))),
                -int(item.metrics.get("gap_condition_count", 0)),
                -int(item.metrics.get("criteria_passed", 0)),
                -float(item.metrics.get("final_score", 0)),
            )
        )

        status = "partial" if self.issues else "ok"
        return self._outcome(status, started, candidates, templates)

    def _collect_candidates(self) -> list[Candidate]:
        self.source_summary["selection_mode"] = self.config.universe_mode
        if self.config.universe_mode == "all":
            return self._collect_all_market_candidates()
        return self._collect_hot_candidates()

    def _collect_all_market_candidates(self) -> list[Candidate]:
        try:
            market = self.provider.all_stocks()
        except Exception as exc:
            self.issues.append(ScanIssue("all_market", str(exc)))
            self.source_summary["universe_rows"] = 0
            return []

        market_attrs = market.attrs.copy()
        self.source_summary["universe_rows"] = len(market)
        self.source_summary["universe_source"] = (
            str(market.iloc[0]["source"]) if not market.empty else "未知"
        )
        self.source_summary["universe_cache_stale"] = bool(
            market_attrs.get("cache_stale", False)
        )
        candidates: list[Candidate] = []
        allowed_rows = 0
        for row in market.itertuples(index=False):
            code = str(row.code)
            name = str(row.name)
            if not self._allowed(code, name):
                continue
            allowed_rows += 1
            if not self._passes_universe_prefilter(row):
                continue
            source = str(getattr(row, "source", "全市场实时行情"))
            candidate = Candidate(code=code, name=name, source_tags=[source])
            self._set_quote_metrics(
                candidate,
                latest=getattr(row, "latest", None),
                change_pct=getattr(row, "change_pct", None),
                source=source,
                attrs=market_attrs,
            )
            candidate.metrics.update(
                {
                    "universe_turnover": self._optional_float(
                        getattr(row, "turnover", None)
                    ),
                    "universe_volume_ratio": self._optional_float(
                        getattr(row, "volume_ratio", None)
                    ),
                    "universe_return_5d": self._optional_float(
                        getattr(row, "return_5d", None)
                    ),
                    "universe_return_20d": self._optional_float(
                        getattr(row, "return_20d", None)
                    ),
                    "universe_return_60d": self._optional_float(
                        getattr(row, "return_60d", None)
                    ),
                }
            )
            candidates.append(candidate)

        self.source_summary["universe_supported_rows"] = allowed_rows
        self.source_summary["universe_prefilter_pass"] = len(candidates)
        self.source_summary["universe_prefilter"] = self.config.universe_prefilter.copy()
        self.source_summary["concepts"] = "not_used_in_all_market_mode"
        known = {item.code: item for item in candidates}
        candidates = self._append_calibration_candidates(candidates, known)
        self._record_candidate_summary(candidates)
        return candidates

    def _collect_hot_candidates(self) -> list[Candidate]:
        merged: dict[str, Candidate] = {}
        try:
            hot_all = self.provider.hot_stocks()
            hot_attrs = hot_all.attrs.copy()
            hot = hot_all.head(self.config.hot_rank_limit)
            self.source_summary["hot_rank_rows"] = len(hot)
            if not hot.empty and "source" in hot.columns:
                self.source_summary["hot_source"] = str(hot.iloc[0]["source"])
            for row in hot.itertuples(index=False):
                code = str(row.code)
                name = str(row.name)
                if not self._allowed(code, name):
                    continue
                source = str(getattr(row, "source", "东方财富人气榜"))
                candidate = Candidate(
                    code=code,
                    name=name,
                    hot_rank=int(row.rank),
                    source_tags=[source],
                )
                self._set_quote_metrics(
                    candidate,
                    latest=getattr(row, "latest", None),
                    change_pct=getattr(row, "change_pct", None),
                    source=source,
                    attrs=hot_attrs,
                )
                merged[code] = candidate
        except Exception as exc:
            self.issues.append(ScanIssue("hot_rank", str(exc)))
            self.source_summary["hot_rank_rows"] = 0

        if self.use_concepts and self.config.concept_limit > 0:
            self._merge_concept_candidates(merged)
        else:
            self.source_summary["concepts"] = "disabled"

        hot_candidates = list(merged.values())
        hot_candidates.sort(
            key=lambda item: (
                item.hot_rank if item.hot_rank is not None else 10_000,
                item.concept_rank if item.concept_rank is not None else 10_000,
            )
        )
        candidates = hot_candidates[: self.config.max_candidates]
        candidates = self._append_calibration_candidates(candidates, merged)
        self._record_candidate_summary(candidates)
        return candidates

    def _append_calibration_candidates(
        self,
        candidates: list[Candidate],
        known: dict[str, Candidate],
    ) -> list[Candidate]:
        selected = {item.code: item for item in candidates}
        for item in self.config.calibration_stocks:
            code = item["code"]
            candidate = known.get(code) or Candidate(code=code, name=item["name"])
            candidate.calibration_role = item["role"]
            label = {
                "target": "用户目标样本",
                "gap": "用户缺口样本",
                "late": "用户后段样本",
            }[item["role"]]
            if label not in candidate.source_tags:
                candidate.source_tags.append(label)
            if code not in selected:
                candidates.append(candidate)
                selected[code] = candidate
        return candidates

    def _record_candidate_summary(self, candidates: list[Candidate]) -> None:
        self.source_summary["candidate_pool"] = len(candidates)
        self.source_summary["current_quote_rows"] = sum(
            item.metrics.get("quote_change_pct") is not None for item in candidates
        )
        self.source_summary["current_quote_stale_cache"] = sum(
            bool(item.metrics.get("quote_cache_stale")) for item in candidates
        )

    def _merge_concept_candidates(self, merged: dict[str, Candidate]) -> None:
        try:
            concepts = self.provider.hot_concepts()
            concepts = concepts[concepts["change_pct"] > 0].head(
                self.config.concept_limit
            )
            self.source_summary["hot_concepts"] = concepts["name"].tolist()
        except Exception as exc:
            self.issues.append(ScanIssue("concepts", str(exc)))
            self.source_summary["hot_concepts"] = []
            return

        for rank, row in enumerate(concepts.itertuples(index=False), start=1):
            concept = str(row.name)
            try:
                members = self.provider.concept_members(concept)
                member_attrs = members.attrs.copy()
                members = members.sort_values("change_pct", ascending=False).head(
                    self.config.concept_members_limit
                )
            except Exception as exc:
                self.issues.append(ScanIssue("concept_members", str(exc), code=concept))
                continue
            for member in members.itertuples(index=False):
                code = str(member.code)
                name = str(member.name)
                if not self._allowed(code, name):
                    continue
                candidate = merged.setdefault(code, Candidate(code=code, name=name))
                if concept not in candidate.concepts:
                    candidate.concepts.append(concept)
                candidate.concept_rank = min(candidate.concept_rank or rank, rank)
                if "热点概念" not in candidate.source_tags:
                    candidate.source_tags.append("热点概念")
                if candidate.metrics.get("quote_change_pct") is None:
                    self._set_quote_metrics(
                        candidate,
                        latest=None,
                        change_pct=getattr(member, "change_pct", None),
                        source=f"概念成分行情：{concept}",
                        attrs=member_attrs,
                    )

    def _load_histories(self, candidates: list[Candidate]) -> None:
        completed = 0
        total = len(candidates)
        with ThreadPoolExecutor(max_workers=self.config.history_workers) as executor:
            futures = {
                executor.submit(
                    self.provider.history, candidate.code, self.config.history_days
                ): candidate
                for candidate in candidates
            }
            for future in as_completed(futures):
                candidate = futures[future]
                try:
                    history = future.result()
                    if len(history) < self.config.min_history_rows:
                        raise ValueError(
                            f"有效日线仅 {len(history)} 根，少于 {self.config.min_history_rows}"
                        )
                    candidate.history = history
                    candidate.metrics["history_source"] = history.attrs.get(
                        "source", "未知"
                    )
                    candidate.metrics["history_cache_stale"] = bool(
                        history.attrs.get("cache_stale", False)
                    )
                    candidate.metrics["history_cache_saved_at"] = history.attrs.get(
                        "cache_saved_at"
                    )
                    if candidate.metrics["history_cache_stale"]:
                        candidate.risks.append("日线使用最近成功缓存，非本次实时返回")
                except Exception as exc:
                    self.issues.append(
                        ScanIssue("history", str(exc), code=candidate.code)
                    )
                finally:
                    completed += 1
                    if completed % 50 == 0 or completed == total:
                        self.progress(f"日线进度 {completed}/{total}")

    def _build_templates(
        self, candidates: list[Candidate]
    ) -> list[ReferenceTemplate]:
        self.progress("从已确认样本自动提取同周期价格路径模板")
        histories = {
            candidate.code: candidate.history
            for candidate in candidates
            if candidate.history is not None
        }
        target_samples = [
            item
            for item in self.config.calibration_stocks
            if item["role"] == "target"
        ]
        template_items = self.config.references + target_samples
        unique_items = {item["code"]: item for item in template_items}
        missing_references = [
            item for item in unique_items.values() if item["code"] not in histories
        ]
        if missing_references:
            with ThreadPoolExecutor(max_workers=self.config.history_workers) as executor:
                futures = {
                    executor.submit(
                        self.provider.history, item["code"], self.config.history_days
                    ): item
                    for item in missing_references
                }
                for future in as_completed(futures):
                    item = futures[future]
                    try:
                        histories[item["code"]] = future.result()
                    except Exception as exc:
                        self.issues.append(
                            ScanIssue("reference_history", str(exc), code=item["code"])
                        )

        templates: list[ReferenceTemplate] = []
        for item in self.config.references:
            history = histories.get(item["code"])
            if history is None:
                continue
            template = extract_reference_template(
                history,
                code=item["code"],
                name=item["name"],
                timeframe=str(item.get("timeframe", "daily")),
            )
            if template is None:
                self.issues.append(
                    ScanIssue(
                        "template",
                        "最近样本中未找到未来 10 日涨幅至少 12% 的可用起点",
                        code=item["code"],
                    )
                )
                continue
            templates.append(template)
        for item in target_samples:
            history = histories.get(item["code"])
            if history is None:
                continue
            template = current_reference_template(
                history,
                code=item["code"],
                name=item["name"],
                timeframe=str(item.get("timeframe", "daily")),
            )
            if template is not None:
                templates.append(template)
        self.source_summary["current_target_templates"] = sum(
            item.kind == "current_target" for item in templates
        )
        self.source_summary["weekly_templates"] = sum(
            item.timeframe == "weekly" for item in templates
        )
        return templates

    def _load_fund_flow(self, candidates: list[Candidate]) -> None:
        success = 0
        failures: list[tuple[str, str]] = []
        with ThreadPoolExecutor(max_workers=self.config.fund_workers) as executor:
            futures = {
                executor.submit(self.provider.fund_flow, candidate.code): candidate
                for candidate in candidates
            }
            for future in as_completed(futures):
                candidate = futures[future]
                try:
                    candidate.fund_flow = future.result()
                    fund_cache_stale = bool(
                        candidate.fund_flow.attrs.get("cache_stale", False)
                    )
                    fund_metrics = calculate_fund_metrics(
                        candidate.fund_flow, self.config.strict_rules
                    )
                    fund_metrics.pop("fund_proxy", None)
                    candidate.metrics.update(fund_metrics)
                    candidate.metrics["fund_cache_stale"] = fund_cache_stale
                    candidate.metrics["fund_cache_saved_at"] = (
                        candidate.fund_flow.attrs.get("cache_saved_at")
                    )
                    candidate.reasons.extend(fund_metrics.get("fund_reasons", []))
                    candidate.risks.extend(fund_metrics.get("fund_risks", []))
                    if fund_cache_stale:
                        candidate.risks.append("四档资金流使用最近成功缓存，非本次实时返回")
                    success += 1
                except Exception as exc:
                    failures.append((candidate.code, str(exc)))
        if failures and success == 0:
            first_code, first_message = failures[0]
            self.issues.append(
                ScanIssue(
                    "fund_flow",
                    "四档历史资金流本轮不可用，熔断后未重复访问故障端点；"
                    f"尝试候选 {len(failures)} 只，首个失败 {first_code}: {first_message}",
                )
            )
        elif len(failures) > 5:
            first_code, first_message = failures[0]
            self.issues.append(
                ScanIssue(
                    "fund_flow",
                    f"四档资金流成功 {success} 只、失败 {len(failures)} 只；"
                    f"同源错误已合并，首个失败 {first_code}: {first_message}",
                )
            )
        elif failures:
            self.issues.extend(
                ScanIssue("fund_flow", message, code=code)
                for code, message in failures
            )
        self.source_summary["fund_flow_success"] = success
        self.source_summary["fund_flow_attempted"] = len(candidates)
        self.source_summary["fund_flow_stale_cache"] = sum(
            bool(item.metrics.get("fund_cache_stale")) for item in candidates
        )

    def _add_source_scores(self, candidate: Candidate) -> None:
        if candidate.hot_rank is not None:
            candidate.metrics["hot_score"] = round(
                100
                * max(
                    0,
                    (self.config.hot_rank_limit - candidate.hot_rank + 1)
                    / self.config.hot_rank_limit,
                ),
                2,
            )
            hot_source = next(
                (
                    item
                    for item in candidate.source_tags
                    if "人气" in item or "热点代理" in item
                ),
                "热点候选池",
            )
            candidate.reasons.append(f"{hot_source}第 {candidate.hot_rank} 名")
        elif self.config.universe_mode == "all":
            candidate.metrics["hot_score"] = None
            candidate.reasons.append("通过全A股实时快照初筛")
        else:
            candidate.metrics["hot_score"] = 0.0
        if candidate.concept_rank is not None and self.config.concept_limit > 0:
            candidate.metrics["theme_score"] = round(
                100
                * (self.config.concept_limit - candidate.concept_rank + 1)
                / self.config.concept_limit,
                2,
            )
            candidate.reasons.append(
                f"属于当日强势概念：{', '.join(candidate.concepts)}"
            )
        else:
            candidate.metrics["theme_score"] = None

    @staticmethod
    def _optional_float(value: object) -> float | None:
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return None
        return numeric if pd.notna(numeric) else None

    def _passes_universe_prefilter(self, row: object) -> bool:
        rules = self.config.universe_prefilter
        code = str(getattr(row, "code", ""))
        turnover = self._optional_float(getattr(row, "turnover", None))
        volume_ratio = self._optional_float(getattr(row, "volume_ratio", None))
        change_pct = self._optional_float(getattr(row, "change_pct", None))
        if turnover is None or volume_ratio is None or change_pct is None:
            return False
        if volume_ratio < rules["volume_ratio_min"]:
            return False

        limit_like = change_pct >= board_limit_rate(code) * 100 - 0.5
        if limit_like:
            return (
                rules["limit_turnover_min"]
                <= turnover
                <= rules["limit_turnover_max"]
            )
        if not rules["turnover_min"] <= turnover <= rules["turnover_max"]:
            return False

        for name, maximum in (
            ("return_5d", rules["return_5d_max"]),
            ("return_20d", rules["return_20d_max"]),
            ("return_60d", rules["return_60d_max"]),
        ):
            value = self._optional_float(getattr(row, name, None))
            if value is not None and value > maximum:
                return False
        return True

    @staticmethod
    def _set_quote_metrics(
        candidate: Candidate,
        *,
        latest: object,
        change_pct: object,
        source: str,
        attrs: dict[str, object],
    ) -> None:
        candidate.metrics["quote_latest"] = Screener._optional_float(latest)
        candidate.metrics["quote_change_pct"] = Screener._optional_float(change_pct)
        candidate.metrics["quote_source"] = source
        candidate.metrics["quote_cache_stale"] = bool(
            attrs.get("cache_stale", False)
        )
        candidate.metrics["quote_cache_saved_at"] = attrs.get("cache_saved_at")

    def _quote_date_for_scan(self, candidate: Candidate) -> object:
        saved_at = candidate.metrics.get("quote_cache_saved_at")
        if not saved_at:
            return None
        try:
            quote_date = datetime.fromisoformat(str(saved_at).replace("Z", "+00:00")).date()
        except ValueError:
            return None
        return saved_at if quote_date == self.provider.as_of else None

    def _merge_explanations(self, candidate: Candidate) -> None:
        candidate.reasons.extend(candidate.metrics.get("technical_reasons", []))
        candidate.risks.extend(candidate.metrics.get("technical_risks", []))
        similarity = candidate.metrics.get("similarity_score")
        reference = candidate.metrics.get("similar_reference")
        if similarity is not None and reference:
            timeframe = candidate.metrics.get("similar_reference_timeframe")
            timeframe_label = "周线" if timeframe == "weekly" else "日线"
            candidate.reasons.append(
                f"{timeframe_label}路径与 {reference} 样本相似度 {similarity:.1f}"
            )

    def _allowed(self, code: str, name: str) -> bool:
        if not is_supported_a_share(code):
            return False
        if self.config.exclude_beijing and market_prefix(code) == "bj":
            return False
        normalized_name = name.upper().replace(" ", "")
        if self.config.exclude_st and ("ST" in normalized_name or "退" in name):
            return False
        return True

    def _outcome(
        self,
        status: str,
        started: datetime,
        candidates: list[Candidate],
        templates: list[ReferenceTemplate],
    ) -> ScanOutcome:
        return ScanOutcome(
            status=status,
            started_at=started,
            finished_at=datetime.now(),
            candidates=candidates,
            templates=templates,
            issues=self.issues,
            source_summary=self.source_summary,
        )
