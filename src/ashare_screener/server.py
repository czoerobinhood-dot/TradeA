from __future__ import annotations

import html
import json
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from ashare_screener.config import ScreenConfig
from ashare_screener.pipeline import Screener
from ashare_screener.provider import AkshareProvider
from ashare_screener.report import write_reports


class ScanState:
    def __init__(
        self,
        *,
        config_path: Path,
        output_dir: Path,
        cache_dir: Path,
        report_limit: int | None,
        cooldown_seconds: int,
        use_concepts: bool,
        use_fund_flow: bool,
    ) -> None:
        self.config_path = config_path
        self.output_dir = output_dir
        self.cache_dir = cache_dir
        self.report_limit = report_limit
        self.cooldown_seconds = cooldown_seconds
        self.use_concepts = use_concepts
        self.use_fund_flow = use_fund_flow
        self.lock = threading.Lock()
        self.last_completed_monotonic = 0.0
        self.last_error: str | None = None
        self.last_status: str | None = None

    @property
    def report_path(self) -> Path:
        return self.output_dir / "latest.html"

    def refresh(self, *, force: bool = False) -> Path:
        with self.lock:
            elapsed = time.monotonic() - self.last_completed_monotonic
            if (
                not force
                and self.report_path.exists()
                and self.last_completed_monotonic > 0
                and elapsed < self.cooldown_seconds
            ):
                return self.report_path

            self.last_error = None
            try:
                config = ScreenConfig.from_file(self.config_path)
                if self.report_limit is not None:
                    config.report_limit = self.report_limit
                print("[web] 浏览器刷新触发新一轮选股", flush=True)
                provider = AkshareProvider(
                    cache_dir=self.cache_dir,
                    cache_minutes=config.cache_minutes,
                    refresh=force,
                )
                outcome = Screener(
                    provider,
                    config,
                    progress=lambda message: print(f"[web] {message}", flush=True),
                    use_concepts=self.use_concepts,
                    use_fund_flow=self.use_fund_flow,
                ).run()
                write_reports(outcome, self.output_dir, config.report_limit)
                self.last_status = outcome.status
                print(
                    f"[web] 扫描完成: {len(outcome.candidates)} 只有效候选, "
                    f"状态 {outcome.status}",
                    flush=True,
                )
            except Exception as exc:
                self.last_error = str(exc)
                print(f"[web] 扫描失败: {exc}", flush=True)
                if not self.report_path.exists():
                    raise
            finally:
                self.last_completed_monotonic = time.monotonic()
            return self.report_path

    def health(self) -> dict[str, object]:
        return {
            "status": self.last_status,
            "last_error": self.last_error,
            "report_exists": self.report_path.exists(),
            "scan_running": self.lock.locked(),
        }


class RefreshingReportServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], state: ScanState) -> None:
        self.scan_state = state
        super().__init__(address, RefreshingReportHandler)


class RefreshingReportHandler(BaseHTTPRequestHandler):
    server: RefreshingReportServer

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
        parsed = urlparse(self.path)
        if parsed.path == "/favicon.ico":
            self.send_response(HTTPStatus.NO_CONTENT)
            self.end_headers()
            return
        if parsed.path == "/health":
            payload = json.dumps(
                self.server.scan_state.health(), ensure_ascii=False
            ).encode("utf-8")
            self._send_bytes(payload, "application/json; charset=utf-8")
            return
        if parsed.path == "/report":
            self._serve_report(scan=False, force=False)
            return
        if parsed.path in {"/", "/latest.html", "/refresh"}:
            query = parse_qs(parsed.query)
            force = query.get("force", ["0"])[0] == "1"
            self._serve_report(scan=True, force=force)
            return
        self.send_error(HTTPStatus.NOT_FOUND, "Not found")

    def _serve_report(self, *, scan: bool, force: bool) -> None:
        try:
            path = (
                self.server.scan_state.refresh(force=force)
                if scan
                else self.server.scan_state.report_path
            )
            if not path.exists():
                raise FileNotFoundError("尚未生成报告")
            self._send_bytes(path.read_bytes(), "text/html; charset=utf-8")
        except Exception as exc:
            body = (
                "<!doctype html><meta charset='utf-8'><title>扫描失败</title>"
                "<style>body{font-family:Microsoft YaHei,sans-serif;padding:32px;}"
                "pre{white-space:pre-wrap;background:#f5f5f5;padding:12px;}</style>"
                f"<h1>扫描失败</h1><pre>{html.escape(str(exc))}</pre>"
                "<p><a href='/'>重试</a></p>"
            ).encode("utf-8")
            self._send_bytes(
                body, "text/html; charset=utf-8", status=HTTPStatus.BAD_GATEWAY
            )

    def _send_bytes(
        self,
        payload: bytes,
        content_type: str,
        *,
        status: HTTPStatus = HTTPStatus.OK,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: object) -> None:
        print(f"[web] {self.address_string()} {format % args}", flush=True)


def serve(
    *,
    config_path: str | Path,
    output_dir: str | Path,
    cache_dir: str | Path,
    host: str = "127.0.0.1",
    port: int = 8765,
    cooldown_seconds: int = 30,
    report_limit: int | None = None,
    use_concepts: bool = True,
    use_fund_flow: bool = True,
) -> None:
    state = ScanState(
        config_path=Path(config_path).resolve(),
        output_dir=Path(output_dir).resolve(),
        cache_dir=Path(cache_dir).resolve(),
        report_limit=report_limit,
        cooldown_seconds=cooldown_seconds,
        use_concepts=use_concepts,
        use_fund_flow=use_fund_flow,
    )
    server = RefreshingReportServer((host, port), state)
    print(f"选股器已启动: http://{host}:{port}/", flush=True)
    print(
        f"刷新页面会重新扫描；{cooldown_seconds} 秒内重复刷新复用刚生成的结果。",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n选股器服务已停止。", flush=True)
    finally:
        server.server_close()
