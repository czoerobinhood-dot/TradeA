import numpy as np
import pandas as pd

from ashare_screener.features import (
    best_shape_similarity,
    calculate_fund_metrics,
    calculate_price_metrics,
    current_reference_template,
    daily_limit_up_price,
    extract_reference_template,
)


def make_history(*, overheated: bool = False) -> pd.DataFrame:
    dates = pd.bdate_range("2026-01-02", periods=140)
    decline = np.linspace(15.0, 10.0, 50)
    base = 10.0 + np.sin(np.linspace(0, 5 * np.pi, 60)) * 0.18
    turn = np.linspace(10.0, 10.45, 29)
    close = np.concatenate([decline, base, turn, [10.78]])
    if overheated:
        close[-20:] = np.linspace(10.2, 17.5, 20)
    volume = np.full(140, 1_000_000.0)
    volume[-1] = 1_800_000.0
    return pd.DataFrame(
        {
            "date": dates,
            "open": close * 0.995,
            "close": close,
            "high": close * 1.012,
            "low": close * 0.988,
            "volume": volume,
            "amount": volume * close,
            "turnover": np.full(140, 5.0),
        }
    )


def test_price_pattern_identifies_early_breakout():
    metrics = calculate_price_metrics(make_history())
    assert metrics["stage"] == "接近"
    assert metrics["price_conditions"]["drawdown_around_half"] is True
    assert metrics["price_conditions"]["entry_not_late"] is True
    assert metrics["max_drawdown_250"] <= -0.30


def test_price_pattern_penalizes_overheated_move():
    metrics = calculate_price_metrics(make_history(overheated=True))
    assert metrics["stage"] == "已启动"
    assert metrics["entry_late"] is True
    assert any("股价已经明显上涨" in item for item in metrics["technical_risks"])


def test_daily_limit_price_uses_half_up_exchange_rounding():
    assert daily_limit_up_price(10.05, "600000") == 11.06
    assert daily_limit_up_price(10.05, "300001") == 12.06
    assert daily_limit_up_price(10.05, "302132") == 12.06


def test_realtime_quote_overlays_stale_history_and_marks_limit_up():
    history = make_history()
    history_day = history.iloc[-1]["date"]
    quote_day = history_day + pd.offsets.BDay(1)
    limit_price = daily_limit_up_price(history.iloc[-1]["close"], "600000")

    metrics = calculate_price_metrics(
        history,
        code="600000",
        quote_latest=limit_price,
        quote_change_pct=10.0,
        quote_date=quote_day,
    )

    assert metrics["history_as_of"] == history_day.date().isoformat()
    assert metrics["as_of"] == quote_day.date().isoformat()
    assert metrics["close"] == limit_price
    assert metrics["current_change_pct"] == 10.0
    assert metrics["current_status_source"] == "实时行情"
    assert metrics["limit_up_today"] is True
    assert metrics["entry_late"] is True


def test_near_limit_move_is_not_treated_as_closed_limit_up():
    history = make_history()
    previous_close = history.iloc[-2]["close"]
    latest = round(previous_close * 1.093, 2)
    history.loc[history.index[-1], ["open", "close", "high", "low"]] = latest

    metrics = calculate_price_metrics(history, code="600000")

    assert metrics["return_1d"] > 0.09
    assert metrics["limit_up_today"] is False
    assert metrics["entry_late"] is True
    assert any("单日上涨" in item for item in metrics["entry_late_reasons"])


def test_early_bottom_match_requires_confirmed_volume_and_calm_price():
    history = make_history()
    history.loc[history.index[-6:], "volume"] = 1_800_000.0

    metrics = calculate_price_metrics(history, code="600000")

    assert metrics["bottom_volume_confirmed"] is True
    assert metrics["bottom_red_volume_share"] == 1.0
    assert metrics["bottom_red_high_volume_days"] >= 2
    assert metrics["bottom_red_volume_confirmed"] is True
    assert metrics["entry_late"] is False
    assert metrics["early_bottom_match"] is True
    assert metrics["rise_pressure"] <= 1.0


def test_green_bottom_volume_does_not_confirm_buying_proxy():
    history = make_history()
    bottom_indexes = history.index[-6:]
    history.loc[bottom_indexes, "volume"] = 1_800_000.0
    history.loc[bottom_indexes, "open"] = history.loc[bottom_indexes, "close"] * 1.02
    history.loc[bottom_indexes, "high"] = history.loc[bottom_indexes, "open"] * 1.01

    metrics = calculate_price_metrics(history, code="600000")

    assert metrics["bottom_volume_confirmed"] is True
    assert metrics["bottom_red_volume_share"] < 0.60
    assert metrics["bottom_red_volume_confirmed"] is False
    assert metrics["early_bottom_match"] is False


def test_right_edge_volume_prefers_latest_or_recent_expansion():
    history = make_history()

    metrics = calculate_price_metrics(history, code="600000")

    assert np.isclose(metrics["latest_volume_ratio"], 1.8)
    assert np.isclose(metrics["recent_3d_volume_ratio"], 1.2666666666666666)
    assert np.isclose(metrics["right_edge_volume_ratio"], 1.8)
    assert metrics["right_edge_volume_expanded"] is True
    assert metrics["preference_conditions"]["right_edge_volume_expanded"] is True


def test_right_edge_volume_rejects_quiet_latest_bars():
    history = make_history()
    history.loc[history.index[-3:], "volume"] = 700_000.0

    metrics = calculate_price_metrics(history, code="600000")

    assert np.isclose(metrics["right_edge_volume_ratio"], 0.7)
    assert metrics["right_edge_volume_expanded"] is False
    assert any("右侧量能不足" in item for item in metrics["technical_risks"])


def make_gap_history(*, fill_gap: bool = False) -> pd.DataFrame:
    history = make_history()
    gap_index = history.index[-7]
    previous_index = history.index[-8]
    history.loc[history.index[-25:], "volume"] = 1_000_000.0
    history.loc[previous_index, ["open", "close", "high", "low"]] = [
        10.05,
        10.10,
        10.20,
        10.00,
    ]
    post_closes = [10.80, 10.88, 10.84, 10.96, 11.02, 11.08, 11.12]
    for offset, index in enumerate(history.index[-7:]):
        close = post_closes[offset]
        history.loc[index, ["open", "close", "high", "low", "volume"]] = [
            close - 0.08,
            close,
            close + 0.12,
            close - 0.18,
            2_000_000.0,
        ]
    history.loc[gap_index, "low"] = 10.50
    if fill_gap:
        history.loc[history.index[-1], ["open", "close", "high", "low"]] = [
            10.10,
            10.00,
            10.20,
            9.90,
        ]
    return history


def test_gap_setup_detects_unfilled_sideways_uptrend_with_sustained_volume():
    metrics = calculate_price_metrics(make_gap_history(), code="600000")

    assert metrics["gap_size_pct"] >= 0.012
    assert metrics["gap_bars_since"] == 6
    assert metrics["gap_close_unfilled"] is True
    assert metrics["gap_intraday_unfilled"] is True
    assert metrics["gap_conditions"]["gap_sideways_holding"] is True
    assert metrics["gap_conditions"]["gap_uptrend"] is True
    assert metrics["gap_conditions"]["gap_sustained_volume"] is True
    assert metrics["gap_setup_match"] is True


def test_gap_setup_rejects_a_filled_gap():
    metrics = calculate_price_metrics(make_gap_history(fill_gap=True), code="600000")

    assert metrics["gap_close_unfilled"] is False
    assert metrics["gap_setup_match"] is False


def test_recovered_price_fails_current_bottom_gate():
    metrics = calculate_price_metrics(make_history(overheated=True), code="600000")
    assert metrics["price_conditions"]["drawdown_around_half"] is True
    assert metrics["primary_structure_match"] is False
    assert metrics["price_conditions"]["decline_into_current_base"] is False
    assert metrics["technical_score"] <= 49


def test_fund_proxy_detects_super_large_cross():
    dates = pd.bdate_range("2026-04-01", periods=30)
    super_large = np.concatenate([np.full(23, -0.2), np.linspace(-0.1, 4.0, 7)])
    large = np.concatenate([np.full(23, 0.1), np.linspace(0.0, -1.5, 7)])
    medium = np.concatenate([np.full(23, 0.05), np.linspace(0.0, -1.0, 7)])
    small = -(super_large + large + medium)
    frame = pd.DataFrame(
        {
            "date": dates,
            "super_large_pct": super_large,
            "large_pct": large,
            "medium_pct": medium,
            "small_pct": small,
        }
    )
    metrics = calculate_fund_metrics(frame)
    assert metrics["fund_super_on_top"] is True
    assert metrics["fund_super_rising"] is True
    assert metrics["fund_shape_match"] is True
    assert metrics["fund_score"] >= 50


def test_fund_proxy_rejects_chaotic_crossings():
    dates = pd.bdate_range("2026-04-01", periods=30)
    alternating = np.where(np.arange(30) % 2 == 0, 3.0, -3.0)
    frame = pd.DataFrame(
        {
            "date": dates,
            "super_large_pct": alternating,
            "large_pct": -alternating,
            "medium_pct": np.roll(alternating, 1),
            "small_pct": -np.roll(alternating, 1),
        }
    )
    metrics = calculate_fund_metrics(frame)
    assert metrics["fund_cross_count_12d"] > 12
    assert metrics["fund_shape_match"] is False
    assert any("过于凌乱" in item for item in metrics["fund_risks"])


def test_reference_template_and_similarity():
    history = make_history()
    surge = history.copy()
    surge.loc[120:, "close"] = np.linspace(surge.loc[120, "close"], 15.0, 20)
    surge["open"] = surge["close"] * 0.995
    surge["high"] = surge["close"] * 1.012
    surge["low"] = surge["close"] * 0.988
    template = extract_reference_template(surge, code="600127", name="样本")
    assert template is not None
    end = surge.index[surge["date"] == pd.Timestamp(template.signal_date)][0]
    candidate = surge.iloc[: end + 1].copy()
    result = best_shape_similarity(candidate, [template])
    assert result["similarity_score"] >= 99
    assert result["similar_reference"] == "样本"


def test_similarity_excludes_same_stock_template():
    history = make_history()
    template = extract_reference_template(
        pd.concat(
            [
                history.iloc[:120],
                history.iloc[120:].assign(
                    close=np.linspace(history.iloc[120]["close"], 15.0, 20)
                ),
            ]
        ).reset_index(drop=True),
        code="600127",
        name="样本",
    )
    assert template is not None
    result = best_shape_similarity(history, [template], exclude_code="600127")
    assert result["similarity_score"] is None


def test_weekly_target_template_compares_weekly_closes():
    dates = pd.bdate_range("2025-07-01", periods=300)
    close = np.linspace(18.0, 9.0, len(dates))
    close[-45:] = np.linspace(7.0, 9.5, 45)
    history = pd.DataFrame({"date": dates, "close": close})

    template = current_reference_template(
        history,
        code="300300",
        name="海峡创新",
        timeframe="weekly",
    )

    assert template is not None
    assert template.timeframe == "weekly"
    assert len(template.path) == 40
    assert template.signal_date == dates[-1].date().isoformat()
    result = best_shape_similarity(history, [template])
    assert result["similarity_score"] >= 99
    assert result["similar_reference"] == "海峡创新"
    assert result["similar_reference_timeframe"] == "weekly"
