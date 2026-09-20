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
    DataSourceError,
    is_supported_a_share,
    market_prefix,
    match_sector_name,
    sector_board_candidates,
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
        self.source_summary["bottom_volume_confirmed"] = sum(
            bool(item.metrics.get("bottom_volume_confirmed")) for item in candidates
        )
        self.source_summary["bottom_red_volume_confirmed"] = sum(
            bool(item.metrics.get("bottom_red_volume_confirmed"))
            for item in candidates
        )
        self.source_summary["early_bottom_matches"] = sum(
            bool(item.metrics.get("early_bottom_match")) for item in candidates
        )

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
                if item.metrics.get("early_bottom_match")
            ]
            eligible_codes = {item.code for item in eligible}
            gap_candidates = [
                item
                for item in candidates
                if item.code not in eligible_codes
                and item.metrics.get("gap_setup_near")
            ]
            gap_codes = {item.code for item in gap_candidates}
            report_candidates = [
                item
                for item in candidates[: self.config.report_limit]
                if item.code not in eligible_codes and item.code not in gap_codes
            ]
            report_codes = {item.code for item in report_candidates}
            secondary = [
                item
                for item in candidates
                if item.code not in eligible_codes
                and item.code not in gap_codes
                and item.code not in report_codes
                and int(item.metrics.get("price_condition_count", 0)) >= 3
            ]
            secondary_codes = {item.code for item in secondary}
            remaining = [
                item
                for item in candidates
                if item.code not in eligible_codes
                and item.code not in gap_codes
                and item.code not in report_codes
                and item.code not in secondary_codes
            ]
            fund_targets = (
                eligible
                + gap_candidates
                + report_candidates
                + secondary
                + remaining
            )[: self.config.fund_flow_limit]
            self.progress(
                f"对 {len(fund_targets)} 只复核候选读取历史资金流并补当日四档快照"
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
                candidate.metrics.get("early_bottom_match")
                and
                int(candidate.metrics.get("price_condition_count", 0)) >= 4
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
                -int(bool(item.metrics.get("early_bottom_match"))),
                -int(bool(item.metrics.get("bottom_red_volume_confirmed"))),
                -int(item.metrics.get("criteria_passed", 0)),
                -float(item.metrics.get("final_score", 0)),
                float(item.metrics.get("rise_pressure", 99.0)),
                -int(bool(item.metrics.get("right_edge_volume_expanded"))),
                -int(bool(item.metrics.get("gap_setup_match"))),
                -int(item.metrics.get("gap_condition_count", 0)),
            )
        )

        self._load_missing_detail_snapshots(candidates)
        self._enrich_sector_context(candidates)

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
        snapshot_success = 0
        snapshot_only = 0
        failures: list[tuple[str, str]] = []

        def load_sources(candidate: Candidate) -> tuple[
            pd.DataFrame | None,
            pd.DataFrame | None,
            list[str],
        ]:
            historical = None
            snapshot = None
            errors = []
            try:
                historical = self.provider.fund_flow(candidate.code)
            except Exception as exc:
                errors.append(f"历史接口: {exc}")
            try:
                snapshot = self.provider.fund_flow_snapshot(candidate.code)
            except Exception as exc:
                errors.append(f"东方财富当日快照: {exc}")
            snapshot_date = (
                pd.to_datetime(candidate.history["date"], errors="coerce").max().date()
                if candidate.history is not None and not candidate.history.empty
                else self.provider.as_of
            )
            snapshot_as_of = (
                pd.to_datetime(snapshot["date"], errors="coerce").max().date()
                if snapshot is not None and not snapshot.empty
                else None
            )
            if historical is None and (
                snapshot is None or snapshot_as_of is None or snapshot_as_of < snapshot_date
            ):
                try:
                    snapshot = self.provider.fund_flow_snapshot_ths(
                        candidate.code, snapshot_date=snapshot_date
                    )
                except Exception as exc:
                    errors.append(f"同花顺当日快照: {exc}")
            return historical, snapshot, errors

        with ThreadPoolExecutor(max_workers=self.config.fund_workers) as executor:
            futures = {
                executor.submit(load_sources, candidate): candidate
                for candidate in candidates
            }
            for future in as_completed(futures):
                candidate = futures[future]
                try:
                    historical, raw_snapshot, source_errors = future.result()
                    snapshot = self._fund_snapshot_ratios(
                        raw_snapshot, candidate.history
                    )
                    candidate.fund_snapshot = snapshot
                    snapshot_days = len(snapshot) if snapshot is not None else 0
                    snapshot_compatible = bool(
                        snapshot is not None
                        and snapshot.attrs.get("compatible_with_history", False)
                    )
                    if snapshot_days:
                        snapshot_success += 1
                        candidate.metrics.update(
                            {
                                "fund_snapshot_days": snapshot_days,
                                "fund_snapshot_as_of": str(
                                    snapshot.iloc[-1]["date"].date()
                                ),
                                "fund_snapshot_source": snapshot.attrs.get("source"),
                                "fund_snapshot_cache_stale": bool(
                                    snapshot.attrs.get("cache_stale", False)
                                ),
                                "fund_snapshot_compatible": snapshot_compatible,
                            }
                        )

                    fund_cache_stale = bool(
                        historical is not None
                        and historical.attrs.get("cache_stale", False)
                    )
                    frames = []
                    if historical is not None:
                        frames.append(historical)
                    if (
                        snapshot is not None
                        and not snapshot.empty
                        and snapshot_compatible
                    ):
                        frames.append(snapshot)
                    if not frames:
                        if snapshot_days:
                            snapshot_only += 1
                            candidate.metrics["fund_data_status"] = (
                                "同花顺当日四档快照（独立口径，未参与历史评分）"
                                if not snapshot_compatible
                                else f"当日快照累计 {snapshot_days}/8 天，历史评分待确认"
                            )
                            candidate.risks.append(
                                "同花顺订单分档口径与东方财富历史线不同，仅作当日复核"
                                if not snapshot_compatible
                                else f"四档资金仅累计 {snapshot_days}/8 个交易日，未参与历史粘连评分"
                            )
                            raise DataSourceError(candidate.metrics["fund_data_status"])
                        raise DataSourceError(
                            "；".join(source_errors) or "没有资金数据"
                        )
                    combined = pd.concat(frames, ignore_index=True)
                    combined["date"] = pd.to_datetime(
                        combined["date"], errors="coerce"
                    )
                    combined = (
                        combined.dropna(subset=["date"])
                        .sort_values("date")
                        .drop_duplicates("date", keep="last")
                        .reset_index(drop=True)
                    )
                    combined.attrs.update(
                        source=(
                            "东方财富历史资金流缓存+当日四档快照"
                            if historical is not None
                            and snapshot_days
                            and snapshot_compatible
                            else "东方财富历史资金流"
                            if historical is not None
                            else "东方财富逐日四档资金快照累计"
                        ),
                        cache_stale=fund_cache_stale,
                        cache_saved_at=(
                            historical.attrs.get("cache_saved_at")
                            if historical is not None
                            else snapshot.attrs.get("cache_saved_at")
                            if snapshot is not None
                            else None
                        ),
                    )
                    if len(combined) < 8:
                        snapshot_only += int(snapshot_days > 0)
                        candidate.metrics["fund_data_status"] = (
                            f"当日快照累计 {snapshot_days}/8 天，历史评分待确认"
                        )
                        candidate.risks.append(
                            f"四档资金仅累计 {snapshot_days}/8 个交易日，未参与历史粘连评分"
                        )
                        raise DataSourceError(
                            f"四档历史资金不足；已累计同源快照 {snapshot_days}/8 天"
                        )

                    candidate.fund_flow = combined
                    fund_metrics = calculate_fund_metrics(
                        candidate.fund_flow, self.config.strict_rules
                    )
                    fund_metrics.pop("fund_proxy", None)
                    candidate.metrics.update(fund_metrics)
                    candidate.metrics["fund_cache_stale"] = fund_cache_stale
                    candidate.metrics["fund_cache_saved_at"] = (
                        candidate.fund_flow.attrs.get("cache_saved_at")
                    )
                    candidate.metrics["fund_data_status"] = (
                        "历史缓存+当日快照"
                        if historical is not None
                        and snapshot_days
                        and snapshot_compatible
                        else "历史四档资金+同花顺当日快照（独立口径）"
                        if historical is not None and snapshot_days
                        else "历史四档资金"
                        if historical is not None
                        else f"逐日快照累计 {len(combined)} 天"
                    )
                    candidate.reasons.extend(fund_metrics.get("fund_reasons", []))
                    candidate.risks.extend(fund_metrics.get("fund_risks", []))
                    if fund_cache_stale:
                        candidate.risks.append(
                            "四档历史段使用最近成功缓存，最新交易日已用同源快照校验"
                            if snapshot_days and snapshot_compatible
                            else "四档历史段使用最近成功缓存；同花顺当日快照独立展示"
                            if snapshot_days
                            else "四档资金流使用最近成功缓存，非本次实时返回"
                        )
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
        self.source_summary["fund_snapshot_success"] = snapshot_success
        self.source_summary["fund_snapshot_only"] = snapshot_only
        self.source_summary["fund_flow_stale_cache"] = sum(
            bool(item.metrics.get("fund_cache_stale")) for item in candidates
        )

    @staticmethod
    def _fund_snapshot_ratios(
        snapshot: pd.DataFrame | None,
        history: pd.DataFrame | None,
    ) -> pd.DataFrame | None:
        if snapshot is None or snapshot.empty or history is None or history.empty:
            return None
        raw = snapshot.copy()
        raw["date"] = pd.to_datetime(raw["date"], errors="coerce")
        amounts = history[["date", "amount"]].copy()
        amounts["date"] = pd.to_datetime(amounts["date"], errors="coerce")
        amounts["amount"] = pd.to_numeric(amounts["amount"], errors="coerce")
        merged = raw.merge(amounts, on="date", how="inner")
        merged = merged[merged["amount"] > 0].copy()
        if merged.empty:
            return None
        mapping = {
            "super_large_pct": "super_large_net_amount",
            "large_pct": "large_net_amount",
            "medium_pct": "medium_net_amount",
            "small_pct": "small_net_amount",
        }
        result = pd.DataFrame({"date": merged["date"]})
        for target, source in mapping.items():
            result[target] = (
                pd.to_numeric(merged[source], errors="coerce")
                / merged["amount"]
                * 100
            )
        result = result.dropna(subset=list(mapping)).sort_values("date")
        result = result.reset_index(drop=True)
        result.attrs.update(snapshot.attrs)
        return result

    def _load_missing_detail_snapshots(self, candidates: list[Candidate]) -> None:
        general_details = candidates[: self.config.report_limit]
        gap_details = [
            item for item in candidates if item.metrics.get("gap_setup_near")
        ][: self.config.report_limit]
        visible_details = []
        visible_codes: set[str] = set()
        for item in general_details + gap_details:
            if item.code not in visible_codes:
                visible_details.append(item)
                visible_codes.add(item.code)
        targets = [
            item
            for item in visible_details
            if item.fund_flow is None and item.fund_snapshot is None
        ]
        self.source_summary["fund_detail_snapshot_attempted"] = len(targets)
        if not targets:
            self.source_summary["fund_detail_snapshot_success"] = 0
            return

        self.progress(f"为 {len(targets)} 张可见复核卡补充同花顺当日四档快照")
        success = 0
        failures: list[tuple[str, str]] = []

        def load_snapshot(candidate: Candidate) -> pd.DataFrame:
            snapshot_date = pd.to_datetime(
                candidate.history["date"], errors="coerce"
            ).max().date()
            return self.provider.fund_flow_snapshot_ths(
                candidate.code, snapshot_date=snapshot_date
            )

        with ThreadPoolExecutor(max_workers=self.config.fund_workers) as executor:
            futures = {
                executor.submit(load_snapshot, candidate): candidate
                for candidate in targets
            }
            for future in as_completed(futures):
                candidate = futures[future]
                try:
                    raw = future.result()
                    snapshot = self._fund_snapshot_ratios(raw, candidate.history)
                    if snapshot is None or snapshot.empty:
                        raise DataSourceError("快照日期与日线成交额无法对应")
                    candidate.fund_snapshot = snapshot
                    candidate.metrics.update(
                        {
                            "fund_snapshot_days": len(snapshot),
                            "fund_snapshot_as_of": str(
                                snapshot.iloc[-1]["date"].date()
                            ),
                            "fund_snapshot_source": snapshot.attrs.get("source"),
                            "fund_snapshot_cache_stale": bool(
                                snapshot.attrs.get("cache_stale", False)
                            ),
                            "fund_snapshot_compatible": False,
                            "fund_data_status": (
                                "同花顺当日四档快照（独立口径，未参与历史评分）"
                            ),
                        }
                    )
                    candidate.risks.append(
                        "同花顺订单分档口径与东方财富历史线不同，仅作当日复核"
                    )
                    success += 1
                except Exception as exc:
                    failures.append((candidate.code, str(exc)))

        self.source_summary["fund_detail_snapshot_success"] = success
        self.source_summary["fund_snapshot_success"] = int(
            self.source_summary.get("fund_snapshot_success", 0)
        ) + success
        self.source_summary["fund_snapshot_only"] = int(
            self.source_summary.get("fund_snapshot_only", 0)
        ) + success
        if failures:
            first_code, first_message = failures[0]
            self.issues.append(
                ScanIssue(
                    "fund_snapshot_detail",
                    f"可见复核卡快照成功 {success} 只、失败 {len(failures)} 只；"
                    f"首个失败 {first_code}: {first_message}",
                )
            )

    def _enrich_sector_context(self, candidates: list[Candidate]) -> None:
        decisions = {"严格匹配", "接近标准", "待资金数据"}
        targets = [
            item
            for item in candidates
            if item.metrics.get("decision") in decisions
            or item.metrics.get("gap_setup_near")
        ]
        self.source_summary["sector_target_count"] = len(targets)
        if not targets:
            self.source_summary.update(
                sector_profile_success=0,
                sector_board_match_success=0,
                sector_member_success=0,
            )
            return

        self.progress(f"为 {len(targets)} 只推荐/缺口候选补充行业与板块热度")
        profile_failures: list[tuple[str, str]] = []
        max_workers = max(1, min(self.config.history_workers, 8, len(targets)))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(self.provider.stock_sector, candidate.code): candidate
                for candidate in targets
            }
            for future in as_completed(futures):
                candidate = futures[future]
                try:
                    candidate.metrics.update(future.result())
                    candidate.metrics["sector_enrichment_status"] = "profile_loaded"
                except Exception as exc:
                    candidate.metrics["sector_enrichment_status"] = "profile_unavailable"
                    candidate.metrics["sector_hot_reason"] = "搜狐行业分类未取得，板块热度不可判定"
                    profile_failures.append((candidate.code, str(exc)))

        profile_success = sum(
            bool(item.metrics.get("sector_name")) for item in targets
        )
        self.source_summary["sector_profile_success"] = profile_success
        self.source_summary["sector_profile_failures"] = len(profile_failures)
        if profile_failures:
            first_code, first_message = profile_failures[0]
            self.issues.append(
                ScanIssue(
                    "sector_profile",
                    f"搜狐行业分类成功 {profile_success} 只、失败 {len(profile_failures)} 只；"
                    f"首个失败 {first_code}: {first_message}",
                )
            )

        try:
            boards = self.provider.industry_sectors()
        except Exception as exc:
            for candidate in targets:
                if candidate.metrics.get("sector_name"):
                    candidate.metrics["sector_enrichment_status"] = "board_unavailable"
                    candidate.metrics["sector_hot_reason"] = (
                        "已取得搜狐行业分类；新浪行业板块行情不可用，热度不可判定"
                    )
            self.source_summary["sector_board_rows"] = 0
            self.source_summary["sector_board_match_success"] = 0
            self.source_summary["sector_member_success"] = 0
            self.issues.append(ScanIssue("sector_boards", str(exc)))
            return

        board_attrs = boards.attrs.copy()
        board_names = boards["sector_name"].astype(str).tolist()
        board_total = len(boards)
        self.source_summary["sector_board_rows"] = board_total
        self.source_summary["sector_board_cache_stale"] = bool(
            board_attrs.get("cache_stale", False)
        )
        excluded_boards = {"次新股", "开发区"}
        searchable_boards = boards.loc[
            ~boards["sector_name"].isin(excluded_boards)
        ].copy()
        self.source_summary["sector_board_queries"] = len(searchable_boards)
        member_failures: list[tuple[str, str]] = []
        member_frames: dict[str, pd.DataFrame] = {}
        if not searchable_boards.empty:
            member_workers = max(
                1, min(max(self.config.fund_workers, 4), 8, len(searchable_boards))
            )
            with ThreadPoolExecutor(max_workers=member_workers) as executor:
                futures = {
                    executor.submit(
                        self.provider.industry_members, str(row.sector_key)
                    ): str(row.sector_key)
                    for row in searchable_boards.itertuples(index=False)
                }
                for future in as_completed(futures):
                    sector_key = futures[future]
                    try:
                        member_frames[sector_key] = future.result()
                    except Exception as exc:
                        member_failures.append((sector_key, str(exc)))

        board_by_key = {
            str(row.sector_key): row for row in searchable_boards.itertuples(index=False)
        }
        memberships: dict[str, list[tuple[object, pd.Series, pd.DataFrame]]] = {
            item.code: [] for item in targets
        }
        target_codes = set(memberships)
        for sector_key, members in member_frames.items():
            board_row = board_by_key[sector_key]
            hits = members.loc[members["code"].isin(target_codes)]
            for _, member_row in hits.iterrows():
                memberships[str(member_row["code"])].append(
                    (board_row, member_row, members)
                )

        matched_count = 0
        for candidate in targets:
            sector_name = candidate.metrics.get("sector_name")
            if not sector_name:
                continue
            matches = memberships.get(candidate.code, [])
            expected_names = sector_board_candidates(sector_name, board_names)
            expected_matches = [
                item for item in matches if str(item[0].sector_name) in expected_names
            ]
            selected: tuple[object, pd.Series, pd.DataFrame] | None = None
            if len(expected_matches) == 1:
                selected = expected_matches[0]
            elif len(matches) == 1:
                selected = matches[0]
            elif len(matches) > 1:
                direct = match_sector_name(
                    sector_name, [str(item[0].sector_name) for item in matches]
                )
                direct_matches = [
                    item for item in matches if str(item[0].sector_name) == direct
                ]
                if len(direct_matches) == 1:
                    selected = direct_matches[0]

            if selected is None:
                if matches:
                    names = "、".join(str(item[0].sector_name) for item in matches)
                    candidate.metrics["sector_enrichment_status"] = "member_ambiguous"
                    candidate.metrics["sector_hot_reason"] = (
                        f"搜狐行业“{sector_name}”；代码同时出现在新浪板块 {names}，"
                        "无法唯一确定板块热度"
                    )
                elif member_failures:
                    candidate.metrics["sector_enrichment_status"] = "members_incomplete"
                    candidate.metrics["sector_hot_reason"] = (
                        f"搜狐行业“{sector_name}”；部分新浪成分表读取失败，"
                        "未能完整核验板块归属"
                    )
                else:
                    candidate.metrics["sector_enrichment_status"] = "member_unmatched"
                    candidate.metrics["sector_hot_reason"] = (
                        f"搜狐行业“{sector_name}”；新浪行业成分表中未找到该代码，"
                        "热度不可判定"
                    )
                continue

            board_row, member_row, members = selected
            board_rank = int(board_row.rank)
            board_change = float(board_row.change_pct)
            board_hot = board_rank <= 10 and board_change > 0
            member_rank = int(member_row["rank"])
            member_total = len(members)
            member_change = self._optional_float(member_row["change_pct"])
            hot_stock = bool(
                member_rank <= 5
                and member_change is not None
                and member_change > 0
            )
            member_attrs = members.attrs.copy()
            board_name = str(board_row.sector_name)
            candidate.metrics.update(
                {
                    "sector_enrichment_status": "complete",
                    "sector_board_name": board_name,
                    "sector_board_source": str(board_row.source),
                    "sector_board_rank": board_rank,
                    "sector_board_count": board_total,
                    "sector_board_change_pct": board_change,
                    "sector_board_hot": board_hot,
                    "sector_board_cache_stale": bool(
                        board_attrs.get("cache_stale", False)
                    ),
                    "sector_board_cache_saved_at": board_attrs.get("cache_saved_at"),
                    "sector_member_rank": member_rank,
                    "sector_member_count": member_total,
                    "sector_member_change_pct": member_change,
                    "sector_hot_stock": hot_stock,
                    "sector_member_source": str(member_row["source"]),
                    "sector_member_cache_stale": bool(
                        member_attrs.get("cache_stale", False)
                    ),
                    "sector_member_cache_saved_at": member_attrs.get(
                        "cache_saved_at"
                    ),
                    "sector_hot_reason": (
                        f"搜狐分类“{sector_name}”，按股票代码核验新浪归属“{board_name}”；"
                        f"板块当日涨幅第 {board_rank}/{board_total}，"
                        f"涨幅 {board_change:+.2f}%，"
                        f"{'属于' if board_hot else '不属于'}当日前10强势板块；"
                        f"个股板内涨幅第 {member_rank}/{member_total}"
                        + (
                            f"，涨幅 {member_change:+.2f}%"
                            if member_change is not None
                            else ""
                        )
                        + f"，{'属于' if hot_stock else '不属于'}板内前5强势股"
                    ),
                }
            )
            if board_hot:
                candidate.reasons.append(f"所属行业板块当日涨幅第 {board_rank} 名")
            if hot_stock:
                candidate.reasons.append(f"个股板块内当日涨幅第 {member_rank} 名")
            matched_count += 1

        self.source_summary["sector_board_match_success"] = matched_count
        self.source_summary["sector_member_success"] = matched_count
        self.source_summary["sector_member_board_success"] = len(member_frames)
        self.source_summary["sector_member_failures"] = len(member_failures)
        if member_failures:
            first_key, first_message = member_failures[0]
            self.issues.append(
                ScanIssue(
                    "sector_members",
                    f"新浪行业成分股成功 {len(member_frames)} 个板块、失败 "
                    f"{len(member_failures)} 个；首个失败 {first_key}: {first_message}",
                )
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
        if quote_date != self.provider.as_of or quote_date.weekday() >= 5:
            return None

        history = candidate.history
        if history is not None and len(history) >= 2:
            closes = pd.to_numeric(history["close"].tail(2), errors="coerce")
            quote_latest = self._optional_float(candidate.metrics.get("quote_latest"))
            quote_change = self._optional_float(
                candidate.metrics.get("quote_change_pct")
            )
            if closes.notna().all() and float(closes.iloc[-2]) != 0:
                history_change = (float(closes.iloc[-1]) / float(closes.iloc[-2]) - 1) * 100
                if (
                    quote_latest is not None
                    and quote_change is not None
                    and abs(quote_latest - float(closes.iloc[-1])) <= 0.001
                    and abs(quote_change - history_change) <= 0.011
                ):
                    return None
        return saved_at

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
