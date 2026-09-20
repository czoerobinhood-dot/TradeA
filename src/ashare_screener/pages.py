from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen


LIVE_TITLE = "<title>A股全市场形态筛选报告</title>"
READ_ONLY_TITLE = "<title>A股全市场形态筛选报告（只读）</title>"
LIVE_SUBTITLE = "<p class=\"subtle\">点击“重新扫描”按钮后抓取数据并执行筛选</p>"
READ_ONLY_SUBTITLE = (
    "<p class=\"subtle\">Cloudflare Pages 只读快照；数据以报告生成时间为准</p>"
)
LIVE_ACTION = '<a class="refresh" href="/?force=1">重新扫描</a>'
READ_ONLY_ACTION = '<span class="subtle">只读发布</span>'
MEMBER_STYLESHEET = '<link rel="stylesheet" href="/member.css">'
MEMBER_SCRIPT = '<script src="/member.js" defer></script>'
RELEASE_MANIFEST_NAME = "release.json"
RELEASE_SCHEMA_VERSION = 1
PAGES_PROJECT_NAME = "TradeA"
PAGES_MODE = "cloudflare-pages-read-only"
PAGES_RUNTIME_ASSETS = ("_worker.js", "member.css", "member.js")
REPORT_GENERATED_PATTERN = re.compile(
    r'<span>生成时间\s*<b>([^<]+)</b></span>'
)

PAGES_HEADERS = """/*
  Cache-Control: public, max-age=300
  X-Content-Type-Options: nosniff
  Referrer-Policy: strict-origin-when-cross-origin
  X-Frame-Options: SAMEORIGIN
  Permissions-Policy: camera=(), microphone=(), geolocation=()

/api/*
  Cache-Control: no-store
  X-Content-Type-Options: nosniff
  Referrer-Policy: no-referrer

/release.json
  Cache-Control: no-store
  X-Content-Type-Options: nosniff
  Referrer-Policy: no-referrer
"""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _report_generated_at(page: str) -> str:
    matches = REPORT_GENERATED_PATTERN.findall(page)
    if len(matches) != 1:
        raise ValueError("报告格式不符合发布时间提取预期")
    return matches[0].strip()


def _require_runtime_assets(target_dir: Path) -> None:
    missing = [name for name in PAGES_RUNTIME_ASSETS if not (target_dir / name).is_file()]
    if missing:
        raise FileNotFoundError(f"Pages 运行文件缺失: {', '.join(missing)}")


def _write_text_atomic(path: Path, value: str, *, encoding: str) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_bytes(value.encode(encoding))
    temporary.replace(path)


def _release_manifest(
    *,
    source_path: Path,
    source_bytes: bytes,
    index_bytes: bytes,
    report_generated_at: str,
) -> dict[str, object]:
    return {
        "schema_version": RELEASE_SCHEMA_VERSION,
        "project": PAGES_PROJECT_NAME,
        "mode": PAGES_MODE,
        "report": {
            "source_file": source_path.name,
            "generated_at": report_generated_at,
            "sha256": _sha256(source_bytes),
            "bytes": len(source_bytes),
        },
        "site": {
            "index_sha256": _sha256(index_bytes),
            "index_bytes": len(index_bytes),
            "scan_trigger": False,
            "member_collaboration": True,
        },
    }


def export_pages_report(
    source: str | Path = "reports/latest.html",
    output_dir: str | Path = "site",
) -> dict[str, Path]:
    source_path = Path(source)
    target_dir = Path(output_dir)
    source_bytes = source_path.read_bytes()
    page = source_bytes.decode("utf-8")

    replacements = (
        (LIVE_TITLE, READ_ONLY_TITLE),
        (LIVE_SUBTITLE, READ_ONLY_SUBTITLE),
        (LIVE_ACTION, READ_ONLY_ACTION),
    )
    for original, replacement in replacements:
        if page.count(original) != 1:
            raise ValueError(f"报告格式不符合静态发布预期: {original}")
        page = page.replace(original, replacement, 1)
    report_generated_at = _report_generated_at(page)

    if page.count("</head>") != 1 or page.count("</body>") != 1:
        raise ValueError("报告格式不符合成员前端注入预期")
    if MEMBER_STYLESHEET in page or MEMBER_SCRIPT in page:
        raise ValueError("源报告已经包含成员前端，拒绝重复注入")
    page = page.replace("</head>", f"{MEMBER_STYLESHEET}\n</head>", 1)
    page = page.replace("</body>", f"{MEMBER_SCRIPT}\n</body>", 1)

    target_dir.mkdir(parents=True, exist_ok=True)
    _require_runtime_assets(target_dir)
    index_path = target_dir / "index.html"
    headers_path = target_dir / "_headers"
    manifest_path = target_dir / RELEASE_MANIFEST_NAME
    index_bytes = page.encode("utf-8")
    manifest = _release_manifest(
        source_path=source_path,
        source_bytes=source_bytes,
        index_bytes=index_bytes,
        report_generated_at=report_generated_at,
    )
    _write_text_atomic(index_path, page, encoding="utf-8")
    _write_text_atomic(headers_path, PAGES_HEADERS, encoding="ascii")
    _write_text_atomic(
        manifest_path,
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    verify_pages_export(source_path, target_dir)
    return {"index": index_path, "headers": headers_path, "release": manifest_path}


def verify_pages_export(
    source: str | Path = "reports/latest.html",
    output_dir: str | Path = "site",
) -> dict[str, object]:
    source_path = Path(source)
    target_dir = Path(output_dir)
    _require_runtime_assets(target_dir)

    source_bytes = source_path.read_bytes()
    source_page = source_bytes.decode("utf-8")
    index_path = target_dir / "index.html"
    manifest_path = target_dir / RELEASE_MANIFEST_NAME
    index_bytes = index_path.read_bytes()
    index_page = index_bytes.decode("utf-8")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    expected = _release_manifest(
        source_path=source_path,
        source_bytes=source_bytes,
        index_bytes=index_bytes,
        report_generated_at=_report_generated_at(source_page),
    )
    if manifest != expected:
        raise ValueError("Pages 发布清单与当前报告不一致，请重新运行 export_pages.cmd")

    required_markers = (
        READ_ONLY_TITLE,
        READ_ONLY_SUBTITLE,
        READ_ONLY_ACTION,
        MEMBER_STYLESHEET,
        MEMBER_SCRIPT,
    )
    if any(index_page.count(marker) != 1 for marker in required_markers):
        raise ValueError("Pages 页面缺少唯一的只读或成员前端标记")
    if LIVE_ACTION in index_page:
        raise ValueError("Pages 页面不得包含本地扫描入口")
    return manifest


def _fetch(url: str, *, accept: str, timeout: float) -> bytes:
    request = Request(
        url,
        headers={
            "Accept": accept,
            "Cache-Control": "no-cache",
            "User-Agent": "TradeA-Publish-Verify/1",
        },
    )
    with urlopen(request, timeout=timeout) as response:
        if response.status != 200:
            raise OSError(f"线上校验失败: HTTP {response.status} {url}")
        return response.read()


def verify_pages_deployment(
    output_dir: str | Path,
    base_url: str,
    *,
    timeout: float = 20.0,
) -> dict[str, object]:
    target_dir = Path(output_dir)
    local_manifest = json.loads(
        (target_dir / RELEASE_MANIFEST_NAME).read_text(encoding="utf-8")
    )
    fingerprint = str(local_manifest["report"]["sha256"])
    root = base_url.rstrip("/")
    remote_manifest = json.loads(
        _fetch(
            f"{root}/{RELEASE_MANIFEST_NAME}?report={quote(fingerprint)}",
            accept="application/json",
            timeout=timeout,
        ).decode("utf-8")
    )
    if remote_manifest != local_manifest:
        raise ValueError("线上 release.json 与本地发布清单不一致")

    remote_index = _fetch(
        f"{root}/?report={quote(fingerprint)}",
        accept="text/html",
        timeout=timeout,
    )
    if _sha256(remote_index) != local_manifest["site"]["index_sha256"]:
        raise ValueError("线上首页与本地 Pages 导出不一致")

    auth_status = json.loads(
        _fetch(
            f"{root}/api/auth/status",
            accept="application/json",
            timeout=timeout,
        ).decode("utf-8")
    )
    if auth_status.get("setupReady") is not True:
        raise ValueError("线上成员服务尚未配置 SETUP_TOKEN")
    if not isinstance(auth_status.get("setupRequired"), bool):
        raise ValueError("线上成员服务状态响应无效")
    return {
        "url": root,
        "report_generated_at": local_manifest["report"]["generated_at"],
        "report_sha256": fingerprint,
        "setup_required": auth_status["setupRequired"],
        "setup_ready": auth_status["setupReady"],
    }
