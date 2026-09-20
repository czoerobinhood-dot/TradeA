from __future__ import annotations

import json
import html
import re
from datetime import date
from typing import Any

import pandas as pd
import requests

from ashare_screener.provider import DataSourceError, normalize_code


SOHU_HISTORY_URL = "https://q.stock.sohu.com/hisHq"
SOHU_STOCK_URL = "https://q.stock.sohu.com/cn/{code}/index.shtml"


def _numeric(value: object, *, percent: bool = False) -> float:
    text = str(value).strip().replace(",", "")
    if percent:
        text = text.rstrip("%")
    return float(pd.to_numeric(text, errors="coerce"))


def parse_sohu_history(payload: Any) -> pd.DataFrame:
    if not isinstance(payload, list) or not payload:
        raise DataSourceError("搜狐日线返回格式不是非空列表")
    item = payload[0]
    if not isinstance(item, dict) or item.get("status") != 0:
        raise DataSourceError(f"搜狐日线状态异常: {item!r}")
    rows = item.get("hq")
    if not isinstance(rows, list) or not rows:
        raise DataSourceError("搜狐日线没有 hq 数据")

    parsed = []
    for row in rows:
        if not isinstance(row, list) or len(row) < 10:
            raise DataSourceError(f"搜狐日线字段数量异常: {row!r}")
        parsed.append(
            {
                "date": pd.to_datetime(row[0], errors="coerce"),
                "open": _numeric(row[1]),
                "close": _numeric(row[2]),
                "change": _numeric(row[3]),
                "change_pct": _numeric(row[4], percent=True),
                "low": _numeric(row[5]),
                "high": _numeric(row[6]),
                "volume": _numeric(row[7]),
                "amount": _numeric(row[8]),
                "turnover": _numeric(row[9], percent=True),
            }
        )
    frame = pd.DataFrame(parsed).dropna(
        subset=["date", "open", "close", "low", "high"]
    )
    return frame.sort_values("date").drop_duplicates("date", keep="last").reset_index(
        drop=True
    )


def parse_sohu_sector_page(page: str) -> str:
    """Extract Sohu's own industry label from an individual stock page."""
    match = re.search(
        r"\bvar\s+plate\s*=\s*([\"'])(?P<sector>.+?)\1\s*;?",
        page,
        flags=re.IGNORECASE,
    )
    if not match:
        raise DataSourceError("搜狐个股页没有找到行业字段 var plate")
    sector = html.unescape(match.group("sector")).strip()
    if not sector:
        raise DataSourceError("搜狐个股页行业字段为空")
    return sector


def fetch_sohu_sector(
    code: str,
    *,
    timeout: float = 15,
    session: requests.Session | None = None,
) -> dict[str, str]:
    code = normalize_code(code)
    client = session or requests.Session()
    response = client.get(
        SOHU_STOCK_URL.format(code=code),
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=timeout,
    )
    response.raise_for_status()
    try:
        page = response.content.decode("gb18030")
    except UnicodeDecodeError as exc:
        raise DataSourceError(f"搜狐个股页无法解码: {exc}") from exc
    return {
        "sector_name": parse_sohu_sector_page(page),
        "sector_source": "搜狐证券个股页行业分类",
        "sector_source_url": response.url,
    }


def fetch_sohu_history(
    code: str,
    *,
    start_date: date,
    end_date: date,
    timeout: float = 15,
    session: requests.Session | None = None,
) -> pd.DataFrame:
    code = normalize_code(code)
    client = session or requests.Session()
    response = client.get(
        SOHU_HISTORY_URL,
        params={
            "code": f"cn_{code}",
            "start": start_date.strftime("%Y%m%d"),
            "end": end_date.strftime("%Y%m%d"),
            "stat": "1",
            "order": "D",
            "period": "d",
            "rt": "json",
        },
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=timeout,
    )
    response.raise_for_status()
    try:
        text = response.content.decode("gb18030")
        payload = json.loads(text)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DataSourceError(f"搜狐日线无法解码: {exc}") from exc
    result = parse_sohu_history(payload)
    result.attrs.update(
        source="搜狐证券网页内部接口（未复权）",
        source_url=response.url,
    )
    return result
