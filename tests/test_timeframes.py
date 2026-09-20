from ashare_screener.config import ScreenConfig


def test_default_config_uses_daily_shape_samples_only():
    config = ScreenConfig()
    samples = {item["code"]: item for item in config.calibration_stocks}

    assert samples["300300"] == {
        "code": "300300",
        "name": "海峡创新",
        "role": "target",
    }
    assert samples["603396"] == {
        "code": "603396",
        "name": "金辰股份",
        "role": "target",
    }
    assert all(item.get("timeframe", "daily") == "daily" for item in config.references)
    assert all(
        item.get("timeframe", "daily") == "daily"
        for item in config.calibration_stocks
    )


def test_config_rejects_non_daily_shape_timeframe():
    config = ScreenConfig(
        calibration_stocks=[
            {
                "code": "300300",
                "name": "海峡创新",
                "role": "target",
                "timeframe": "weekly",
            }
        ]
    )

    try:
        config.validate()
    except ValueError as exc:
        assert "timeframe" in str(exc)
    else:
        raise AssertionError("invalid shape timeframe should fail validation")
