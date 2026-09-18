from datetime import date
from types import SimpleNamespace

import pandas as pd

from ashare_screener.config import ScreenConfig
from ashare_screener.models import Candidate
from ashare_screener.pipeline import Screener


def test_quote_is_only_applied_to_its_scan_date():
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
