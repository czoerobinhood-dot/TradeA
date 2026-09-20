from __future__ import annotations

import argparse
import json
import math
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import requests

from ashare_screener.config import ScreenConfig
from ashare_screener.features import calculate_price_metrics
from ashare_screener.provider import AkshareProvider
from ashare_screener.sohu import fetch_sohu_history


PRICE_COLUMNS = ("open", "high", "low", "close")


def latest_scan(path: Path) -> Path:
    files = sorted(path.glob("scan_*.json"), key=lambda item: item.stat().st_mtime)
    if not files:
        raise FileNotFoundError(f"没有扫描结果: {path}")
    return files[-1]


def finite(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def audit_one(
    *,
    candidate: dict[str, Any],
    provider: AkshareProvider,
    config: ScreenConfig,
    session: requests.Session,
    compare_days: int,
    timeout: float,
) -> dict[str, Any]:
    code = str(candidate["code"])
    existing = provider.history(code, config.history_days)
    sohu = fetch_sohu_history(
        code,
        start_date=provider.as_of - timedelta(days=500),
        end_date=provider.as_of,
        timeout=timeout,
        session=session,
    )
    common = existing.merge(sohu, on="date", suffixes=("_existing", "_sohu"))
    common = common.tail(compare_days)
    if common.empty:
        raise ValueError("现有日线与搜狐没有共同交易日")

    price_differences = []
    for column in PRICE_COLUMNS:
        price_differences.extend(
            (common[f"{column}_existing"] - common[f"{column}_sohu"])
            .abs()
            .tolist()
        )
    price_max_abs_diff = float(np.nanmax(price_differences))
    price_match = price_max_abs_diff <= 0.011

    turnover_diff = (
        common["turnover_existing"] - common["turnover_sohu"]
    ).abs()
    turnover_max_abs_diff = finite(turnover_diff.max())
    turnover_match = bool(
        turnover_max_abs_diff is not None and turnover_max_abs_diff <= 0.11
    )

    valid_volume = common[
        (common["volume_existing"] > 0) & (common["volume_sohu"] > 0)
    ].copy()
    if valid_volume.empty:
        volume_scale = None
        volume_median_relative_diff = None
        volume_match = False
    else:
        median_ratio = float(
            (valid_volume["volume_existing"] / valid_volume["volume_sohu"]).median()
        )
        volume_scale = min(
            (1.0, 100.0),
            key=lambda value: abs(math.log(median_ratio / value)),
        )
        normalized = valid_volume["volume_sohu"] * volume_scale
        denominator = pd.concat(
            [valid_volume["volume_existing"].abs(), normalized.abs()], axis=1
        ).max(axis=1)
        relative = (valid_volume["volume_existing"] - normalized).abs() / denominator
        volume_median_relative_diff = finite(relative.median())
        volume_match = bool(
            volume_median_relative_diff is not None
            and volume_median_relative_diff <= 0.01
        )

    sohu_metrics = calculate_price_metrics(
        sohu,
        code=code,
        rules=config.strict_rules,
        gap_rules=config.gap_rules,
    )
    report_gap_date = candidate.get("gap_date")
    report_gap_unfilled = candidate.get("gap_close_unfilled")
    sohu_gap_date = sohu_metrics.get("gap_date")
    sohu_gap_unfilled = sohu_metrics.get("gap_close_unfilled")
    gap_match = bool(
        report_gap_date == sohu_gap_date
        and bool(report_gap_unfilled) == bool(sohu_gap_unfilled)
    )

    latest_existing = pd.Timestamp(existing.iloc[-1]["date"]).date().isoformat()
    latest_sohu = pd.Timestamp(sohu.iloc[-1]["date"]).date().isoformat()
    date_match = latest_existing == latest_sohu
    if date_match and price_match and turnover_match and volume_match and gap_match:
        result = "confirmed"
    elif (
        date_match
        and abs(
            float(common.iloc[-1]["close_existing"])
            - float(common.iloc[-1]["close_sohu"])
        )
        <= 0.011
    ):
        result = "partial"
    else:
        result = "mismatch"
    return {
        "code": code,
        "name": candidate.get("name"),
        "decision": candidate.get("decision"),
        "existing_source": candidate.get("history_source"),
        "existing_cache_stale": candidate.get("history_cache_stale"),
        "latest_existing_date": latest_existing,
        "latest_sohu_date": latest_sohu,
        "date_match": date_match,
        "common_days_compared": len(common),
        "price_max_abs_diff": price_max_abs_diff,
        "price_match": price_match,
        "turnover_max_abs_diff": turnover_max_abs_diff,
        "turnover_match": turnover_match,
        "volume_scale_sohu_to_existing": volume_scale,
        "volume_median_relative_diff": volume_median_relative_diff,
        "volume_match": volume_match,
        "report_gap_date": report_gap_date,
        "sohu_gap_date": sohu_gap_date,
        "report_gap_unfilled": report_gap_unfilled,
        "sohu_gap_unfilled": sohu_gap_unfilled,
        "gap_match": gap_match,
        "result": result,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="搜狐与现有日线交叉校验")
    parser.add_argument("--config", default="config.example.json")
    parser.add_argument("--reports", default="reports")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--compare-days", type=int, default=10)
    parser.add_argument("--timeout", type=float, default=15)
    parser.add_argument("--delay", type=float, default=0.15)
    args = parser.parse_args()

    config = ScreenConfig.from_file(args.config)
    report_dir = Path(args.reports)
    source_path = latest_scan(report_dir)
    payload = json.loads(source_path.read_text(encoding="utf-8"))
    selected = list(payload.get("candidates", []))[: args.limit]
    by_code = {str(item["code"]): item for item in selected}
    for code in ("002281", "000592"):
        match = next(
            (item for item in payload.get("candidates", []) if str(item["code"]) == code),
            None,
        )
        if match is not None:
            by_code.setdefault(code, match)

    provider = AkshareProvider(cache_dir=".cache/akshare", offline=True)
    session = requests.Session()
    rows = []
    for candidate in by_code.values():
        try:
            row = audit_one(
                candidate=candidate,
                provider=provider,
                config=config,
                session=session,
                compare_days=args.compare_days,
                timeout=args.timeout,
            )
        except Exception as exc:
            row = {
                "code": candidate.get("code"),
                "name": candidate.get("name"),
                "decision": candidate.get("decision"),
                "result": "error",
                "error": str(exc),
            }
        rows.append(row)
        if args.delay > 0:
            time.sleep(args.delay)

    timestamp = pd.Timestamp.now().strftime("%Y%m%d_%H%M%S")
    csv_path = report_dir / f"source_audit_{timestamp}.csv"
    json_path = report_dir / f"source_audit_{timestamp}.json"
    pd.DataFrame(rows).to_csv(csv_path, index=False, encoding="utf-8-sig")
    summary = {
        "generated_at": pd.Timestamp.now().isoformat(),
        "source_report": str(source_path),
        "note": "搜狐 hisHq 是网页内部未复权接口，仅用于交叉校验",
        "counts": pd.Series([row["result"] for row in rows]).value_counts().to_dict(),
        "rows": rows,
    }
    json_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    print(json.dumps(summary["counts"], ensure_ascii=False))
    print(csv_path.resolve())
    print(json_path.resolve())
    return 0 if rows and all(row["result"] == "confirmed" for row in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
