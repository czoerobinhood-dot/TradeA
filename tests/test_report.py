from datetime import datetime

import numpy as np
import pandas as pd

from ashare_screener.models import Candidate, ScanOutcome
from ashare_screener.report import candidate_record, render_html


def make_candidate(code: str, name: str, *, limit_up: bool) -> Candidate:
    dates = pd.bdate_range("2026-06-01", periods=60)
    close = np.linspace(10.0, 10.6, len(dates))
    history = pd.DataFrame(
        {
            "date": dates,
            "open": close * 0.99,
            "close": close,
            "high": close * 1.01,
            "low": close * 0.98,
            "volume": np.full(len(dates), 1_000_000),
            "amount": close * 1_000_000,
            "turnover": np.full(len(dates), 6.0),
        }
    )
    decision = "已启动/错过低位" if limit_up else "待资金数据"
    return Candidate(
        code=code,
        name=name,
        history=history,
        metrics={
            "decision": decision,
            "decision_reason": "测试结论",
            "stage": "已启动" if limit_up else "启动前",
            "final_score": 70.0,
            "technical_score": 75.0,
            "similarity_score": 65.0,
            "close": 11.0 if limit_up else 10.5,
            "as_of": "2026-09-18",
            "history_as_of": "2026-09-17",
            "current_change_pct": 10.0 if limit_up else 2.0,
            "current_status_source": "实时行情",
            "quote_source": "东方财富人气榜",
            "quote_cache_saved_at": "2026-09-18T15:00:00",
            "quote_cache_stale": False,
            "limit_up_today": limit_up,
            "limit_up_count_120": 2,
            "limit_down_count_120": 1,
            "latest_volume_ratio": 2.0 if not limit_up else 1.0,
            "recent_3d_volume_ratio": 1.5 if not limit_up else 1.0,
            "right_edge_volume_ratio": 2.0 if not limit_up else 1.0,
            "right_edge_volume_expanded": not limit_up,
            "volume_as_of": "2026-09-17",
            "gap_date": "2026-09-10" if not limit_up else None,
            "gap_size_pct": 0.02 if not limit_up else None,
            "gap_bars_since": 5 if not limit_up else 0,
            "gap_close_unfilled": not limit_up,
            "gap_intraday_unfilled": not limit_up,
            "gap_close_range": 0.10 if not limit_up else None,
            "gap_post_volume_ratio": 1.8 if not limit_up else None,
            "gap_setup_match": not limit_up,
            "gap_setup_near": not limit_up,
            "gap_condition_count": 5 if not limit_up else 0,
            "gap_condition_total": 5,
            "gap_conditions": {
                "recent_gap_up": not limit_up,
                "gap_close_unfilled": not limit_up,
            },
            "preference_conditions": {
                "right_edge_volume_expanded": not limit_up,
                "recent_gap_up": not limit_up,
                "gap_close_unfilled": not limit_up,
            },
            "primary_structure_match": True,
            "criteria_passed": 7,
            "criteria_total": 11,
            "price_conditions": {"entry_not_late": not limit_up},
            "fund_conditions": {},
            "history_source": "腾讯",
            "history_cache_stale": False,
            "reasons": [],
            "risks": [],
        },
    )


def test_candidate_record_exposes_current_limit_status():
    record = candidate_record(make_candidate("600001", "测试一", limit_up=True))

    assert record["limit_up_today"] is True
    assert record["current_change_pct"] == 10.0
    assert record["current_status_source"] == "实时行情"


def test_report_has_limit_groups_filters_and_sortable_headers():
    candidates = [
        make_candidate("600001", "测试一", limit_up=False),
        make_candidate("600002", "测试二", limit_up=True),
    ]
    now = datetime(2026, 9, 18, 16, 0, 0)
    outcome = ScanOutcome(
        status="partial",
        started_at=now,
        finished_at=now,
        candidates=candidates,
        templates=[],
        issues=[],
        source_summary={
            "selection_mode": "all",
            "candidate_pool": 2,
            "universe_supported_rows": 5360,
            "universe_prefilter_pass": 569,
            "history_success": 2,
            "current_quote_rows": 2,
        },
    )

    page = render_html(outcome, candidates)

    assert "<title>A股全市场形态筛选报告</title>" in page
    assert "状态 <b>部分数据缺失</b>" in page
    assert "全市场 <b>5360</b>" in page
    assert "快照通过 <b>569</b>" in page
    assert "当前状态优先使用全市场实时快照的价格与涨跌幅" in page
    assert "当前状态优先使用人气榜实时价" not in page
    assert "尚未涨停候选 <span>1</span>" in page
    assert "已涨停形态候选 <span>1</span>" in page
    assert 'data-view-group="not-limit"' in page
    assert 'data-view-group="limit-up"' in page
    assert 'id="candidate-table"' in page
    assert 'data-table-filter="not-limit"' in page
    assert 'data-table-filter="right-volume"' in page
    assert 'data-table-filter="gap-setup"' in page
    assert 'data-table-filter="favorites"' in page
    assert 'data-right-volume="true"' in page
    assert 'data-gap-setup="true"' in page
    assert 'data-code="600001"' in page
    assert page.count('class="favorite-button"') == 4
    assert 'ashare-screener:favorites:v1' in page
    assert 'localStorage.setItem(favoriteStorageKey' in page
    assert page.count('class="sort-button"') == 17
    assert "缺口平台" in page
    assert "缺口后量" in page
    assert 'localeCompare(rightRaw, "zh-CN"' in page


def test_report_lists_all_rows_but_limits_expensive_details():
    candidates = [
        make_candidate("600001", "测试一", limit_up=False),
        make_candidate("600002", "测试二", limit_up=True),
    ]
    now = datetime(2026, 9, 18, 16, 0, 0)
    outcome = ScanOutcome(
        status="ok",
        started_at=now,
        finished_at=now,
        candidates=candidates,
        templates=[],
        issues=[],
        source_summary={"candidate_pool": 2, "history_success": 2},
    )

    page = render_html(outcome, candidates, detail_limit=1)

    assert page.count('data-original-rank="') == 2
    assert page.count('<section class="candidate"') == 1
    assert "逐股复核（前 1 只）" in page
