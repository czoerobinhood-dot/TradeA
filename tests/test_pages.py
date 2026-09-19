from pathlib import Path

import pytest

from ashare_screener.pages import (
    LIVE_ACTION,
    LIVE_SUBTITLE,
    LIVE_TITLE,
    READ_ONLY_ACTION,
    READ_ONLY_SUBTITLE,
    READ_ONLY_TITLE,
    MEMBER_SCRIPT,
    MEMBER_STYLESHEET,
    export_pages_report,
)


def test_export_pages_report_removes_scan_controls(tmp_path: Path):
    source = tmp_path / "latest.html"
    source.write_text(
        f"<!doctype html><html><head>{LIVE_TITLE}</head><body>"
        f"{LIVE_SUBTITLE}{LIVE_ACTION}<p>报告内容</p></body></html>",
        encoding="utf-8",
    )

    paths = export_pages_report(source, tmp_path / "site")
    page = paths["index"].read_text(encoding="utf-8")
    headers = paths["headers"].read_text(encoding="ascii")

    assert READ_ONLY_TITLE in page
    assert READ_ONLY_SUBTITLE in page
    assert READ_ONLY_ACTION in page
    assert LIVE_ACTION not in page
    assert MEMBER_STYLESHEET in page
    assert MEMBER_SCRIPT in page
    assert "X-Content-Type-Options: nosniff" in headers
    assert "/api/*" in headers
    assert "Cache-Control: no-store" in headers


def test_export_pages_report_rejects_unexpected_html(tmp_path: Path):
    source = tmp_path / "latest.html"
    source.write_text("<!doctype html><title>未知报告</title>", encoding="utf-8")

    with pytest.raises(ValueError, match="报告格式不符合静态发布预期"):
        export_pages_report(source, tmp_path / "site")
