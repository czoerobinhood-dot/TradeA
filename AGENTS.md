# Project coordination

- 严格检查每个实施步骤和最终结果。
- 本地交互式选股器的唯一入口是 `http://127.0.0.1:8765/`，必须使用 `run.cmd` 启动。
- 本地服务固定使用 `config.example.json`、`.cache/akshare` 和 `reports`，默认手动刷新；不要另开端口或第二个选股器进程。
- `reports/latest.html` 是本地页面的唯一报告源。不要让多个任务同时扫描或写入该文件。
- `site/index.html` 仅是 Cloudflare 只读发布产物。需要更新时运行 `export_pages.cmd`，不要把 `python -m http.server` 静态预览作为用户的本地入口；临时预览完成后必须停止。
- Cloudflare 生产发布统一使用 `publish_pages.cmd`；它只读取 `reports/latest.html`，不会触发扫描，并必须通过 `site/release.json` 与线上回读校验。
- 修改共享文件前先检查当前工作区变更，并保留其他任务已完成的内容。完成后运行相关测试和服务健康检查。
