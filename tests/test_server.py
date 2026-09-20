from pathlib import Path
from types import SimpleNamespace
from http.client import HTTPConnection
import threading
from urllib.request import Request, urlopen

import pytest

import ashare_screener.server as server_module
from ashare_screener.cli import build_parser
from ashare_screener.local_owner import LocalOwnerResponse
from ashare_screener.server import (
    LOCAL_OWNER_CONFIG,
    LOCAL_OWNER_SCRIPT,
    LOCAL_OWNER_STYLESHEET,
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


def raw_http_request(
    host: str,
    port: int,
    method: str,
    path: str,
    headers: list[tuple[str, str]],
    body: bytes | None = None,
):
    connection = HTTPConnection(host, port, timeout=2)
    try:
        connection.putrequest(method, path, skip_host=True)
        for name, value in headers:
            connection.putheader(name, value)
        if body is not None:
            connection.putheader("Content-Length", str(len(body)))
        connection.endheaders(body)
        response = connection.getresponse()
        return response.status, response.headers, response.read()
    finally:
        connection.close()


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


def test_live_mode_injects_local_owner_runtime_only_when_enabled():
    plain = inject_live_mode(
        REPORT_PAGE,
        report_revision="123:456",
        auto_refresh_seconds=0,
    )
    owner = inject_live_mode(
        REPORT_PAGE,
        report_revision="123:456",
        auto_refresh_seconds=0,
        local_owner_enabled=True,
    )

    assert LOCAL_OWNER_STYLESHEET not in plain
    assert LOCAL_OWNER_CONFIG not in plain
    assert LOCAL_OWNER_SCRIPT not in plain
    assert owner.count(LOCAL_OWNER_STYLESHEET) == 1
    assert owner.count(LOCAL_OWNER_CONFIG) == 1
    assert owner.count(LOCAL_OWNER_SCRIPT) == 1
    assert owner.index(LOCAL_OWNER_CONFIG) < owner.index(LOCAL_OWNER_SCRIPT)


def test_serve_defaults_to_manual_refresh():
    args = build_parser().parse_args(["serve"])

    assert args.refresh_interval == 0


def test_default_owner_vars_path_uses_local_appdata_without_project_fallback(
    tmp_path, monkeypatch
):
    local_appdata = tmp_path / "LocalAppData"
    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / ".owner.vars").write_text("must not be used", encoding="utf-8")
    monkeypatch.chdir(project_root)

    monkeypatch.setenv("LOCALAPPDATA", str(local_appdata))
    assert server_module._default_owner_vars_path() == (
        local_appdata / "TradeA" / "owner.vars"
    )

    monkeypatch.delenv("LOCALAPPDATA")
    assert server_module._default_owner_vars_path() is None


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
            response_headers = response.headers
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=2)

    assert body == b"existing report"
    assert response_headers["X-Frame-Options"] == "DENY"
    assert response_headers["Content-Security-Policy"] == "frame-ancestors 'none'"


def test_local_owner_runtime_serves_assets_and_proxies_api(tmp_path):
    site_dir = tmp_path / "site"
    site_dir.mkdir()
    (site_dir / "member.css").write_text(".member{}", encoding="utf-8")
    (site_dir / "member.js").write_text("window.memberReady=true;", encoding="utf-8")

    class FakeOwnerProxy:
        connected = True

        def __init__(self):
            self.calls = []

        def request(self, method, path, body=None, content_type=None):
            self.calls.append((method, path, body, content_type))
            return LocalOwnerResponse(
                status=200,
                body=b'{"user":{"role":"admin"}}',
                content_type="application/json; charset=utf-8",
            )

    proxy = FakeOwnerProxy()
    state = make_state(tmp_path)
    state.site_dir = site_dir
    state.owner_proxy = proxy
    state.report_path.parent.mkdir(parents=True)
    state.report_path.write_text(REPORT_PAGE, encoding="utf-8")
    httpd = RefreshingReportServer(("127.0.0.1", 0), state)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = httpd.server_address
        with urlopen(f"http://{host}:{port}/", timeout=2) as response:
            page = response.read().decode("utf-8")
        with urlopen(f"http://{host}:{port}/member.js", timeout=2) as response:
            member_script = response.read()
        request_body = '{"code":"000001","name":"平安银行"}'.encode("utf-8")
        request = Request(
            f"http://{host}:{port}/api/favorites",
            data=request_body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Origin": f"http://{host}:{port}",
                "Sec-Fetch-Site": "same-origin",
            },
        )
        with urlopen(request, timeout=2) as response:
            api_payload = response.read()
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=2)

    assert LOCAL_OWNER_STYLESHEET in page
    assert LOCAL_OWNER_CONFIG in page
    assert LOCAL_OWNER_SCRIPT in page
    assert member_script == b"window.memberReady=true;"
    assert api_payload == b'{"user":{"role":"admin"}}'
    assert proxy.calls == [
        (
            "POST",
            "/api/favorites",
            request_body,
            "application/json",
        )
    ]


def test_local_owner_api_rejects_missing_wrong_or_cross_site_mutation_origin(
    tmp_path,
):
    class FakeOwnerProxy:
        connected = False

        def request(self, *args, **kwargs):
            raise AssertionError("foreign origin must not reach the owner proxy")

    state = make_state(tmp_path)
    state.owner_proxy = FakeOwnerProxy()
    httpd = RefreshingReportServer(("127.0.0.1", 0), state)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = httpd.server_address
        expected_host = f"127.0.0.1:{port}"
        rejected_headers = [
            [("Host", expected_host), ("Content-Type", "application/json")],
            [
                ("Host", expected_host),
                ("Content-Type", "application/json"),
                ("Origin", "https://evil.test"),
            ],
            [
                ("Host", expected_host),
                ("Content-Type", "application/json"),
                ("Origin", f"http://localhost:{port}"),
            ],
            [
                ("Host", expected_host),
                ("Content-Type", "application/json"),
                ("Origin", f"http://127.0.0.1:{port}"),
                ("Sec-Fetch-Site", "cross-site"),
            ],
        ]
        for headers in rejected_headers:
            status, _, _ = raw_http_request(
                host, port, "POST", "/api/favorites", headers, b"{}"
            )
            assert status == 403
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=2)


def test_local_owner_api_rejects_missing_duplicate_or_hostile_host_and_get_origin(
    tmp_path,
):
    class FakeOwnerProxy:
        connected = False

        def __init__(self):
            self.calls = []

        def request(self, *args, **kwargs):
            self.calls.append((args, kwargs))
            return LocalOwnerResponse(
                status=200,
                body=b'{"members":[]}',
                content_type="application/json; charset=utf-8",
            )

    state = make_state(tmp_path)
    proxy = FakeOwnerProxy()
    state.owner_proxy = proxy
    httpd = RefreshingReportServer(("127.0.0.1", 0), state)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = httpd.server_address
        expected_host = f"127.0.0.1:{port}"
        rejected_headers = [
            [],
            [("Host", "attacker.test")],
            [("Host", expected_host), ("Host", "attacker.test")],
            [("Host", f"localhost:{port}")],
            [("Host", expected_host), ("Origin", "https://attacker.test")],
            [
                ("Host", expected_host),
                ("Origin", f"http://127.0.0.1:{port}"),
                ("Origin", "https://attacker.test"),
            ],
            [("Host", expected_host), ("Sec-Fetch-Site", "cross-site")],
            [("Host", expected_host), ("Sec-Fetch-Site", "same-site")],
            [
                ("Host", expected_host),
                ("Sec-Fetch-Site", "same-origin"),
                ("Sec-Fetch-Site", "none"),
            ],
        ]
        for headers in rejected_headers:
            status, response_headers, _ = raw_http_request(
                host, port, "GET", "/api/members", headers
            )
            assert status == 403
            assert response_headers["X-Frame-Options"] == "DENY"
            assert (
                response_headers["Content-Security-Policy"]
                == "frame-ancestors 'none'"
            )
        assert proxy.calls == []

        valid_headers = [
            [("Host", expected_host)],
            [
                ("Host", expected_host),
                ("Origin", f"http://127.0.0.1:{port}"),
            ],
            [("Host", expected_host), ("Sec-Fetch-Site", "same-origin")],
            [("Host", expected_host), ("Sec-Fetch-Site", "none")],
        ]
        for headers in valid_headers:
            status, _, body = raw_http_request(
                host, port, "GET", "/api/members", headers
            )
            assert status == 200
            assert body == b'{"members":[]}'
        assert len(proxy.calls) == 4
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=2)


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
