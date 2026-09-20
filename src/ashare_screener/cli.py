from __future__ import annotations

import argparse
import sys
from datetime import date, datetime
from pathlib import Path

from ashare_screener.config import ScreenConfig
from ashare_screener.pages import (
    export_pages_report,
    verify_pages_deployment,
    verify_pages_export,
)
from ashare_screener.pipeline import Screener
from ashare_screener.provider import AkshareProvider, DataSourceError
from ashare_screener.report import write_reports
from ashare_screener.server import serve


def _configure_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


def _date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("日期格式必须是 YYYY-MM-DD") from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="a-share-screen",
        description="从A股中筛选与已确认样本相似的同周期价格形态",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    scan = subparsers.add_parser("scan", help="执行一次扫描并生成 CSV/JSON/HTML")
    scan.add_argument("--config", default="config.example.json", help="JSON 配置文件")
    scan.add_argument("--output", default="reports", help="报告输出目录")
    scan.add_argument("--cache", default=".cache/akshare", help="数据缓存目录")
    scan.add_argument("--top", type=int, help="覆盖报告展示数量")
    scan.add_argument("--as-of", type=_date, help="历史扫描截止日 YYYY-MM-DD")
    scan.add_argument("--refresh", action="store_true", help="忽略有效缓存并重新抓取")
    scan.add_argument("--offline", action="store_true", help="只使用本地缓存")
    scan.add_argument("--no-concepts", action="store_true", help="不读取热点概念板块")
    scan.add_argument("--no-fund-flow", action="store_true", help="不读取四档资金流")

    web = subparsers.add_parser("serve", help="启动刷新即扫描的本地网页")
    web.add_argument("--config", default="config.example.json", help="JSON 配置文件")
    web.add_argument("--output", default="reports", help="报告输出目录")
    web.add_argument("--cache", default=".cache/akshare", help="数据缓存目录")
    web.add_argument("--host", default="127.0.0.1", help="监听地址")
    web.add_argument("--port", type=int, default=8765, help="监听端口")
    web.add_argument("--cooldown", type=int, default=30, help="重复扫描冷却秒数")
    web.add_argument(
        "--refresh-interval",
        type=int,
        default=0,
        help="后台自动刷新行情的间隔秒数，0 表示关闭",
    )
    web.add_argument("--top", type=int, help="覆盖报告展示数量")
    web.add_argument("--no-concepts", action="store_true", help="不读取热点概念板块")
    web.add_argument("--no-fund-flow", action="store_true", help="不读取四档资金流")

    pages = subparsers.add_parser(
        "export-pages", help="将最新 HTML 导出为 Cloudflare Pages 只读站点"
    )
    pages.add_argument("--source", default="reports/latest.html", help="源 HTML 报告")
    pages.add_argument("--output", default="site", help="Pages 输出目录")

    verify_pages = subparsers.add_parser(
        "verify-pages", help="校验本地 Pages 导出，并可回读线上生产站点"
    )
    verify_pages.add_argument("--source", default="reports/latest.html", help="源 HTML 报告")
    verify_pages.add_argument("--output", default="site", help="Pages 输出目录")
    verify_pages.add_argument("--url", help="可选的 Pages 生产地址")

    validate = subparsers.add_parser("validate", help="只校验配置文件")
    validate.add_argument("--config", default="config.example.json")
    return parser


def _progress(message: str) -> None:
    now = datetime.now().strftime("%H:%M:%S")
    print(f"[{now}] {message}", flush=True)


def main(argv: list[str] | None = None) -> int:
    _configure_console()
    args = build_parser().parse_args(argv)
    try:
        if args.command == "export-pages":
            paths = export_pages_report(args.source, args.output)
            for label, path in paths.items():
                print(f"{label}: {path.resolve()}")
            return 0
        if args.command == "verify-pages":
            manifest = verify_pages_export(args.source, args.output)
            print(
                "本地 Pages 导出一致: "
                f"报告时间={manifest['report']['generated_at']}, "
                f"SHA256={manifest['report']['sha256']}"
            )
            if args.url:
                result = verify_pages_deployment(args.output, args.url)
                print(
                    "线上 Pages 部署一致: "
                    f"{result['url']}, 成员服务已连接"
                )
            return 0
        config_path = Path(args.config) if args.config else None
        config = ScreenConfig.from_file(config_path)
        if args.command == "validate":
            print(f"配置有效: {config_path}")
            return 0
        if args.command == "serve":
            if args.top is not None and args.top <= 0:
                raise ValueError("--top 必须大于 0")
            if args.cooldown < 0:
                raise ValueError("--cooldown 不能小于 0")
            if args.refresh_interval < 0:
                raise ValueError("--refresh-interval 不能小于 0")
            serve(
                config_path=config_path,
                output_dir=args.output,
                cache_dir=args.cache,
                host=args.host,
                port=args.port,
                cooldown_seconds=args.cooldown,
                auto_refresh_seconds=args.refresh_interval,
                report_limit=args.top,
                use_concepts=not args.no_concepts,
                use_fund_flow=not args.no_fund_flow,
            )
            return 0
        if args.top is not None:
            if args.top <= 0:
                raise ValueError("--top 必须大于 0")
            config.report_limit = args.top
        provider = AkshareProvider(
            cache_dir=args.cache,
            cache_minutes=config.cache_minutes,
            refresh=args.refresh,
            offline=args.offline,
            as_of=args.as_of,
        )
        outcome = Screener(
            provider,
            config,
            progress=_progress,
            use_concepts=not args.no_concepts,
            use_fund_flow=not args.no_fund_flow,
        ).run()
        paths = write_reports(outcome, args.output, config.report_limit)
        print(
            f"扫描完成: status={outcome.status}, 有效候选={len(outcome.candidates)}, "
            f"数据问题={len(outcome.issues)}"
        )
        for label, path in paths.items():
            print(f"{label}: {path.resolve()}")
        if outcome.candidates:
            print("\n前列候选（仅供研究复核）:")
            for candidate in outcome.candidates[: min(10, config.report_limit)]:
                print(
                    f"{candidate.code} {candidate.name} | "
                    f"{candidate.metrics.get('decision')} | "
                    f"{candidate.metrics.get('final_score', 0):.1f} | "
                    f"{candidate.metrics.get('stage')}"
                )
        return 2 if outcome.status == "failed" else 0
    except (ValueError, OSError, DataSourceError) as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
