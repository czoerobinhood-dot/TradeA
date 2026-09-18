import pandas as pd
import pytest

from ashare_screener.provider import (
    AkshareProvider,
    is_supported_a_share,
    market_prefix,
    normalize_code,
    normalize_fund_flow,
    normalize_history,
)


class FakeAkshare:
    def stock_zh_a_hist(self, **kwargs):
        raise ConnectionError("primary unavailable")

    def stock_zh_a_hist_tx(self, **kwargs):
        dates = pd.bdate_range("2026-01-02", periods=100)
        return pd.DataFrame(
            {
                "date": dates,
                "open": range(100),
                "close": range(1, 101),
                "high": range(2, 102),
                "low": range(100),
                "volume": [1000] * 100,
                "turnover": [0.02] * 100,
                "amount": [10000] * 100,
            }
        )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("SZ000001", "000001"), ("SH600000", "600000"), ("920001", "920001")],
)
def test_normalize_code(raw, expected):
    assert normalize_code(raw) == expected


def test_market_prefix():
    assert market_prefix("600000") == "sh"
    assert market_prefix("000001") == "sz"
    assert market_prefix("920001") == "bj"


def test_supported_a_share_includes_302_chinext_codes():
    assert is_supported_a_share("302132") is True


def test_normalize_history_accepts_akshare_columns():
    raw = pd.DataFrame(
        {
            "日期": ["2026-01-02"],
            "开盘": [10],
            "收盘": [10.2],
            "最高": [10.4],
            "最低": [9.9],
            "成交量": [1000],
            "成交额": [10000],
            "换手率": [2.3],
        }
    )
    result = normalize_history(raw)
    assert list(result.columns) == [
        "date",
        "open",
        "close",
        "high",
        "low",
        "volume",
        "amount",
        "turnover",
    ]
    assert result.loc[0, "close"] == pytest.approx(10.2)


def test_normalize_fund_flow_uses_net_ratio_columns():
    raw = pd.DataFrame(
        {
            "日期": ["2026-01-02"],
            "超大单净流入-净占比": [3.1],
            "大单净流入-净占比": [-1.2],
            "中单净流入-净占比": [-0.8],
            "小单净流入-净占比": [-1.1],
        }
    )
    result = normalize_fund_flow(raw)
    assert result.loc[0, "super_large_pct"] == pytest.approx(3.1)
    assert result.loc[0, "small_pct"] == pytest.approx(-1.1)


def test_normalize_all_market_tencent_fields():
    raw = pd.DataFrame(
        {
            "code": ["sh600000", "sz300001"],
            "name": ["浦发银行", "特锐德"],
            "zxj": [10.5, 20.0],
            "zdf": [1.2, 19.8],
            "hsl": [5.5, 12.0],
            "lb": [1.3, 2.1],
            "zdf_d5": [-1.0, 18.0],
            "zdf_d20": [-5.0, 30.0],
            "zdf_d60": [-10.0, 40.0],
            "zdf_w52": [-20.0, 50.0],
        }
    )
    raw.attrs["cache_saved_at"] = "2026-09-18T15:00:00"
    provider = object.__new__(AkshareProvider)

    result = provider._normalize_all_stocks_tencent(raw)

    assert result["code"].tolist() == ["600000", "300001"]
    assert result.loc[0, "turnover"] == pytest.approx(5.5)
    assert result.loc[1, "return_60d"] == pytest.approx(40.0)
    assert result.loc[0, "source"] == "腾讯全A股实时行情"
    assert result.attrs["cache_saved_at"] == "2026-09-18T15:00:00"


def test_history_falls_back_to_tencent(tmp_path, monkeypatch):
    from ashare_screener.provider import AkshareProvider

    provider = object.__new__(AkshareProvider)
    provider.ak = FakeAkshare()
    from ashare_screener.provider import CsvCache

    provider.cache = CsvCache(tmp_path, ttl_minutes=30)
    provider.refresh = False
    provider.as_of = pd.Timestamp("2026-09-17").date()
    import threading

    provider._source_lock = threading.Lock()
    provider._history_primary_available = None
    provider._history_primary_error = None
    provider._fund_flow_available = None
    provider._fund_flow_error = None
    provider._fund_flow_failures = 0
    result = provider.history("600127", history_days=90)
    assert len(result) == 90
    assert result.attrs["source"] == "腾讯（东方财富失败后回退）"
    assert result["turnover"].iloc[-1] == pytest.approx(2.0)


def test_hot_proxy_is_explicitly_labeled(tmp_path):
    from ashare_screener.provider import AkshareProvider, CsvCache

    class FakeHotAkshare:
        def stock_hot_rank_em(self):
            raise ConnectionError("hot rank unavailable")

        def stock_zh_a_spot_tx(self):
            return pd.DataFrame(
                {
                    "code": ["sh600127", "sz000001"],
                    "name": ["金健米业", "平安银行"],
                    "zxj": [13.02, 10.0],
                    "zdf": [9.97, 2.0],
                    "hsl": [29.7, 3.0],
                    "lb": [2.5, 1.1],
                }
            )

    provider = object.__new__(AkshareProvider)
    provider.ak = FakeHotAkshare()
    provider.cache = CsvCache(tmp_path, ttl_minutes=30)
    provider.refresh = False
    result = provider.hot_stocks()
    assert result.iloc[0]["source"].startswith("热点代理")
    assert result.iloc[0]["code"] == "600127"


def test_history_primary_failure_is_circuit_broken(tmp_path):
    from ashare_screener.provider import AkshareProvider, CsvCache

    class FlakyAkshare(FakeAkshare):
        primary_calls = 0

        def stock_zh_a_hist(self, **kwargs):
            self.primary_calls += 1
            if self.primary_calls == 1:
                return super().stock_zh_a_hist(**kwargs)
            raise AssertionError("primary should be disabled after first failure")

    provider = object.__new__(AkshareProvider)
    provider.ak = FlakyAkshare()
    provider.cache = CsvCache(tmp_path, ttl_minutes=30)
    provider.refresh = False
    provider.as_of = pd.Timestamp("2026-09-17").date()
    import threading

    provider._source_lock = threading.Lock()
    provider._history_primary_available = None
    provider._history_primary_error = None
    provider._fund_flow_available = None
    provider._fund_flow_error = None
    provider._fund_flow_failures = 0
    provider.history("600127", history_days=90)
    provider.history("600354", history_days=90)
    assert provider.ak.primary_calls == 1


def test_cache_can_fall_back_to_last_success(tmp_path):
    from ashare_screener.provider import CsvCache

    cache = CsvCache(tmp_path, ttl_minutes=30)
    expected = pd.DataFrame({"value": [1, 2, 3]})
    first = cache.get_or_load("sample", lambda: expected)
    assert first.attrs["cache_stale"] is False

    def fail():
        raise ConnectionError("upstream unavailable")

    fallback = cache.get_or_load(
        "sample", fail, refresh=True, allow_stale_on_error=True
    )
    assert fallback["value"].tolist() == [1, 2, 3]
    assert fallback.attrs["cache_stale"] is True
