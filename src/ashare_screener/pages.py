from __future__ import annotations

from pathlib import Path


LIVE_TITLE = "<title>A股全市场形态筛选报告</title>"
READ_ONLY_TITLE = "<title>A股全市场形态筛选报告（只读）</title>"
LIVE_SUBTITLE = "<p class=\"subtle\">浏览器刷新本页会重新抓取数据并执行筛选</p>"
READ_ONLY_SUBTITLE = (
    "<p class=\"subtle\">Cloudflare Pages 只读快照；数据以报告生成时间为准</p>"
)
LIVE_ACTION = '<a class="refresh" href="/?force=1">重新扫描</a>'
READ_ONLY_ACTION = '<span class="subtle">只读发布</span>'
MEMBER_STYLESHEET = '<link rel="stylesheet" href="/member.css">'
MEMBER_SCRIPT = '<script src="/member.js" defer></script>'

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
"""


def export_pages_report(
    source: str | Path = "reports/latest.html",
    output_dir: str | Path = "site",
) -> dict[str, Path]:
    source_path = Path(source)
    target_dir = Path(output_dir)
    page = source_path.read_text(encoding="utf-8")

    replacements = (
        (LIVE_TITLE, READ_ONLY_TITLE),
        (LIVE_SUBTITLE, READ_ONLY_SUBTITLE),
        (LIVE_ACTION, READ_ONLY_ACTION),
    )
    for original, replacement in replacements:
        if page.count(original) != 1:
            raise ValueError(f"报告格式不符合静态发布预期: {original}")
        page = page.replace(original, replacement, 1)

    if page.count("</head>") != 1 or page.count("</body>") != 1:
        raise ValueError("报告格式不符合成员前端注入预期")
    page = page.replace("</head>", f"{MEMBER_STYLESHEET}\n</head>", 1)
    page = page.replace("</body>", f"{MEMBER_SCRIPT}\n</body>", 1)

    target_dir.mkdir(parents=True, exist_ok=True)
    index_path = target_dir / "index.html"
    headers_path = target_dir / "_headers"
    index_path.write_text(page, encoding="utf-8")
    headers_path.write_text(PAGES_HEADERS, encoding="ascii")
    return {"index": index_path, "headers": headers_path}
