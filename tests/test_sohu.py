import json
from datetime import date

from ashare_screener.sohu import (
    SOHU_HISTORY_URL,
    SOHU_STOCK_URL,
    fetch_sohu_history,
    fetch_sohu_sector,
    parse_sohu_history,
    parse_sohu_sector_page,
)


def test_parse_sohu_history_fields_and_order():
    payload = [
        {
            "status": 0,
            "hq": [
                [
                    "2026-09-18",
                    "8.29",
                    "8.63",
                    "0.26",
                    "3.11%",
                    "8.26",
                    "8.87",
                    "3981406",
                    "342190.56",
                    "20.79%",
                    "6543.00",
                ],
                [
                    "2026-09-17",
                    "8.50",
                    "8.37",
                    "-0.34",
                    "-3.90%",
                    "8.12",
                    "8.68",
                    "4150858",
                    "348138.41",
                    "21.67%",
                    "4825.00",
                ],
            ],
        }
    ]

    result = parse_sohu_history(payload)

    assert result["date"].dt.strftime("%Y-%m-%d").tolist() == [
        "2026-09-17",
        "2026-09-18",
    ]
    assert result.iloc[-1]["close"] == 8.63
    assert result.iloc[-1]["turnover"] == 20.79


def test_fetch_sohu_history_uses_the_observed_web_contract():
    payload = [
        {
            "status": 0,
            "hq": [
                [
                    "2026-09-18",
                    "8.29",
                    "8.63",
                    "0.26",
                    "3.11%",
                    "8.26",
                    "8.87",
                    "3981406",
                    "342190.56",
                    "20.79%",
                ]
            ],
        }
    ]

    class FakeResponse:
        content = json.dumps(payload, ensure_ascii=False).encode("gb18030")
        url = "https://q.stock.sohu.com/hisHq?code=cn_000592"

        @staticmethod
        def raise_for_status():
            return None

    class FakeSession:
        def __init__(self):
            self.request = None

        def get(self, url, **kwargs):
            self.request = (url, kwargs)
            return FakeResponse()

    session = FakeSession()
    result = fetch_sohu_history(
        "000592",
        start_date=date(2026, 9, 1),
        end_date=date(2026, 9, 18),
        timeout=7,
        session=session,
    )

    url, request = session.request
    assert url == SOHU_HISTORY_URL
    assert request["params"] == {
        "code": "cn_000592",
        "start": "20260901",
        "end": "20260918",
        "stat": "1",
        "order": "D",
        "period": "d",
        "rt": "json",
    }
    assert request["timeout"] == 7
    assert result.iloc[0]["close"] == 8.63
    assert result.attrs["source"] == "搜狐证券网页内部接口（未复权）"


def test_parse_sohu_sector_page_uses_plate_variable():
    page = '<script>var plate="基础化工"; var ignored="其他";</script>'

    assert parse_sohu_sector_page(page) == "基础化工"


def test_fetch_sohu_sector_uses_individual_stock_page():
    class FakeResponse:
        content = 'var plate="通信设备";'.encode("gb18030")
        url = "https://q.stock.sohu.com/cn/002281/index.shtml"

        @staticmethod
        def raise_for_status():
            return None

    class FakeSession:
        def __init__(self):
            self.request = None

        def get(self, url, **kwargs):
            self.request = (url, kwargs)
            return FakeResponse()

    session = FakeSession()
    result = fetch_sohu_sector("002281", timeout=7, session=session)

    url, request = session.request
    assert url == SOHU_STOCK_URL.format(code="002281")
    assert request["timeout"] == 7
    assert result["sector_name"] == "通信设备"
    assert result["sector_source"] == "搜狐证券个股页行业分类"
