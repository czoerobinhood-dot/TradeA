from datetime import date
from types import SimpleNamespace

import pandas as pd
import pytest

from ashare_screener.config import ScreenConfig
from ashare_screener.models import Candidate
from ashare_screener.pipeline import Screener


def test_quote_is_only_applied_to_its_scan_date_on_a_trading_weekday():
    screener = object.__new__(Screener)
    screener.provider = SimpleNamespace(as_of=date(2026, 9, 17))
    candidate = Candidate(
        code="600000",
        name="测试",
        metrics={"quote_cache_saved_at": "2026-09-18T15:39:09"},
    )

    assert screener._quote_date_for_scan(candidate) is None

    screener.provider.as_of = date(2026, 9, 18)
    assert screener._quote_date_for_scan(candidate) == "2026-09-18T15:39:09"


def test_quote_is_not_applied_as_a_weekend_bar():
    screener = object.__new__(Screener)
    screener.provider = SimpleNamespace(as_of=date(2026, 9, 19))
    candidate = Candidate(
        code="600000",
        name="测试",
        metrics={
            "quote_cache_saved_at": "2026-09-19T09:30:00",
            "quote_latest": 10.5,
            "quote_change_pct": 1.0,
        },
    )

    assert screener._quote_date_for_scan(candidate) is None


def test_quote_equal_to_last_history_bar_is_treated_as_stale_snapshot():
    screener = object.__new__(Screener)
    screener.provider = SimpleNamespace(as_of=date(2026, 9, 21))
    candidate = Candidate(
        code="600000",
        name="测试",
        history=pd.DataFrame(
            {
                "date": pd.to_datetime(["2026-09-17", "2026-09-18"]),
                "close": [10.0, 10.5],
            }
        ),
        metrics={
            "quote_cache_saved_at": "2026-09-21T09:00:00",
            "quote_latest": 10.5,
            "quote_change_pct": 5.0,
        },
    )

    assert screener._quote_date_for_scan(candidate) is None


def test_changed_quote_is_applied_on_a_trading_weekday():
    screener = object.__new__(Screener)
    screener.provider = SimpleNamespace(as_of=date(2026, 9, 21))
    candidate = Candidate(
        code="600000",
        name="测试",
        history=pd.DataFrame(
            {
                "date": pd.to_datetime(["2026-09-17", "2026-09-18"]),
                "close": [10.0, 10.5],
            }
        ),
        metrics={
            "quote_cache_saved_at": "2026-09-21T10:00:00",
            "quote_latest": 10.71,
            "quote_change_pct": 2.0,
        },
    )

    assert screener._quote_date_for_scan(candidate) == "2026-09-21T10:00:00"


def test_all_market_collection_checks_full_snapshot_without_top_n_cutoff():
    market = pd.DataFrame(
        {
            "code": ["600000", "600001", "300001", "600002"],
            "name": ["普通通过", "量比不足", "涨停通过", "近期过热"],
            "latest": [10.0, 11.0, 20.0, 12.0],
            "change_pct": [1.0, 2.0, 19.8, 3.0],
            "turnover": [8.0, 8.0, 30.0, 8.0],
            "volume_ratio": [1.2, 0.5, 2.0, 1.5],
            "return_5d": [2.0, 2.0, 30.0, 5.0],
            "return_20d": [5.0, 5.0, 35.0, 10.0],
            "return_60d": [10.0, 10.0, 50.0, 30.0],
            "return_52w": [0.0, 0.0, 0.0, 0.0],
            "source": ["腾讯全A股实时行情"] * 4,
        }
    )
    market.attrs.update(
        cache_stale=False,
        cache_saved_at="2026-09-18T15:00:00",
    )
    provider = SimpleNamespace(
        as_of=date(2026, 9, 18),
        all_stocks=lambda: market,
    )
    config = ScreenConfig(universe_mode="all", calibration_stocks=[])
    screener = Screener(
        provider,
        config,
        use_concepts=False,
        use_fund_flow=False,
    )

    candidates = screener._collect_candidates()

    assert [item.code for item in candidates] == ["600000", "300001"]
    assert screener.source_summary["universe_rows"] == 4
    assert screener.source_summary["universe_supported_rows"] == 4
    assert screener.source_summary["universe_prefilter_pass"] == 2
    assert screener.source_summary["candidate_pool"] == 2
    assert all(item.hot_rank is None for item in candidates)


def test_config_rejects_unknown_universe_mode():
    config = ScreenConfig(universe_mode="everything")

    try:
        config.validate()
    except ValueError as exc:
        assert "universe_mode" in str(exc)
    else:
        raise AssertionError("invalid universe mode should fail validation")


def test_sector_enrichment_marks_hot_board_and_hot_member():
    boards = pd.DataFrame(
        {
            "sector_key": ["new_hghy", "new_mthy"],
            "sector_name": ["化工行业", "煤炭行业"],
            "change_pct": [3.5, 1.0],
            "rank": [1, 2],
            "source": ["新浪财经行业板块行情（AkShare）"] * 2,
        }
    )
    members = pd.DataFrame(
        {
            "code": ["600001", "600002"],
            "name": ["测试一", "测试二"],
            "change_pct": [4.2, 1.5],
            "rank": [1, 2],
            "source": ["新浪财经行业成分股行情（AkShare）"] * 2,
        }
    )
    provider = SimpleNamespace(
        stock_sector=lambda code: {
            "sector_name": "基础化工",
            "sector_source": "搜狐证券个股页行业分类",
            "sector_source_url": f"https://q.stock.sohu.com/cn/{code}/index.shtml",
        },
        industry_sectors=lambda: boards,
        industry_members=lambda sector_key: members,
    )
    config = ScreenConfig(history_workers=2, fund_workers=2)
    screener = Screener(provider, config)
    candidate = Candidate(
        code="600001",
        name="测试一",
        metrics={"decision": "严格匹配", "gap_setup_near": False},
    )

    screener._enrich_sector_context([candidate])

    assert candidate.metrics["sector_name"] == "基础化工"
    assert candidate.metrics["sector_board_name"] == "化工行业"
    assert candidate.metrics["sector_board_hot"] is True
    assert candidate.metrics["sector_member_rank"] == 1
    assert candidate.metrics["sector_hot_stock"] is True
    assert candidate.metrics["sector_enrichment_status"] == "complete"
    assert screener.source_summary["sector_member_success"] == 1


def test_sector_enrichment_skips_non_recommendations():
    calls = []
    provider = SimpleNamespace(stock_sector=lambda code: calls.append(code))
    screener = Screener(provider, ScreenConfig())
    candidate = Candidate(
        code="600001",
        name="测试一",
        metrics={"decision": "不符合", "gap_setup_near": False},
    )

    screener._enrich_sector_context([candidate])

    assert calls == []
    assert screener.source_summary["sector_target_count"] == 0


def test_fund_snapshot_amounts_convert_to_historical_ratio_contract():
    history = pd.DataFrame(
        {
            "date": pd.to_datetime(["2026-09-18"]),
            "amount": [366_758_900.0],
        }
    )
    snapshot = pd.DataFrame(
        {
            "date": pd.to_datetime(["2026-09-18"]),
            "main_net_amount": [42_519_700.0],
            "small_net_amount": [371_639.0],
            "medium_net_amount": [-42_891_339.0],
            "large_net_amount": [-1_007_783.0],
            "super_large_net_amount": [43_527_483.0],
        }
    )
    snapshot.attrs["source"] = "东方财富当日四档资金快照累计"

    result = Screener._fund_snapshot_ratios(snapshot, history)

    assert result is not None
    assert result.iloc[0]["super_large_pct"] == pytest.approx(11.87, abs=0.01)
    assert result.iloc[0]["large_pct"] == pytest.approx(-0.27, abs=0.01)
    assert result.iloc[0]["medium_pct"] == pytest.approx(-11.69, abs=0.01)
    assert result.iloc[0]["small_pct"] == pytest.approx(0.10, abs=0.01)
    assert result.attrs["source"] == "东方财富当日四档资金快照累计"


def test_visible_detail_without_history_gets_independent_ths_snapshot():
    raw = pd.DataFrame(
        {
            "date": pd.to_datetime(["2026-09-18"]),
            "main_net_amount": [20_811_183.0],
            "small_net_amount": [-12_759_337.0],
            "medium_net_amount": [-8_051_846.0],
            "large_net_amount": [-8_388_112.0],
            "super_large_net_amount": [29_199_295.0],
        }
    )
    raw.attrs.update(
        source="同花顺当日四档资金快照累计（独立订单分档口径）",
        compatible_with_history=False,
        cache_stale=False,
    )
    provider = SimpleNamespace(
        fund_flow_snapshot_ths=lambda code, snapshot_date: raw,
    )
    screener = Screener(provider, ScreenConfig(report_limit=1, fund_workers=1))
    candidate = Candidate(
        code="600630",
        name="龙头股份",
        history=pd.DataFrame(
            {
                "date": pd.to_datetime(["2026-09-18"]),
                "amount": [378_732_100.0],
            }
        ),
        metrics={"gap_setup_near": False},
    )

    screener._load_missing_detail_snapshots([candidate])

    assert candidate.fund_snapshot is not None
    assert candidate.metrics["fund_snapshot_days"] == 1
    assert candidate.metrics["fund_snapshot_compatible"] is False
    assert "同花顺当日四档快照" in candidate.metrics["fund_data_status"]
    assert screener.source_summary["fund_detail_snapshot_success"] == 1


def test_visible_detail_snapshot_targets_deduplicate_general_and_gap_cards():
    raw = pd.DataFrame(
        {
            "date": pd.to_datetime(["2026-09-18"]),
            "main_net_amount": [20_811_183.0],
            "small_net_amount": [-12_759_337.0],
            "medium_net_amount": [-8_051_846.0],
            "large_net_amount": [-8_388_112.0],
            "super_large_net_amount": [29_199_295.0],
        }
    )
    raw.attrs.update(
        source="同花顺当日四档资金快照累计（独立订单分档口径）",
        compatible_with_history=False,
        cache_stale=False,
    )
    calls = []

    def load_snapshot(code, snapshot_date):
        calls.append((code, snapshot_date))
        return raw

    provider = SimpleNamespace(fund_flow_snapshot_ths=load_snapshot)
    screener = Screener(provider, ScreenConfig(report_limit=1, fund_workers=1))
    candidate = Candidate(
        code="600630",
        name="龙头股份",
        history=pd.DataFrame(
            {
                "date": pd.to_datetime(["2026-09-18"]),
                "amount": [378_732_100.0],
            }
        ),
        metrics={"gap_setup_near": True},
    )

    screener._load_missing_detail_snapshots([candidate])

    assert calls == [("600630", date(2026, 9, 18))]
    assert screener.source_summary["fund_detail_snapshot_attempted"] == 1
