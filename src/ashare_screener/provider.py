from __future__ import annotations

import hashlib
import re
import threading
from difflib import SequenceMatcher
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable

import pandas as pd
import requests


class DataSourceError(RuntimeError):
    """Raised when an upstream data source is unavailable or changes schema."""


def normalize_code(value: object) -> str:
    text = str(value).strip().upper()
    match = re.search(r"(\d{6})$", text)
    if not match:
        raise ValueError(f"无法识别股票代码: {value!r}")
    return match.group(1)


def market_prefix(code: str) -> str:
    code = normalize_code(code)
    if code.startswith(("4", "8", "9")):
        return "bj"
    if code.startswith("6"):
        return "sh"
    return "sz"


def prefixed_code(code: str) -> str:
    code = normalize_code(code)
    return f"{market_prefix(code).upper()}{code}"


def is_supported_a_share(code: str) -> bool:
    code = normalize_code(code)
    prefixes = (
        "000",
        "001",
        "002",
        "003",
        "300",
        "301",
        "302",
        "600",
        "601",
        "603",
        "605",
        "688",
        "689",
        "430",
        "831",
        "832",
        "833",
        "834",
        "835",
        "836",
        "837",
        "838",
        "839",
        "870",
        "871",
        "872",
        "873",
        "920",
    )
    return code.startswith(prefixes)


def normalize_sector_name(value: object) -> str:
    text = re.sub(r"[\s·•・/\\()（）\-—_]+", "", str(value).strip())
    for token in ("申万", "新浪", "行业", "板块", "产业", "基础"):
        text = text.replace(token, "")
    aliases = {
        "化学原料及化学制品": "化工",
        "化学原料和化学制品": "化工",
        "房地产开发经营": "房地产",
        "计算机软件": "软件服务",
    }
    return aliases.get(text, text)


def match_sector_name(source_name: object, board_names: list[object]) -> str | None:
    """Return one conservative Sohu-to-Sina industry match, or fail closed."""
    source = normalize_sector_name(source_name)
    if len(source) < 2:
        return None
    normalized = [(str(name).strip(), normalize_sector_name(name)) for name in board_names]
    exact = [name for name, candidate in normalized if candidate == source]
    if len(exact) == 1:
        return exact[0]

    scored: list[tuple[float, str]] = []
    for name, candidate in normalized:
        if len(candidate) < 2:
            continue
        ratio = SequenceMatcher(None, source, candidate).ratio()
        if min(len(source), len(candidate)) >= 3 and (
            source in candidate or candidate in source
        ):
            ratio = max(ratio, 0.90)
        scored.append((ratio, name))
    scored.sort(reverse=True)
    if not scored or scored[0][0] < 0.82:
        return None
    if len(scored) > 1 and scored[0][0] - scored[1][0] < 0.08:
        return None
    return scored[0][1]


_SECTOR_BOARD_OPTIONS: dict[str, tuple[str, ...]] = {
    "基础化工": ("化工行业", "化纤行业", "塑料制品", "农药化肥"),
    "建筑材料": ("建筑建材", "水泥行业", "玻璃行业", "陶瓷行业"),
    "计算机": ("电子信息",),
    "传媒": ("传媒娱乐",),
    "医药生物": ("生物制药", "医疗器械"),
    "轻工制造": ("家具行业", "造纸行业", "印刷包装", "其它行业"),
    "电力设备": ("发电设备", "电器行业", "仪器仪表", "机械行业"),
    "机械设备": ("机械行业", "仪器仪表", "纺织机械"),
    "汽车": ("汽车制造", "摩托车"),
    "家用电器": ("家电行业", "电器行业", "电子器件"),
    "美容护理": ("化工行业", "其它行业"),
    "商贸零售": ("商业百货", "物资外贸"),
    "建筑装饰": ("建筑建材",),
    "纺织服饰": ("服装鞋类", "纺织行业"),
    "电子": ("电子器件", "电子信息", "仪器仪表"),
    "通信": ("电子信息",),
    "交通运输": ("交通运输", "公路桥梁"),
}


def sector_board_candidates(
    source_name: object, board_names: list[object]
) -> list[str]:
    """List plausible legacy Sina boards; code membership must confirm the result."""
    available = {str(name).strip() for name in board_names}
    result: list[str] = []
    direct = match_sector_name(source_name, board_names)
    if direct is not None:
        result.append(direct)
    for name in _SECTOR_BOARD_OPTIONS.get(str(source_name).strip(), ()):
        if name in available and name not in result:
            result.append(name)
    return result


EASTMONEY_FUND_SNAPSHOT_URL = (
    "https://push2.eastmoney.com/api/qt/stock/fflow/kline/get"
)
THS_FUND_SNAPSHOT_URL = (
    "https://stockpage.10jqka.com.cn/stock_page/api/v1/stockpage/funds/"
)


def parse_eastmoney_fund_snapshot(payload: object) -> pd.DataFrame:
    if not isinstance(payload, dict) or payload.get("rc") != 0:
        raise DataSourceError(f"东方财富当日资金快照状态异常: {payload!r}")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise DataSourceError("东方财富当日资金快照没有 data")
    rows = data.get("klines")
    if not isinstance(rows, list) or not rows:
        raise DataSourceError("东方财富当日资金快照没有 klines")
    parsed = []
    for row in rows:
        values = str(row).split(",")
        if len(values) < 6:
            raise DataSourceError(f"东方财富当日资金快照字段不足: {row!r}")
        parsed.append(
            {
                "date": pd.to_datetime(values[0], errors="coerce"),
                "main_net_amount": pd.to_numeric(values[1], errors="coerce"),
                "small_net_amount": pd.to_numeric(values[2], errors="coerce"),
                "medium_net_amount": pd.to_numeric(values[3], errors="coerce"),
                "large_net_amount": pd.to_numeric(values[4], errors="coerce"),
                "super_large_net_amount": pd.to_numeric(values[5], errors="coerce"),
            }
        )
    result = pd.DataFrame(parsed).dropna(subset=["date"])
    if result.empty:
        raise DataSourceError("东方财富当日资金快照日期无效")
    return result.sort_values("date").drop_duplicates("date", keep="last")


def parse_ths_fund_snapshot(payload: object, snapshot_date: date) -> pd.DataFrame:
    if not isinstance(payload, dict) or payload.get("status_code") != 0:
        raise DataSourceError(f"同花顺当日资金快照状态异常: {payload!r}")
    data = payload.get("data")
    funds_data = data.get("fundsData") if isinstance(data, dict) else None
    flow = funds_data.get("largeOrderFlow") if isinstance(funds_data, dict) else None
    if not isinstance(flow, dict):
        raise DataSourceError("同花顺当日资金快照没有 largeOrderFlow")

    def numeric(name: str) -> float:
        value = pd.to_numeric(flow.get(name), errors="coerce")
        if pd.isna(value):
            raise DataSourceError(f"同花顺当日资金快照缺少 {name}")
        return float(value)

    super_large = numeric("mass_capital_net_inflow")
    large = numeric("big_capital_net_inflow")
    return pd.DataFrame(
        [
            {
                "date": pd.Timestamp(snapshot_date),
                "main_net_amount": super_large + large,
                "small_net_amount": numeric("small_capital_net_inflow"),
                "medium_net_amount": numeric("medium_capital_net_inflow"),
                "large_net_amount": large,
                "super_large_net_amount": super_large,
            }
        ]
    )


class CsvCache:
    def __init__(self, root: Path, ttl_minutes: int, offline: bool = False) -> None:
        self.root = root
        self.ttl = timedelta(minutes=ttl_minutes)
        self.offline = offline
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:20]
        return self.root / f"{digest}.csv"

    def path_for(self, key: str) -> Path:
        return self._path(key)

    def get_or_load(
        self,
        key: str,
        loader: Callable[[], pd.DataFrame],
        *,
        refresh: bool = False,
        allow_stale_on_error: bool = False,
    ) -> pd.DataFrame:
        path = self._path(key)
        if path.exists() and not refresh:
            age = datetime.now() - datetime.fromtimestamp(path.stat().st_mtime)
            if self.offline or age <= self.ttl:
                cached = pd.read_csv(path)
                cached.attrs["cache_stale"] = bool(self.offline or age > self.ttl)
                cached.attrs["cache_saved_at"] = datetime.fromtimestamp(
                    path.stat().st_mtime
                ).isoformat()
                return cached
        if self.offline:
            raise DataSourceError(f"离线缓存不存在或不可读: {key}")
        try:
            frame = loader()
        except Exception:
            if allow_stale_on_error and path.exists():
                cached = pd.read_csv(path)
                cached.attrs["cache_stale"] = True
                cached.attrs["cache_saved_at"] = datetime.fromtimestamp(
                    path.stat().st_mtime
                ).isoformat()
                return cached
            raise
        if not isinstance(frame, pd.DataFrame):
            raise DataSourceError(f"数据接口未返回 DataFrame: {key}")
        frame.to_csv(path, index=False, encoding="utf-8-sig")
        result = frame.copy()
        result.attrs["cache_stale"] = False
        result.attrs["cache_saved_at"] = datetime.now().isoformat()
        return result


class AkshareProvider:
    def __init__(
        self,
        *,
        cache_dir: str | Path = ".cache/akshare",
        cache_minutes: int = 180,
        refresh: bool = False,
        refresh_realtime: bool | None = None,
        offline: bool = False,
        as_of: date | None = None,
    ) -> None:
        try:
            import akshare as ak
        except ImportError as exc:
            raise DataSourceError(
                "未安装 AkShare；请先执行 python -m pip install -e ."
            ) from exc
        self.ak = ak
        self.cache = CsvCache(Path(cache_dir), cache_minutes, offline=offline)
        self.refresh = refresh
        self.refresh_realtime = refresh if refresh_realtime is None else refresh_realtime
        self.as_of = as_of or date.today()
        self._source_lock = threading.Lock()
        self._history_primary_available: bool | None = None
        self._history_primary_error: str | None = None
        self._fund_flow_available: bool | None = None
        self._fund_flow_error: str | None = None
        self._fund_flow_failures = 0

    def hot_stocks(self) -> pd.DataFrame:
        primary_error: Exception | None = None
        try:
            frame = self.cache.get_or_load(
                "hot-rank-em",
            self.ak.stock_hot_rank_em,
            refresh=self.refresh_realtime,
            allow_stale_on_error=True,
            )
            return self._normalize_hot_rank(frame, source="东方财富人气榜")
        except Exception as exc:
            primary_error = exc

        try:
            return self._hot_proxy_from_tencent()
        except Exception as fallback_error:
            raise DataSourceError(
                "东方财富人气榜失败，腾讯实时行情热点代理也失败；"
                f"人气榜: {primary_error}; 腾讯代理: {fallback_error}"
            ) from fallback_error

    def all_stocks(self) -> pd.DataFrame:
        tencent_error: Exception | None = None
        try:
            frame = self.cache.get_or_load(
                "all-stocks-tencent",
                self.ak.stock_zh_a_spot_tx,
                refresh=self.refresh_realtime,
                allow_stale_on_error=True,
            )
            return self._normalize_all_stocks_tencent(frame)
        except Exception as exc:
            tencent_error = exc

        try:
            frame = self.cache.get_or_load(
                "all-stocks-eastmoney",
                self.ak.stock_zh_a_spot_em,
                refresh=self.refresh_realtime,
                allow_stale_on_error=True,
            )
            return self._normalize_all_stocks_eastmoney(frame)
        except Exception as eastmoney_error:
            raise DataSourceError(
                "腾讯全A股行情与东方财富全A股行情均失败；"
                f"腾讯: {tencent_error}; 东方财富: {eastmoney_error}"
            ) from eastmoney_error

    def _normalize_all_stocks_tencent(self, frame: pd.DataFrame) -> pd.DataFrame:
        required = {
            "code",
            "name",
            "zxj",
            "zdf",
            "hsl",
            "lb",
            "zdf_d5",
            "zdf_d20",
            "zdf_d60",
        }
        self._require_columns(frame, required, "腾讯全A股行情")
        result = pd.DataFrame(
            {
                "code": frame["code"].map(self._safe_code),
                "name": frame["name"].astype(str).str.strip(),
                "latest": pd.to_numeric(frame["zxj"], errors="coerce"),
                "change_pct": pd.to_numeric(frame["zdf"], errors="coerce"),
                "turnover": pd.to_numeric(frame["hsl"], errors="coerce"),
                "volume_ratio": pd.to_numeric(frame["lb"], errors="coerce"),
                "return_5d": pd.to_numeric(frame["zdf_d5"], errors="coerce"),
                "return_20d": pd.to_numeric(frame["zdf_d20"], errors="coerce"),
                "return_60d": pd.to_numeric(frame["zdf_d60"], errors="coerce"),
                "return_52w": pd.to_numeric(frame.get("zdf_w52"), errors="coerce"),
                "source": "腾讯全A股实时行情",
            }
        )
        output = result.dropna(subset=["code", "latest"]).copy()
        output = output[output["latest"] > 0]
        output.attrs.update(frame.attrs)
        return output

    def _normalize_all_stocks_eastmoney(self, frame: pd.DataFrame) -> pd.DataFrame:
        required = {"代码", "名称", "最新价", "涨跌幅", "换手率", "量比"}
        self._require_columns(frame, required, "东方财富全A股行情")

        def numeric(column: str) -> pd.Series:
            if column not in frame:
                return pd.Series(float("nan"), index=frame.index)
            return pd.to_numeric(frame[column], errors="coerce")

        result = pd.DataFrame(
            {
                "code": frame["代码"].map(self._safe_code),
                "name": frame["名称"].astype(str).str.strip(),
                "latest": numeric("最新价"),
                "change_pct": numeric("涨跌幅"),
                "turnover": numeric("换手率"),
                "volume_ratio": numeric("量比"),
                "return_5d": numeric("5日涨跌幅"),
                "return_20d": numeric("20日涨跌幅"),
                "return_60d": numeric("60日涨跌幅"),
                "return_52w": numeric("年初至今涨跌幅"),
                "source": "东方财富全A股实时行情",
            }
        )
        output = result.dropna(subset=["code", "latest"]).copy()
        output = output[output["latest"] > 0]
        output.attrs.update(frame.attrs)
        return output

    def _hot_proxy_from_tencent(self) -> pd.DataFrame:
        if not hasattr(self.ak, "stock_zh_a_spot_tx"):
            raise DataSourceError("当前 AkShare 缺少 stock_zh_a_spot_tx")
        frame = self.cache.get_or_load(
            "hot-proxy-tencent",
            self.ak.stock_zh_a_spot_tx,
            refresh=self.refresh_realtime,
            allow_stale_on_error=True,
        )
        required = {"code", "name", "zdf", "hsl", "lb"}
        self._require_columns(frame, required, "腾讯实时行情热点代理")
        result = pd.DataFrame(
            {
                "code": frame["code"].map(self._safe_code),
                "name": frame["name"].astype(str).str.strip(),
                "latest": pd.to_numeric(frame.get("zxj"), errors="coerce"),
                "change_pct": pd.to_numeric(frame["zdf"], errors="coerce"),
                "turnover": pd.to_numeric(frame["hsl"], errors="coerce"),
                "volume_ratio": pd.to_numeric(frame["lb"], errors="coerce"),
            }
        )
        result = result[result["code"].notna()].copy()
        result = result[result["code"].map(is_supported_a_share)]
        for column in ("change_pct", "turnover", "volume_ratio"):
            result[column] = result[column].replace([float("inf"), -float("inf")], pd.NA)
        result["change_pct"] = result["change_pct"].fillna(0.0)
        result["turnover"] = result["turnover"].fillna(0.0)
        result["volume_ratio"] = result["volume_ratio"].fillna(0.0)
        # This is a transparent activity proxy, not the official popularity rank.
        result["activity_score"] = (
            result["change_pct"].rank(pct=True) * 0.55
            + result["turnover"].rank(pct=True) * 0.25
            + result["volume_ratio"].rank(pct=True) * 0.20
        )
        result.sort_values(
            ["activity_score", "change_pct", "turnover"],
            ascending=False,
            inplace=True,
        )
        result["rank"] = range(1, len(result) + 1)
        result["source"] = "热点代理：腾讯实时涨幅/换手/量比"
        output = result[["rank", "code", "name", "latest", "change_pct", "source"]].copy()
        output.attrs.update(frame.attrs)
        return output

    def _normalize_hot_rank(self, frame: pd.DataFrame, source: str) -> pd.DataFrame:
        required = {"当前排名", "代码", "股票名称"}
        self._require_columns(frame, required, source)
        result = pd.DataFrame(
            {
                "rank": pd.to_numeric(frame["当前排名"], errors="coerce"),
                "code": frame["代码"].map(self._safe_code),
                "name": frame["股票名称"].astype(str).str.strip(),
                "latest": pd.to_numeric(frame.get("最新价"), errors="coerce"),
                "change_pct": pd.to_numeric(frame.get("涨跌幅"), errors="coerce"),
                "source": source,
            }
        )
        output = result.dropna(subset=["rank", "code"]).sort_values("rank")
        output.attrs.update(frame.attrs)
        return output

    def hot_concepts(self) -> pd.DataFrame:
        if not hasattr(self.ak, "stock_board_concept_name_em"):
            raise DataSourceError("当前 AkShare 缺少 stock_board_concept_name_em")
        frame = self.cache.get_or_load(
            "concept-name-em",
            self.ak.stock_board_concept_name_em,
            refresh=self.refresh_realtime,
            allow_stale_on_error=True,
        )
        self._require_columns(frame, {"板块名称", "涨跌幅"}, "概念板块行情")
        result = pd.DataFrame(
            {
                "name": frame["板块名称"].astype(str).str.strip(),
                "change_pct": pd.to_numeric(frame["涨跌幅"], errors="coerce"),
            }
        )
        return result.dropna(subset=["name", "change_pct"]).sort_values(
            "change_pct", ascending=False
        )

    def concept_members(self, concept: str) -> pd.DataFrame:
        if not hasattr(self.ak, "stock_board_concept_cons_em"):
            raise DataSourceError("当前 AkShare 缺少 stock_board_concept_cons_em")
        frame = self.cache.get_or_load(
            f"concept-members:{concept}",
            lambda: self.ak.stock_board_concept_cons_em(symbol=concept),
            refresh=self.refresh_realtime,
            allow_stale_on_error=True,
        )
        self._require_columns(frame, {"代码", "名称"}, f"概念成分股:{concept}")
        change_column = "涨跌幅" if "涨跌幅" in frame else None
        result = pd.DataFrame(
            {
                "code": frame["代码"].map(self._safe_code),
                "name": frame["名称"].astype(str).str.strip(),
                "change_pct": (
                    pd.to_numeric(frame[change_column], errors="coerce")
                    if change_column
                    else float("nan")
                ),
            }
        )
        output = result.dropna(subset=["code"])
        output.attrs.update(frame.attrs)
        return output

    def stock_sector(self, code: str) -> dict[str, object]:
        code = normalize_code(code)

        def load_profile() -> pd.DataFrame:
            # Local import avoids making the Sohu history helper part of provider setup.
            from ashare_screener.sohu import fetch_sohu_sector

            return pd.DataFrame([fetch_sohu_sector(code)])

        frame = self.cache.get_or_load(
            f"sohu-sector:{code}",
            load_profile,
            refresh=self.refresh,
            allow_stale_on_error=True,
        )
        self._require_columns(
            frame,
            {"sector_name", "sector_source", "sector_source_url"},
            f"搜狐行业分类:{code}",
        )
        if frame.empty:
            raise DataSourceError(f"搜狐行业分类为空: {code}")
        row = frame.iloc[0]
        return {
            "sector_name": str(row["sector_name"]).strip(),
            "sector_source": str(row["sector_source"]).strip(),
            "sector_source_url": str(row["sector_source_url"]).strip(),
            "sector_cache_stale": bool(frame.attrs.get("cache_stale", False)),
            "sector_cache_saved_at": frame.attrs.get("cache_saved_at"),
        }

    def industry_sectors(self) -> pd.DataFrame:
        if not hasattr(self.ak, "stock_sector_spot"):
            raise DataSourceError("当前 AkShare 缺少 stock_sector_spot")
        frame = self.cache.get_or_load(
            "industry-sectors-sina",
            lambda: self.ak.stock_sector_spot(indicator="新浪行业"),
            refresh=self.refresh_realtime,
            allow_stale_on_error=True,
        )
        self._require_columns(frame, {"label", "板块", "涨跌幅"}, "新浪行业板块")
        result = pd.DataFrame(
            {
                "sector_key": frame["label"].astype(str).str.strip(),
                "sector_name": frame["板块"].astype(str).str.strip(),
                "change_pct": pd.to_numeric(
                    frame["涨跌幅"].astype(str).str.rstrip("%"), errors="coerce"
                ),
            }
        ).dropna(subset=["sector_key", "sector_name", "change_pct"])
        result = result[result["sector_name"] != ""].sort_values(
            "change_pct", ascending=False, kind="stable"
        )
        result = result.drop_duplicates("sector_name", keep="first").reset_index(drop=True)
        result["rank"] = range(1, len(result) + 1)
        result["source"] = "新浪财经行业板块行情（AkShare）"
        result.attrs.update(frame.attrs)
        return result

    def industry_members(self, sector_key: str) -> pd.DataFrame:
        if not hasattr(self.ak, "stock_sector_detail"):
            raise DataSourceError("当前 AkShare 缺少 stock_sector_detail")
        frame = self.cache.get_or_load(
            f"industry-members-sina:{sector_key}",
            lambda: self.ak.stock_sector_detail(sector=sector_key),
            refresh=self.refresh_realtime,
            allow_stale_on_error=True,
        )
        self._require_columns(
            frame,
            {"code", "name", "changepercent"},
            f"新浪行业成分股:{sector_key}",
        )
        result = pd.DataFrame(
            {
                "code": frame["code"].map(self._safe_code),
                "name": frame["name"].astype(str).str.strip(),
                "change_pct": pd.to_numeric(
                    frame["changepercent"].astype(str).str.rstrip("%"),
                    errors="coerce",
                ),
                "turnover": pd.to_numeric(frame.get("turnoverratio"), errors="coerce"),
            }
        ).dropna(subset=["code"])
        result = result.sort_values("change_pct", ascending=False, kind="stable").reset_index(
            drop=True
        )
        result["rank"] = range(1, len(result) + 1)
        result["source"] = "新浪财经行业成分股行情（AkShare）"
        result.attrs.update(frame.attrs)
        return result

    def history(self, code: str, history_days: int) -> pd.DataFrame:
        code = normalize_code(code)
        start = self.as_of - timedelta(days=max(int(history_days * 1.6), 365))
        key = f"history:{code}:{start:%Y%m%d}:{self.as_of:%Y%m%d}:qfq"

        def load_history() -> pd.DataFrame:
            if market_prefix(code) == "bj":
                try:
                    return self._load_eastmoney_history(code, start)
                except Exception as exc:
                    raise DataSourceError(
                        f"北交所日线主源失败，腾讯没有可用备用源: {exc}"
                    ) from exc
            if self._history_primary_available is None:
                with self._source_lock:
                    if self._history_primary_available is None:
                        try:
                            result = self._load_eastmoney_history(code, start)
                            self._history_primary_available = True
                            return result
                        except Exception as exc:
                            self._history_primary_available = False
                            self._history_primary_error = str(exc)
            elif self._history_primary_available:
                try:
                    return self._load_eastmoney_history(code, start)
                except Exception as exc:
                    self._history_primary_available = False
                    self._history_primary_error = str(exc)

            if not hasattr(self.ak, "stock_zh_a_hist_tx"):
                raise DataSourceError(
                    "东方财富日线失败且没有可用备用源: "
                    f"{self._history_primary_error or '未知错误'}"
                )
            try:
                result = self.ak.stock_zh_a_hist_tx(
                    symbol=f"{market_prefix(code)}{code}",
                    start_date=start.strftime("%Y%m%d"),
                    end_date=self.as_of.strftime("%Y%m%d"),
                    adjust="qfq",
                    timeout=20,
                )
                result["__source"] = "腾讯（东方财富失败后回退）"
                return result
            except Exception as fallback_error:
                raise DataSourceError(
                    "日线主源与备用源均失败；"
                    f"东方财富: {self._history_primary_error or '未知错误'}; "
                    f"腾讯: {fallback_error}"
                ) from fallback_error

        frame = self.cache.get_or_load(
            key,
            load_history,
            refresh=self.refresh,
            allow_stale_on_error=True,
        )
        source = (
            str(frame["__source"].dropna().iloc[-1])
            if "__source" in frame and not frame["__source"].dropna().empty
            else "未知缓存源"
        )
        result = normalize_history(frame).tail(history_days).reset_index(drop=True)
        result.attrs["source"] = source
        result.attrs["cache_stale"] = bool(frame.attrs.get("cache_stale", False))
        result.attrs["cache_saved_at"] = frame.attrs.get("cache_saved_at")
        return result

    def fund_flow(self, code: str) -> pd.DataFrame:
        code = normalize_code(code)
        if not hasattr(self.ak, "stock_individual_fund_flow"):
            raise DataSourceError("当前 AkShare 缺少 stock_individual_fund_flow")
        key = f"fund-flow:{code}:{market_prefix(code)}"

        def load_fund_flow() -> pd.DataFrame:
            if self._fund_flow_available is False:
                raise DataSourceError(
                    "本轮扫描已停止重复请求四档资金流: "
                    f"{self._fund_flow_error or '未知连接错误'}"
                )
            try:
                result = self.ak.stock_individual_fund_flow(
                    stock=code, market=market_prefix(code)
                )
            except Exception as exc:
                with self._source_lock:
                    self._fund_flow_failures += 1
                    self._fund_flow_error = str(exc)
                    if self._fund_flow_failures >= 3:
                        self._fund_flow_available = False
                raise DataSourceError(f"四档历史资金流接口连接失败: {exc}") from exc
            with self._source_lock:
                self._fund_flow_available = True
                self._fund_flow_failures = 0
            return result

        frame = self.cache.get_or_load(
            key,
            load_fund_flow,
            refresh=self.refresh,
            allow_stale_on_error=True,
        )
        result = normalize_fund_flow(frame)
        result.attrs["cache_stale"] = bool(frame.attrs.get("cache_stale", False))
        result.attrs["cache_saved_at"] = frame.attrs.get("cache_saved_at")
        return result

    def fund_flow_snapshot(self, code: str) -> pd.DataFrame:
        code = normalize_code(code)
        key = f"fund-flow-snapshot:{code}:{market_prefix(code)}"
        path = self.cache.path_for(key)

        def read_saved() -> pd.DataFrame:
            frame = pd.read_csv(path)
            frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
            return frame.dropna(subset=["date"])

        if self.cache.offline:
            if not path.exists():
                raise DataSourceError(f"离线资金快照不存在: {code}")
            result = read_saved()
            result.attrs.update(
                source="东方财富当日四档资金快照累计",
                compatible_with_history=True,
                cache_stale=True,
                cache_saved_at=datetime.fromtimestamp(path.stat().st_mtime).isoformat(),
            )
            return result

        try:
            market_id = 1 if market_prefix(code) == "sh" else 0
            client = requests.Session()
            client.trust_env = False
            response = client.get(
                EASTMONEY_FUND_SNAPSHOT_URL,
                params={
                    "secid": f"{market_id}.{code}",
                    "lmt": "1",
                    "klt": "101",
                    "fields1": "f1,f2,f3,f7",
                    "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
                    "ut": "b2884a393a59ad64002292a3e90d46a5",
                },
                headers={
                    "User-Agent": "Mozilla/5.0",
                    "Referer": "https://data.eastmoney.com/",
                },
                timeout=10,
            )
            response.raise_for_status()
            live = parse_eastmoney_fund_snapshot(response.json())
        except Exception as exc:
            if not path.exists():
                raise DataSourceError(f"当日四档资金快照接口失败: {exc}") from exc
            result = read_saved()
            result.attrs.update(
                source="东方财富当日四档资金快照累计",
                compatible_with_history=True,
                cache_stale=True,
                cache_saved_at=datetime.fromtimestamp(path.stat().st_mtime).isoformat(),
            )
            return result

        saved = read_saved() if path.exists() else pd.DataFrame(columns=live.columns)
        result = pd.concat([saved, live], ignore_index=True)
        result = result.sort_values("date").drop_duplicates("date", keep="last")
        result.to_csv(path, index=False, encoding="utf-8-sig")
        result.attrs.update(
            source="东方财富当日四档资金快照累计",
            source_url=response.url,
            compatible_with_history=True,
            cache_stale=False,
            cache_saved_at=datetime.now().isoformat(),
        )
        return result

    def fund_flow_snapshot_ths(
        self, code: str, *, snapshot_date: date
    ) -> pd.DataFrame:
        code = normalize_code(code)
        key = f"fund-flow-snapshot-ths:{code}:{market_prefix(code)}"
        path = self.cache.path_for(key)

        def read_saved() -> pd.DataFrame:
            frame = pd.read_csv(path)
            frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
            return frame.dropna(subset=["date"])

        source = "同花顺当日四档资金快照累计（独立订单分档口径）"
        if self.cache.offline:
            if not path.exists():
                raise DataSourceError(f"离线同花顺资金快照不存在: {code}")
            result = read_saved()
            result.attrs.update(
                source=source,
                compatible_with_history=False,
                cache_stale=True,
                cache_saved_at=datetime.fromtimestamp(path.stat().st_mtime).isoformat(),
            )
            return result

        market_id = {"sh": 17, "sz": 33, "bj": 151}[market_prefix(code)]
        try:
            response = requests.get(
                THS_FUND_SNAPSHOT_URL,
                params={"code": code, "marketId": market_id},
                headers={
                    "User-Agent": "Mozilla/5.0",
                    "Referer": f"https://stockpage.10jqka.com.cn/{code}/money-flow/",
                },
                timeout=10,
            )
            response.raise_for_status()
            live = parse_ths_fund_snapshot(response.json(), snapshot_date)
        except Exception as exc:
            if not path.exists():
                raise DataSourceError(f"同花顺当日四档资金快照接口失败: {exc}") from exc
            result = read_saved()
            result.attrs.update(
                source=source,
                compatible_with_history=False,
                cache_stale=True,
                cache_saved_at=datetime.fromtimestamp(path.stat().st_mtime).isoformat(),
            )
            return result

        saved = read_saved() if path.exists() else pd.DataFrame(columns=live.columns)
        result = pd.concat([saved, live], ignore_index=True)
        result = result.sort_values("date").drop_duplicates("date", keep="last")
        result.to_csv(path, index=False, encoding="utf-8-sig")
        result.attrs.update(
            source=source,
            source_url=response.url,
            compatible_with_history=False,
            cache_stale=False,
            cache_saved_at=datetime.now().isoformat(),
        )
        return result

    def _load_eastmoney_history(self, code: str, start: date) -> pd.DataFrame:
        result = self.ak.stock_zh_a_hist(
            symbol=code,
            period="daily",
            start_date=start.strftime("%Y%m%d"),
            end_date=self.as_of.strftime("%Y%m%d"),
            adjust="qfq",
            timeout=8,
        )
        result["__source"] = "东方财富"
        return result

    @staticmethod
    def _require_columns(frame: pd.DataFrame, columns: set[str], source: str) -> None:
        missing = sorted(columns - set(frame.columns))
        if missing:
            raise DataSourceError(
                f"{source} 字段发生变化，缺少: {', '.join(missing)}"
            )

    @staticmethod
    def _safe_code(value: object) -> str | None:
        try:
            text = str(value).strip()
            numeric_match = re.fullmatch(r"(\d{1,6})(?:\.0+)?", text)
            if numeric_match:
                return numeric_match.group(1).zfill(6)
            return normalize_code(value)
        except ValueError:
            return None


def normalize_history(frame: pd.DataFrame) -> pd.DataFrame:
    aliases = {
        "date": ("日期", "date"),
        "open": ("开盘", "open"),
        "close": ("收盘", "close"),
        "high": ("最高", "high"),
        "low": ("最低", "low"),
        "volume": ("成交量", "volume"),
        "amount": ("成交额", "amount"),
        "turnover": ("换手率", "turnover"),
    }
    payload: dict[str, pd.Series] = {}
    for target, choices in aliases.items():
        source = next((item for item in choices if item in frame.columns), None)
        if source is None:
            if target in {"amount", "turnover"}:
                payload[target] = pd.Series(float("nan"), index=frame.index)
                continue
            raise DataSourceError(f"日线字段发生变化，缺少 {target}")
        payload[target] = frame[source]
    result = pd.DataFrame(payload)
    result["date"] = pd.to_datetime(result["date"], errors="coerce")
    for column in result.columns.difference(["date"]):
        result[column] = pd.to_numeric(result[column], errors="coerce")
    turnover_values = result["turnover"].dropna().abs()
    if not turnover_values.empty and turnover_values.quantile(0.95) <= 1.5:
        result["turnover"] = result["turnover"] * 100
    result = result.dropna(subset=["date", "open", "high", "low", "close"])
    return result.sort_values("date").drop_duplicates("date", keep="last")


def normalize_fund_flow(frame: pd.DataFrame) -> pd.DataFrame:
    aliases = {
        "date": ("日期", "date"),
        "super_large_pct": (
            "超大单净流入-净占比",
            "超大单净流入净占比",
            "超大单净占比",
        ),
        "large_pct": ("大单净流入-净占比", "大单净流入净占比", "大单净占比"),
        "medium_pct": (
            "中单净流入-净占比",
            "中单净流入净占比",
            "中单净占比",
        ),
        "small_pct": ("小单净流入-净占比", "小单净流入净占比", "小单净占比"),
    }
    payload: dict[str, pd.Series] = {}
    for target, choices in aliases.items():
        source = next((item for item in choices if item in frame.columns), None)
        if source is None:
            raise DataSourceError(
                f"资金流字段发生变化，缺少 {target}；实际字段: {list(frame.columns)}"
            )
        payload[target] = frame[source]
    result = pd.DataFrame(payload)
    result["date"] = pd.to_datetime(result["date"], errors="coerce")
    for column in result.columns.difference(["date"]):
        result[column] = pd.to_numeric(result[column], errors="coerce")
    result = result.dropna(subset=["date"]).sort_values("date")
    return result.drop_duplicates("date", keep="last").reset_index(drop=True)
