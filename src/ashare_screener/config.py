from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


DEFAULT_REFERENCES = [
    {"code": "600127", "name": "金健米业"},
    {"code": "600354", "name": "敦煌种业"},
    {"code": "600722", "name": "金牛化工"},
    {"code": "002212", "name": "天融信"},
    {"code": "000017", "name": "深中华A"},
    {"code": "002181", "name": "粤传媒"},
    {"code": "300016", "name": "北陆药业"},
    {"code": "002487", "name": "大金重工"},
]

DEFAULT_CALIBRATION_STOCKS = [
    {"code": "002281", "name": "光迅科技", "role": "target"},
    {"code": "000592", "name": "平潭发展", "role": "gap"},
    {"code": "300741", "name": "华宝股份", "role": "target"},
    {"code": "920895", "name": "花溪科技", "role": "target"},
    {"code": "688137", "name": "近岸蛋白", "role": "target"},
    {"code": "920087", "name": "秋乐种业", "role": "target"},
    {"code": "600354", "name": "敦煌种业", "role": "target"},
    {"code": "600792", "name": "云煤能源", "role": "target"},
    {
        "code": "300300",
        "name": "海峡创新",
        "role": "target",
    },
    {
        "code": "603396",
        "name": "金辰股份",
        "role": "target",
    },
    {"code": "600630", "name": "龙头股份", "role": "late"},
]

DEFAULT_WEIGHTS = {
    "hot": 0.10,
    "theme": 0.05,
    "technical": 0.35,
    "similarity": 0.10,
    "fund": 0.40,
}

DEFAULT_UNIVERSE_PREFILTER = {
    "turnover_min": 4.0,
    "turnover_max": 20.0,
    "volume_ratio_min": 1.0,
    "return_5d_max": 20.0,
    "return_20d_max": 15.0,
    "return_60d_max": 20.0,
    "limit_turnover_min": 0.5,
    "limit_turnover_max": 40.0,
}

DEFAULT_STRICT_RULES = {
    "drawdown_min": 0.35,
    "drawdown_max": 0.65,
    "bottom_volume_ratio_min": 1.10,
    "bottom_high_volume_days_min": 2,
    "bottom_red_volume_share_min": 0.60,
    "bottom_red_high_volume_days_min": 2,
    "right_edge_volume_ratio_min": 1.20,
    "turnover_avg_min": 5.0,
    "turnover_avg_max": 18.0,
    "limit_up_min": 2,
    "limit_down_min": 1,
    "max_return_1d": 0.08,
    "max_return_5d": 0.12,
    "max_return_20d": 0.15,
    "max_extension_ma20": 0.10,
    "max_recovery_from_trough": 0.35,
    "current_below_decline_peak_min": 0.20,
    "fund_tightness_ratio_max": 0.80,
    "fund_absolute_spread_max": 8.0,
    "fund_cross_min": 1,
    "fund_cross_max": 12,
}

DEFAULT_GAP_RULES = {
    "lookback_days": 25,
    "minimum_gap_pct": 0.012,
    "close_fill_tolerance": 0.005,
    "hold_min_days": 3,
    "consolidation_close_range_max": 0.18,
    "post_volume_ratio_min": 1.30,
    "post_volume_min_days": 3,
    "post_volume_active_fraction_min": 0.60,
}


@dataclass(slots=True)
class ScreenConfig:
    universe_mode: str = "all"
    hot_rank_limit: int = 80
    concept_limit: int = 4
    concept_members_limit: int = 6
    max_candidates: int = 80
    fund_flow_limit: int = 60
    report_limit: int = 30
    history_days: int = 300
    min_history_rows: int = 90
    history_workers: int = 8
    fund_workers: int = 3
    cache_minutes: int = 180
    exclude_st: bool = True
    exclude_beijing: bool = False
    universe_prefilter: dict[str, float] = field(
        default_factory=lambda: DEFAULT_UNIVERSE_PREFILTER.copy()
    )
    references: list[dict[str, str]] = field(
        default_factory=lambda: [item.copy() for item in DEFAULT_REFERENCES]
    )
    calibration_stocks: list[dict[str, str]] = field(
        default_factory=lambda: [item.copy() for item in DEFAULT_CALIBRATION_STOCKS]
    )
    weights: dict[str, float] = field(default_factory=lambda: DEFAULT_WEIGHTS.copy())
    strict_rules: dict[str, float] = field(
        default_factory=lambda: DEFAULT_STRICT_RULES.copy()
    )
    gap_rules: dict[str, float] = field(
        default_factory=lambda: DEFAULT_GAP_RULES.copy()
    )

    @classmethod
    def from_file(cls, path: str | Path | None) -> "ScreenConfig":
        if path is None:
            config = cls()
        else:
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
            allowed = set(cls.__dataclass_fields__)
            unknown = sorted(set(payload) - allowed)
            if unknown:
                raise ValueError(f"配置包含未知字段: {', '.join(unknown)}")
            config = cls(**payload)
        config.validate()
        return config

    def validate(self) -> None:
        if self.universe_mode not in {"all", "hot"}:
            raise ValueError("universe_mode 必须是 all 或 hot")
        positive_fields = (
            "hot_rank_limit",
            "max_candidates",
            "report_limit",
            "history_days",
            "min_history_rows",
            "history_workers",
            "fund_workers",
            "cache_minutes",
        )
        for name in positive_fields:
            if int(getattr(self, name)) <= 0:
                raise ValueError(f"{name} 必须大于 0")
        if self.concept_limit < 0 or self.concept_members_limit < 0:
            raise ValueError("concept_limit 和 concept_members_limit 不能小于 0")
        if self.fund_flow_limit < 0:
            raise ValueError("fund_flow_limit 不能小于 0")
        if set(self.weights) != set(DEFAULT_WEIGHTS):
            raise ValueError(
                "weights 必须包含 hot/theme/technical/similarity/fund"
            )
        if any(float(value) < 0 for value in self.weights.values()):
            raise ValueError("weights 不能为负数")
        if sum(float(value) for value in self.weights.values()) <= 0:
            raise ValueError("weights 总和必须大于 0")
        if set(self.universe_prefilter) != set(DEFAULT_UNIVERSE_PREFILTER):
            raise ValueError("universe_prefilter 字段必须与示例配置保持一致")
        if any(float(value) < 0 for value in self.universe_prefilter.values()):
            raise ValueError("universe_prefilter 不能包含负数")
        if self.universe_prefilter["turnover_min"] >= self.universe_prefilter["turnover_max"]:
            raise ValueError("universe_prefilter turnover_min 必须小于 turnover_max")
        if (
            self.universe_prefilter["limit_turnover_min"]
            >= self.universe_prefilter["limit_turnover_max"]
        ):
            raise ValueError(
                "universe_prefilter limit_turnover_min 必须小于 limit_turnover_max"
            )
        if set(self.strict_rules) != set(DEFAULT_STRICT_RULES):
            raise ValueError(
                "strict_rules 字段必须与示例配置保持一致，不可遗漏或新增"
            )
        if any(float(value) < 0 for value in self.strict_rules.values()):
            raise ValueError("strict_rules 不能包含负数")
        if self.strict_rules["drawdown_min"] >= self.strict_rules["drawdown_max"]:
            raise ValueError("drawdown_min 必须小于 drawdown_max")
        if self.strict_rules["turnover_avg_min"] >= self.strict_rules["turnover_avg_max"]:
            raise ValueError("turnover_avg_min 必须小于 turnover_avg_max")
        if not 0 < self.strict_rules["bottom_red_volume_share_min"] <= 1:
            raise ValueError("bottom_red_volume_share_min 必须在 0 到 1 之间")
        if set(self.gap_rules) != set(DEFAULT_GAP_RULES):
            raise ValueError("gap_rules 字段必须与示例配置保持一致")
        if any(float(value) < 0 for value in self.gap_rules.values()):
            raise ValueError("gap_rules 不能包含负数")
        if not 0 < self.gap_rules["post_volume_active_fraction_min"] <= 1:
            raise ValueError("post_volume_active_fraction_min 必须在 0 到 1 之间")
        for reference in self.references:
            code = str(reference.get("code", ""))
            if len(code) != 6 or not code.isdigit():
                raise ValueError(f"参考股票代码无效: {code!r}")
            timeframe = str(reference.get("timeframe", "daily"))
            if timeframe != "daily":
                raise ValueError(
                    f"参考股票 timeframe 必须是 daily: {code}"
                )
        for item in self.calibration_stocks:
            code = str(item.get("code", ""))
            role = str(item.get("role", ""))
            if len(code) != 6 or not code.isdigit():
                raise ValueError(f"校准股票代码无效: {code!r}")
            if role not in {"target", "gap", "late"}:
                raise ValueError(
                    f"校准股票 role 必须是 target、gap 或 late: {code}"
                )
            timeframe = str(item.get("timeframe", "daily"))
            if timeframe != "daily":
                raise ValueError(
                    f"校准股票 timeframe 必须是 daily: {code}"
                )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
