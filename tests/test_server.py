from pathlib import Path
from types import SimpleNamespace
import threading
from urllib.request import urlopen

import pytest

import ashare_screener.server as server_module
from ashare_screener.cli import build_parser
from ashare_screener.server import (
    LIVE_SUBTITLE,
    RefreshingReportServer,
    ScanState,
    inject_live_mode,
)


REPORT_PAGE = (
    "<!doctype html><html><head><style>body { color:#222; }</style></head><body>"
    f"{LIVE_SUBTITLE}"
    '<a class="refresh" href="/?force=1">重新扫描</a>'
    "<main>报告内容</main></body></html>"
)


def make_state(tmp_path: Path, *, auto_refresh_seconds: int = 0) -> ScanState:
    return ScanState(
        config_path=tmp_path / "config.json",
        output_dir=tmp_path / "reports",
        cache_dir=tmp_path / "cache",
        report_limit=30,
        cooldown_seconds=30,
        auto_refresh_seconds=auto_refresh_seconds,
        use_concepts=True,
        use_fund_flow=True,
    )


def test_live_mode_injection_defaults_to_manual_refresh():
    page = inject_live_mode(
        REPORT_PAGE,
        report_revision="123:456",
        auto_refresh_seconds=0,
    )

    assert "手动更新模式" in page
    assert "点击右侧“重新扫描”" in page
    assert 'href="/?force=1">重新扫描</a>' in page
    assert 'fetch("/health"' in page
    assert "window.setInterval(pollHealth, 10000)" in page
    assert 'window.location.replace(`/report?revision=' in page
    assert 'const initialRevision = "123:456"' in page


def test_serve_defaults_to_manual_refresh():
    args = build_parser().parse_args(["serve"])

    assert args.refresh_interval == 0


def test_scan_state_refreshes_realtime_cache_without_forcing_slow_data(
    tmp_path,
    monkeypatch,
):
    state = make_state(tmp_path)
    providers = []
    config = SimpleNamespace(cache_minutes=180, report_limit=30)

    class FakeProvider:
        def __init__(self, **kwargs):
            providers.append(kwargs)

    class FakeScreener:
        def __init__(self, provider, config, **kwargs):
            pass

        def run(self):
            return SimpleNamespace(status="ok", candidates=[])

    def fake_write_reports(outcome, output_dir, report_limit):
        output = Path(output_dir)
        output.mkdir(parents=True, exist_ok=True)
        latest = output / "latest.html"
        latest.write_text(REPORT_PAGE, encoding="utf-8")
        return {"latest": latest}

    monkeypatch.setattr(
        server_module.ScreenConfig,
        "from_file",
        staticmethod(lambda path: config),
    )
    monkeypatch.setattr(server_module, "AkshareProvider", FakeProvider)
    monkeypatch.setattr(server_module, "Screener", FakeScreener)
    monkeypatch.setattr(server_module, "write_reports", fake_write_reports)

    state.refresh(refresh_realtime=True, trigger="automatic")
    state.refresh(force=True, refresh_realtime=True, trigger="manual")

    assert providers[0]["refresh"] is False
    assert providers[0]["refresh_realtime"] is True
    assert providers[1]["refresh"] is True
    assert providers[1]["refresh_realtime"] is True
    assert state.health()["last_trigger"] == "manual"
    assert "手动更新模式" in state.rendered_report().decode("utf-8")


def test_manual_mode_does_not_start_scheduler(tmp_path):
    state = make_state(tmp_path)

    state.start_auto_refresh()

    assert state.health()["auto_refresh_seconds"] == 0
    assert state.health()["next_auto_refresh_at"] is None
    assert state._auto_refresh_thread is None


def test_auto_refresh_scheduler_runs_without_overlapping_page_logic(tmp_path):
    state = make_state(tmp_path, auto_refresh_seconds=0.02)
    triggered = threading.Event()
    calls = []

    def fake_refresh(**kwargs):
        calls.append(kwargs)
        triggered.set()
        return state.report_path

    state.refresh = fake_refresh
    state.start_auto_refresh()
    try:
        assert triggered.wait(0.5)
    finally:
        state.stop_auto_refresh()

    assert calls[0] == {
        "refresh_realtime": True,
        "trigger": "automatic",
    }
    assert state.health()["next_auto_refresh_at"] is None


def test_failed_refresh_keeps_existing_report(tmp_path, monkeypatch):
    state = make_state(tmp_path)
    state.report_path.parent.mkdir(parents=True)
    state.report_path.write_text(REPORT_PAGE, encoding="utf-8")

    monkeypatch.setattr(
        server_module.ScreenConfig,
        "from_file",
        staticmethod(lambda path: SimpleNamespace(cache_minutes=180, report_limit=30)),
    )

    class FailingProvider:
        def __init__(self, **kwargs):
            raise RuntimeError("upstream unavailable")

    monkeypatch.setattr(server_module, "AkshareProvider", FailingProvider)

    result = state.refresh(force=True, trigger="manual")

    assert result == state.report_path
    assert state.report_path.read_text(encoding="utf-8") == REPORT_PAGE
    assert state.health()["last_error"] == "upstream unavailable"


def test_refresh_endpoint_keeps_manual_force_refresh(tmp_path):
    report_path = tmp_path / "latest.html"
    report_path.write_text(REPORT_PAGE, encoding="utf-8")

    class FakeState:
        def __init__(self):
            self.report_path = report_path
            self.calls = []

        def refresh(self, **kwargs):
            self.calls.append(kwargs)
            return self.report_path

        def rendered_report(self):
            return b"manual refresh complete"

    state = FakeState()
    httpd = RefreshingReportServer(("127.0.0.1", 0), state)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = httpd.server_address
        with urlopen(f"http://{host}:{port}/refresh", timeout=2) as response:
            body = response.read()
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=2)

    assert body == b"manual refresh complete"
    assert state.calls == [
        {
            "force": True,
            "refresh_realtime": True,
            "trigger": "manual",
        }
    ]


def test_root_serves_existing_report_without_starting_scan(tmp_path):
    report_path = tmp_path / "latest.html"
    report_path.write_text(REPORT_PAGE, encoding="utf-8")

    class FakeState:
        def __init__(self):
            self.report_path = report_path

        def refresh(self, **kwargs):
            raise AssertionError(f"root must not start a scan: {kwargs}")

        def rendered_report(self):
            return b"existing report"

    httpd = RefreshingReportServer(("127.0.0.1", 0), FakeState())
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = httpd.server_address
        with urlopen(f"http://{host}:{port}/", timeout=2) as response:
            body = response.read()
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=2)

    assert body == b"existing report"


def test_local_server_rejects_a_second_listener_on_the_same_port():
    state = SimpleNamespace()
    first = RefreshingReportServer(("127.0.0.1", 0), state)
    port = first.server_address[1]
    try:
        with pytest.raises(OSError):
            second = RefreshingReportServer(("127.0.0.1", port), state)
            second.server_close()
    finally:
        first.server_close()
