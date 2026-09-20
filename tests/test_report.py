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
    fund_snapshot = pd.DataFrame(
        {
            "date": pd.to_datetime(["2026-09-18"]),
            "super_large_pct": [11.87],
            "large_pct": [-0.27],
            "medium_pct": [-11.69],
            "small_pct": [0.10],
        }
    )
    return Candidate(
        code=code,
        name=name,
        history=history,
        fund_snapshot=fund_snapshot,
        metrics={
            "decision": decision,
            "decision_reason": "测试结论",
            "stage": "已启动" if limit_up else "启动前",
            "final_score": 70.0,
            "technical_score": 75.0,
            "similarity_score": 65.0,
            "similar_reference": "海峡创新",
            "similar_reference_timeframe": "daily",
            "close": 11.0 if limit_up else 10.5,
            "as_of": "2026-09-18",
            "history_as_of": "2026-09-17",
            "current_change_pct": 10.0 if limit_up else 2.0,
            "return_1d": 0.10 if limit_up else 0.02,
            "return_5d": 0.18 if limit_up else 0.06,
            "return_20d": 0.25 if limit_up else 0.10,
            "extension_ma20": 0.15 if limit_up else 0.05,
            "current_status_source": "实时行情",
            "quote_source": "东方财富人气榜",
            "quote_cache_saved_at": "2026-09-18T15:00:00",
            "quote_cache_stale": False,
            "fund_data_status": "当日快照累计 1/8 天，历史评分待确认",
            "fund_snapshot_days": 1,
            "fund_snapshot_as_of": "2026-09-18",
            "fund_snapshot_source": "东方财富当日四档资金快照累计",
            "fund_snapshot_cache_stale": False,
            "fund_snapshot_compatible": True,
            "sector_name": "基础化工",
            "sector_source": "搜狐证券个股页行业分类",
            "sector_board_name": "化工行业",
            "sector_board_source": "新浪财经行业板块行情（AkShare）",
            "sector_board_rank": 3,
            "sector_board_count": 50,
            "sector_board_change_pct": 2.5,
            "sector_board_hot": True,
            "sector_member_rank": 2,
            "sector_member_count": 80,
            "sector_member_change_pct": 4.2,
            "sector_hot_stock": True,
            "sector_enrichment_status": "complete",
            "sector_hot_reason": "行业板块当日涨幅第 3/50；个股板块内涨幅第 2/80",
            "limit_up_today": limit_up,
            "limit_up_count_120": 2,
            "limit_down_count_120": 1,
            "latest_volume_ratio": 2.0 if not limit_up else 1.0,
            "recent_3d_volume_ratio": 1.5 if not limit_up else 1.0,
            "right_edge_volume_ratio": 2.0 if not limit_up else 1.0,
            "right_edge_volume_expanded": not limit_up,
            "bottom_volume_confirmed": not limit_up,
            "bottom_red_volume_share": 0.72 if not limit_up else 0.45,
            "bottom_red_high_volume_days": 3 if not limit_up else 1,
            "bottom_red_volume_confirmed": not limit_up,
            "early_bottom_match": not limit_up,
            "rise_pressure": 1.8 if limit_up else 0.67,
            "volume_as_of": "2026-09-17",
            "gap_date": "2026-09-10" if not limit_up else None,
            "gap_floor": 10.20 if not limit_up else None,
            "gap_ceiling": 10.50 if not limit_up else None,
            "gap_size_pct": 0.02 if not limit_up else None,
            "gap_bars_since": 5 if not limit_up else 0,
            "gap_close_unfilled": not limit_up,
            "gap_intraday_unfilled": not limit_up,
            "gap_hold_confirmed": not limit_up,
            "gap_close_range": 0.10 if not limit_up else None,
            "gap_post_volume_ratio": 1.8 if not limit_up else None,
            "gap_post_volume_active_fraction": 0.75 if not limit_up else None,
            "gap_preference_score": 15.0 if not limit_up else 0.0,
            "gap_setup_match": not limit_up,
            "gap_setup_near": not limit_up,
            "gap_condition_count": 5 if not limit_up else 0,
            "gap_condition_total": 5,
            "gap_conditions": {
                "recent_gap_up": not limit_up,
                "gap_close_unfilled": not limit_up,
                "gap_sideways_holding": not limit_up,
                "gap_uptrend": not limit_up,
                "gap_sustained_volume": not limit_up,
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
    assert record["similar_reference"] == "海峡创新"
    assert record["similar_reference_timeframe"] == "daily"
    assert record["sector_name"] == "基础化工"
    assert record["sector_board_hot"] is True
    assert record["sector_hot_stock"] is True
    assert record["fund_snapshot_days"] == 1
    assert record["fund_snapshot_as_of"] == "2026-09-18"
    assert record["sohu_url"] == "https://q.stock.sohu.com/cn/600001/index.shtml"
    assert record["eastmoney_url"] == "https://quote.eastmoney.com/sh600001.html"


def test_candidate_record_links_cover_shenzhen_and_beijing_markets():
    shenzhen = candidate_record(make_candidate("002442", "龙星科技", limit_up=False))
    beijing = candidate_record(make_candidate("920895", "花溪科技", limit_up=False))

    assert shenzhen["sohu_url"] == "https://q.stock.sohu.com/cn/002442/index.shtml"
    assert shenzhen["eastmoney_url"] == "https://quote.eastmoney.com/sz002442.html"
    assert beijing["sohu_url"] == "https://q.stock.sohu.com/cn/920895/index.shtml"
    assert beijing["eastmoney_url"] == "https://quote.eastmoney.com/bj/920895.html"


def test_report_has_limit_groups_filters_and_sortable_headers():
    candidates = [
        make_candidate("600001", "测试一", limit_up=False),
        make_candidate("600002", "测试二", limit_up=True),
    ]
    candidates[1].metrics["gap_setup_near"] = True
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
    assert "日K低位红量候选 <span>1</span>" in page
    assert "已涨停形态候选 <span>1</span>" in page
    assert 'data-view-group="not-limit"' in page
    assert 'data-view-group="limit-up"' in page
    assert 'id="candidate-table"' in page
    assert 'data-table-filter="not-limit"' in page
    assert 'data-table-filter="right-volume"' in page
    assert 'data-table-filter="gap-setup"' in page
    assert (
        'data-table-filter="gap-setup" data-gap-review-target="gap-review" '
        'aria-controls="candidate-table gap-review" aria-pressed="false">'
        '缺口趋势 2</button>'
    ) in page
    assert 'data-table-filter="favorites"' in page
    assert 'data-right-volume="true"' in page
    assert 'data-gap-setup="true"' in page
    assert page.count('data-gap-setup="true"') == 2
    assert 'activeFilter === "gap-setup"\n      && row.dataset.gapSetup === "true"' in page
    assert (
        'activeFilter === "gap-setup"\n      && row.dataset.viewGroup === "not-limit"'
        not in page
    )
    assert 'data-code="600001"' in page
    assert 'href="https://q.stock.sohu.com/cn/600001/index.shtml"' in page
    assert 'href="https://quote.eastmoney.com/sh600001.html"' in page
    assert 'class="status-stock-link"' in page
    assert 'title="打开搜狐完整行情与K线"' in page
    assert "东财K线/F10" in page
    assert "相似 65.0（日K）" in page
    assert "相似样本 海峡创新（日K）" in page
    assert "红量 72%" in page
    assert "日K底部红量" in page
    assert "板块 / 热度" in page
    assert "基础化工" in page
    assert "热门板块" in page
    assert "板块热门股" in page
    assert "板块热度依据" in page
    assert "当日四档资金快照（东方财富）" in page
    assert "本地已累计 1/8 个交易日" in page
    assert 'aria-label="2026-09-18 当日四档资金快照"' in page
    assert "所属板块（搜狐）" in page
    assert "热度对应板块（新浪）" in page
    assert "新浪 化工行业" in page
    assert "搜狐证券个股页行业分类" in page
    assert "新浪行业行情" in page
    assert 'data-copy-code="600001"' in page
    assert "navigator.clipboard.writeText(code)" in page
    assert "当前选股数据来自 AkShare" in page
    assert "不是华泰证券数据" in page
    assert "<th>查看</th>" in page
    assert '<template id="candidate-row-template">' in page
    assert '<tbody></tbody></table>' in page
    assert 'id="previous-page"' in page
    assert 'id="next-page"' in page
    assert 'id="page-size"' in page
    assert 'tbody.replaceChildren(...pageRows)' in page
    assert page.count('class="favorite-button"') == 6
    assert 'ashare-screener:favorites:v1' in page
    assert 'localStorage.setItem(favoriteStorageKey' in page
    assert 'const scrollToStockWithoutHash = (hash, behavior = "smooth")' in page
    assert 'window.history.replaceState(' in page
    assert 'window.location.pathname}${window.location.search}' in page
    assert 'a[href^="#stock-"]' in page
    assert 'favoriteFilterButton.title = "当前浏览器的本机收藏"' in page
    assert page.count('class="sort-button"') == 18
    assert "缺口平台" in page
    assert "缺口后量" in page
    assert "缺口趋势详细复核（2 只）" in page
    assert '<details class="gap-review" id="gap-review">' in page
    assert "点击展开" in page
    assert page.count('class="gap-index-item"') == 2
    assert 'class="gap-index-item" href="#gap-stock-600001"' in page
    assert 'class="gap-detail-link" href="#gap-stock-600001"' in page
    assert 'href="#stock-600001" title="查看通用逐股复核">通用复核</a>' in page
    assert "缺口趋势专属复核" in page
    assert "缺口价格区间 <b>10.20 - 10.50</b>" in page
    assert "活跃放量占比 <b>75%</b>" in page
    assert "gapReview.open = true;" in page
    filter_handler = page.split("filterButtons.forEach", 1)[1].split(
        "previousPageButton.addEventListener", 1
    )[0]
    assert 'if (activeFilter === "gap-setup") showGapReview();' in filter_handler
    assert "grid-template-columns:repeat(2,minmax(0,1fr))" in page
    assert ".segmented button,.segmented button:nth-last-child(-n+2)" in page
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
    assert page.count('<section class="candidate') == 2
    assert page.count('class="candidate gap-candidate"') == 1
    assert page.count('id="stock-600001"') == 1
    assert page.count('id="gap-stock-600001"') == 1
    assert 'id="stock-600002"' not in page
    assert "缺口趋势详细复核（1 只）" in page
    assert "通用逐股复核（前 1 只）" in page
    assert page.index('<details class="gap-review" id="gap-review">') < page.index(
        "通用逐股复核（前 1 只）"
    )
    assert '<details class="gap-review" id="gap-review" open>' not in page
    assert 'target.closest("details")' in page


def test_gap_index_lists_every_candidate_when_detail_cards_are_limited():
    candidates = [
        make_candidate("600001", "测试一", limit_up=False),
        make_candidate("600002", "测试二", limit_up=False),
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

    assert "缺口趋势详细复核（展示 1/2 只）" in page
    assert page.count('class="gap-index-item"') == 2
    assert 'class="gap-index-item" href="#gap-stock-600001"' in page
    assert (
        'class="gap-index-item" href="https://q.stock.sohu.com/cn/600002/index.shtml" '
        'target="_blank" rel="noopener noreferrer"'
    ) in page
    assert 'id="gap-stock-600002"' not in page


def test_report_labels_ths_snapshot_as_independent_review_data():
    candidate = make_candidate("600001", "测试一", limit_up=False)
    candidate.metrics.update(
        {
            "fund_data_status": "同花顺当日四档快照（独立口径，未参与历史评分）",
            "fund_snapshot_source": "同花顺当日四档资金快照累计（独立订单分档口径）",
            "fund_snapshot_compatible": False,
        }
    )
    now = datetime(2026, 9, 18, 16, 0, 0)
    outcome = ScanOutcome(
        status="partial",
        started_at=now,
        finished_at=now,
        candidates=[candidate],
        templates=[],
        issues=[],
        source_summary={"candidate_pool": 1, "history_success": 1},
    )

    page = render_html(outcome, [candidate], detail_limit=1)

    assert "当日四档资金快照（同花顺）" in page
    assert "该快照只用于当日复核" in page
    assert "不参与历史粘连、交叉或红线位置评分" in page
