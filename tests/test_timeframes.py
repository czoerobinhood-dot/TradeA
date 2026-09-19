from ashare_screener.config import ScreenConfig


def test_default_config_contains_confirmed_weekly_shape_samples():
    config = ScreenConfig()
    samples = {item["code"]: item for item in config.calibration_stocks}

    assert samples["300300"] == {
        "code": "300300",
        "name": "海峡创新",
        "role": "target",
        "timeframe": "weekly",
    }
    assert samples["603396"] == {
        "code": "603396",
        "name": "金辰股份",
        "role": "target",
        "timeframe": "weekly",
    }


def test_config_rejects_unknown_shape_timeframe():
    config = ScreenConfig(
        calibration_stocks=[
            {
                "code": "300300",
                "name": "海峡创新",
                "role": "target",
                "timeframe": "monthly",
            }
        ]
    )

    try:
        config.validate()
    except ValueError as exc:
        assert "timeframe" in str(exc)
    else:
        raise AssertionError("invalid shape timeframe should fail validation")
