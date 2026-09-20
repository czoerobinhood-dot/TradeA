import hashlib
import json
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
    RELEASE_MANIFEST_NAME,
    export_pages_report,
    verify_pages_export,
)


def make_runtime_assets(output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    for name in ("_worker.js", "member.css", "member.js"):
        (output / name).write_text(f"/* {name} */", encoding="utf-8")


def test_export_pages_report_removes_scan_controls(tmp_path: Path):
    source = tmp_path / "latest.html"
    source.write_text(
        f"<!doctype html><html><head>{LIVE_TITLE}</head><body>"
        f"{LIVE_SUBTITLE}{LIVE_ACTION}"
        "<div class=\"summary\"><span>生成时间 <b>2026-09-19 22:26:55</b></span></div>"
        "<p>报告内容</p></body></html>",
        encoding="utf-8",
    )
    output = tmp_path / "site"
    make_runtime_assets(output)

    paths = export_pages_report(source, output)
    page = paths["index"].read_text(encoding="utf-8")
    headers = paths["headers"].read_text(encoding="ascii")
    release = json.loads(paths["release"].read_text(encoding="utf-8"))

    assert READ_ONLY_TITLE in page
    assert READ_ONLY_SUBTITLE in page
    assert READ_ONLY_ACTION in page
    assert LIVE_ACTION not in page
    assert MEMBER_STYLESHEET in page
    assert MEMBER_SCRIPT in page
    assert "X-Content-Type-Options: nosniff" in headers
    assert "/api/*" in headers
    assert "Cache-Control: no-store" in headers
    assert f"/{RELEASE_MANIFEST_NAME}" in headers
    assert release["report"]["generated_at"] == "2026-09-19 22:26:55"
    assert release["report"]["sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert release["site"]["index_sha256"] == hashlib.sha256(
        paths["index"].read_bytes()
    ).hexdigest()
    assert release["site"]["scan_trigger"] is False
    assert release["site"]["member_collaboration"] is True
    assert verify_pages_export(source, output) == release


def test_export_pages_report_rejects_unexpected_html(tmp_path: Path):
    source = tmp_path / "latest.html"
    source.write_text("<!doctype html><title>未知报告</title>", encoding="utf-8")

    with pytest.raises(ValueError, match="报告格式不符合静态发布预期"):
        export_pages_report(source, tmp_path / "site")


def test_verify_pages_export_rejects_stale_report(tmp_path: Path):
    source = tmp_path / "latest.html"
    source.write_text(
        f"<!doctype html><html><head>{LIVE_TITLE}</head><body>"
        f"{LIVE_SUBTITLE}{LIVE_ACTION}"
        "<div class=\"summary\"><span>生成时间 <b>2026-09-19 22:26:55</b></span></div>"
        "</body></html>",
        encoding="utf-8",
    )
    output = tmp_path / "site"
    make_runtime_assets(output)
    export_pages_report(source, output)
    source.write_text(source.read_text(encoding="utf-8") + "<!-- newer -->", encoding="utf-8")

    with pytest.raises(ValueError, match="发布清单与当前报告不一致"):
        verify_pages_export(source, output)


def test_export_pages_report_requires_member_runtime_assets(tmp_path: Path):
    source = tmp_path / "latest.html"
    source.write_text(
        f"<!doctype html><html><head>{LIVE_TITLE}</head><body>"
        f"{LIVE_SUBTITLE}{LIVE_ACTION}"
        "<span>生成时间 <b>2026-09-19 22:26:55</b></span>"
        "</body></html>",
        encoding="utf-8",
    )

    with pytest.raises(FileNotFoundError, match="Pages 运行文件缺失"):
        export_pages_report(source, tmp_path / "site")
