from __future__ import annotations

import html
import json
import socket
import threading
import time
from datetime import datetime, timedelta
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from ashare_screener.config import ScreenConfig
from ashare_screener.pipeline import Screener
from ashare_screener.provider import AkshareProvider
from ashare_screener.report import write_reports


LIVE_SUBTITLE = '<p class="subtle">点击“重新扫描”按钮后抓取数据并执行筛选</p>'
LIVE_STATUS_STYLE = """
.live-summary { display:flex; flex-wrap:wrap; align-items:center; gap:7px; }
.live-indicator { flex:0 0 8px; width:8px; height:8px; border-radius:50%; background:var(--green); }
.live-indicator.running { background:var(--blue); animation:live-pulse 1.2s ease-in-out infinite; }
.live-indicator.error { background:var(--red); }
.live-status-detail { color:var(--muted); }
@keyframes live-pulse { 50% { opacity:0.35; } }
"""
LIVE_STATUS_SCRIPT = """<script>
(() => {
  const initialRevision = __REPORT_REVISION__;
  const indicator = document.getElementById("live-indicator");
  const statusText = document.getElementById("live-status-text");
  const statusDetail = document.getElementById("live-status-detail");
  const scrollKey = "ashare-screener:live-scroll:v1";
  let navigating = false;

  try {
    const savedScroll = sessionStorage.getItem(scrollKey);
    if (savedScroll !== null) {
      sessionStorage.removeItem(scrollKey);
      requestAnimationFrame(() => window.scrollTo(0, Number(savedScroll) || 0));
    }
  } catch (error) {
    // Live updates still work when browser storage is unavailable.
  }

  const setStatus = (state, text, detail) => {
    indicator.classList.toggle("running", state === "running");
    indicator.classList.toggle("error", state === "error");
    statusText.textContent = text;
    statusDetail.textContent = detail;
  };

  const timeLabel = (value) => {
    if (!value) return "";
    const parsed = new Date(value);
    return Number.isNaN(parsed.getTime())
      ? ""
      : parsed.toLocaleTimeString("zh-CN", { hour12: false });
  };

  const pollHealth = async () => {
    try {
      const response = await fetch("/health", {
        cache: "no-store",
        headers: { Accept: "application/json" },
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const health = await response.json();
      statusDetail.title = "";
      if (health.scan_running) {
        setStatus("running", "正在更新行情", "当前页面继续显示上一版报告");
      } else if (health.last_error) {
        setStatus("error", "上次更新失败", "已保留上一版报告");
        statusDetail.title = health.last_error;
      } else if (health.auto_refresh_seconds <= 0) {
        setStatus("", "手动更新模式", "点击“重新扫描”获取最新数据");
      } else {
        const nextTime = timeLabel(health.next_auto_refresh_at);
        setStatus("", "本地自动更新", nextTime ? `下次 ${nextTime}` : "等待调度");
      }

      if (
        !navigating
        && !health.scan_running
        && health.report_revision
        && health.report_revision !== initialRevision
      ) {
        navigating = true;
        try { sessionStorage.setItem(scrollKey, String(window.scrollY)); } catch (error) {}
        window.location.replace(`/report?revision=${encodeURIComponent(health.report_revision)}`);
      }
    } catch (error) {
      setStatus("error", "状态连接中断", "本地服务不可用或正在停止");
    }
  };

  pollHealth();
  window.setInterval(pollHealth, 10000);
})();
</script>"""


def _interval_label(seconds: int) -> str:
    if seconds <= 0:
        return "自动更新已关闭"
    if seconds % 60 == 0:
        return f"每 {seconds // 60} 分钟"
    return f"每 {seconds} 秒"


def inject_live_mode(
    page: str,
    *,
    report_revision: str | None,
    auto_refresh_seconds: int,
) -> str:
    if auto_refresh_seconds > 0:
        status_text = "本地自动更新"
        interval_text = f"{_interval_label(auto_refresh_seconds)}刷新实时行情"
    else:
        status_text = "手动更新模式"
        interval_text = "点击右侧“重新扫描”"
    live_subtitle = (
        '<p class="subtle live-summary" role="status" aria-live="polite">'
        '<span id="live-indicator" class="live-indicator" aria-hidden="true"></span>'
        f'<span id="live-status-text">{status_text}</span><span>· {interval_text}</span>'
        '<span id="live-status-detail" class="live-status-detail">等待状态</span></p>'
    )
    replacements = (
        (LIVE_SUBTITLE, live_subtitle),
        ("</style>", f"{LIVE_STATUS_STYLE}</style>"),
        (
            "</body>",
            LIVE_STATUS_SCRIPT.replace(
                "__REPORT_REVISION__",
                json.dumps(report_revision, ensure_ascii=False),
            )
            + "\n</body>",
        ),
    )
    for original, replacement in replacements:
        if page.count(original) != 1:
            raise ValueError(f"报告格式不符合本地实时模式预期: {original}")
        page = page.replace(original, replacement, 1)
    return page


class ScanState:
    def __init__(
        self,
        *,
        config_path: Path,
        output_dir: Path,
        cache_dir: Path,
        report_limit: int | None,
        cooldown_seconds: int,
        auto_refresh_seconds: int,
        use_concepts: bool,
        use_fund_flow: bool,
    ) -> None:
        self.config_path = config_path
        self.output_dir = output_dir
        self.cache_dir = cache_dir
        self.report_limit = report_limit
        self.cooldown_seconds = cooldown_seconds
        self.auto_refresh_seconds = auto_refresh_seconds
        self.use_concepts = use_concepts
        self.use_fund_flow = use_fund_flow
        self.lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._auto_refresh_thread: threading.Thread | None = None
        self.last_completed_monotonic = 0.0
        self.last_error: str | None = None
        self.last_status: str | None = None
        self.last_started_at: str | None = None
        self.last_completed_at: str | None = None
        self.last_success_at: str | None = None
        self.last_trigger: str | None = None
        self.next_auto_refresh_at: str | None = None

    @property
    def report_path(self) -> Path:
        return self.output_dir / "latest.html"

    def report_revision(self) -> str | None:
        try:
            stat = self.report_path.stat()
        except OSError:
            return None
        return f"{stat.st_mtime_ns}:{stat.st_size}"

    def refresh(
        self,
        *,
        force: bool = False,
        refresh_realtime: bool = False,
        trigger: str = "page",
    ) -> Path:
        with self.lock:
            elapsed = time.monotonic() - self.last_completed_monotonic
            if (
                not force
                and self.report_path.exists()
                and self.last_completed_monotonic > 0
                and elapsed < self.cooldown_seconds
            ):
                return self.report_path

            with self._state_lock:
                self.last_error = None
                self.last_started_at = datetime.now().isoformat(timespec="seconds")
                self.last_trigger = trigger
            try:
                config = ScreenConfig.from_file(self.config_path)
                if self.report_limit is not None:
                    config.report_limit = self.report_limit
                trigger_text = {
                    "automatic": "后台自动更新",
                    "manual": "主动强制刷新",
                    "page": "页面刷新",
                }.get(trigger, trigger)
                print(f"[web] {trigger_text}触发新一轮选股", flush=True)
                provider = AkshareProvider(
                    cache_dir=self.cache_dir,
                    cache_minutes=config.cache_minutes,
                    refresh=force,
                    refresh_realtime=force or refresh_realtime,
                )
                outcome = Screener(
                    provider,
                    config,
                    progress=lambda message: print(f"[web] {message}", flush=True),
                    use_concepts=self.use_concepts,
                    use_fund_flow=self.use_fund_flow,
                ).run()
                write_reports(outcome, self.output_dir, config.report_limit)
                with self._state_lock:
                    self.last_status = outcome.status
                    self.last_success_at = datetime.now().isoformat(timespec="seconds")
                print(
                    f"[web] 扫描完成: {len(outcome.candidates)} 只有效候选, "
                    f"状态 {outcome.status}",
                    flush=True,
                )
            except Exception as exc:
                with self._state_lock:
                    self.last_error = str(exc)
                print(f"[web] 扫描失败: {exc}", flush=True)
                if not self.report_path.exists():
                    raise
            finally:
                self.last_completed_monotonic = time.monotonic()
                with self._state_lock:
                    self.last_completed_at = datetime.now().isoformat(timespec="seconds")
            return self.report_path

    def start_auto_refresh(self) -> None:
        if self.auto_refresh_seconds <= 0:
            return
        if self._auto_refresh_thread and self._auto_refresh_thread.is_alive():
            return
        self._stop_event.clear()
        self._set_next_auto_refresh()
        self._auto_refresh_thread = threading.Thread(
            target=self._auto_refresh_loop,
            name="ashare-auto-refresh",
            daemon=True,
        )
        self._auto_refresh_thread.start()

    def stop_auto_refresh(self) -> None:
        self._stop_event.set()
        thread = self._auto_refresh_thread
        if thread and thread.is_alive():
            thread.join(timeout=1.0)
        with self._state_lock:
            self.next_auto_refresh_at = None

    def _set_next_auto_refresh(self) -> None:
        next_refresh = datetime.now() + timedelta(seconds=self.auto_refresh_seconds)
        with self._state_lock:
            self.next_auto_refresh_at = next_refresh.isoformat(timespec="seconds")

    def _auto_refresh_loop(self) -> None:
        while not self._stop_event.wait(self.auto_refresh_seconds):
            with self._state_lock:
                self.next_auto_refresh_at = None
            try:
                self.refresh(
                    refresh_realtime=True,
                    trigger="automatic",
                )
            except Exception:
                # refresh() already records and logs the failure.
                pass
            finally:
                if not self._stop_event.is_set():
                    self._set_next_auto_refresh()

    def health(self) -> dict[str, object]:
        with self._state_lock:
            state = {
                "status": self.last_status,
                "last_error": self.last_error,
                "last_started_at": self.last_started_at,
                "last_completed_at": self.last_completed_at,
                "last_success_at": self.last_success_at,
                "last_trigger": self.last_trigger,
                "next_auto_refresh_at": self.next_auto_refresh_at,
            }
        return {
            **state,
            "report_exists": self.report_path.exists(),
            "report_revision": self.report_revision(),
            "scan_running": self.lock.locked(),
            "auto_refresh_seconds": self.auto_refresh_seconds,
        }

    def rendered_report(self) -> bytes:
        if not self.report_path.exists():
            raise FileNotFoundError("尚未生成报告")
        page = self.report_path.read_text(encoding="utf-8")
        return inject_live_mode(
            page,
            report_revision=self.report_revision(),
            auto_refresh_seconds=self.auto_refresh_seconds,
        ).encode("utf-8")


class RefreshingReportServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def server_bind(self) -> None:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(
                socket.SOL_SOCKET,
                socket.SO_EXCLUSIVEADDRUSE,
                1,
            )
        super().server_bind()

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
            force = (
                parsed.path == "/refresh"
                or query.get("force", ["0"])[0] == "1"
            )
            scan = force or not self.server.scan_state.report_path.exists()
            self._serve_report(scan=scan, force=force)
            return
        self.send_error(HTTPStatus.NOT_FOUND, "Not found")

    def _serve_report(self, *, scan: bool, force: bool) -> None:
        try:
            path = (
                self.server.scan_state.refresh(
                    force=force,
                    refresh_realtime=True,
                    trigger="manual" if force else "page",
                )
                if scan
                else self.server.scan_state.report_path
            )
            if not path.exists():
                raise FileNotFoundError("尚未生成报告")
            self._send_bytes(
                self.server.scan_state.rendered_report(),
                "text/html; charset=utf-8",
            )
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
    auto_refresh_seconds: int = 0,
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
        auto_refresh_seconds=auto_refresh_seconds,
        use_concepts=use_concepts,
        use_fund_flow=use_fund_flow,
    )
    server = RefreshingReportServer((host, port), state)
    print(f"选股器已启动: http://{host}:{port}/", flush=True)
    print(
        "手动模式：点击页面右上角“重新扫描”按钮获取最新数据。",
        flush=True,
    )
    if auto_refresh_seconds > 0:
        print(
            f"本地实时模式已启用：{_interval_label(auto_refresh_seconds)}自动刷新行情。",
            flush=True,
        )
    else:
        print("本地自动刷新已关闭。", flush=True)
    state.start_auto_refresh()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n选股器服务已停止。", flush=True)
    finally:
        state.stop_auto_refresh()
        server.server_close()
