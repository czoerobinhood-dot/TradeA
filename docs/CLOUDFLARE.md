# Cloudflare 接入说明

## 当前生产结构

本项目的扫描过程依赖 Python、Pandas、NumPy 和 AkShare，并需要访问第三方行情接口。Cloudflare Pages 只托管生成后的静态报告；Pages Worker 只处理成员 API 和静态资源转发，不能运行 Python 扫描后端。

当前生产地址为 [https://tradea-3al.pages.dev/](https://tradea-3al.pages.dev/)，Pages 项目名为 `tradea`，采用 Direct Upload。Python 扫描只在可信本地主机运行。

统一数据流：

```text
本地扫描 -> reports/latest.html -> export_pages.cmd
         -> site/index.html + site/release.json
         -> publish_pages.cmd -> Cloudflare Pages + D1 成员协作
```

`reports/latest.html` 是唯一报告源。`site/index.html` 是派生产物，不得反向覆盖本地报告。`site/release.json` 记录报告生成时间、源报告 SHA-256 和导出首页 SHA-256，用于避免把旧报告误报为已发布。

## 生产发布

首次安装固定版本的 Wrangler：

```powershell
npm install
```

完成扫描并人工确认 `reports/latest.html` 后执行：

```powershell
.\publish_pages.cmd
```

该命令严格按以下顺序执行：

1. 导出并离线校验 `site`。
2. 运行 Pages/D1 Python 测试与 Worker 测试。
3. 使用 `wrangler pages deploy` Direct Upload，项目名固定为 `tradea`。
4. 从生产地址回读 `release.json` 和首页，核对哈希。
5. 校验 `/api/auth/status`，确认成员服务仍连接。

该流程不会触发股票扫描，不会读取或输出 `.dev.vars` 中的初始化口令，也不会修改 D1 数据。

## 只读边界

Cloudflare 页面不会公开本地“重新扫描”入口。访问网页只读取发布时的快照；需要更新行情时，在本地 `8765` 手动扫描、人工确认，再执行发布命令。不要用 Pages、Worker 或公开接口反向触发本地扫描。

未登录时保留浏览器本地收藏。成员登录后，Pages Worker 和 D1 提供共享收藏、个人标注、管理员审核及训练反馈导出；这不会自动修改本地筛选规则。

## 可选 Tunnel 方案

若未来确实需要远程触发扫描，应另行部署常驻主机和 Cloudflare Tunnel，并使用 Cloudflare Access、速率限制和严格身份验证保护整个入口。该方案不属于当前 Pages 生产站点，不能直接复用公开的 `/api` 路径。

## 发布前检查

- Cloudflare 域名、账号和 `tradea` 项目归属正确。
- 页面公开内容已人工复核，且不包含本地扫描入口。
- `site/release.json` 与 `reports/latest.html` 一致。
- GitHub/Cloudflare 凭据只保存在对应 Secret 或本机授权配置中，绝不提交到仓库。
- D1 绑定名仍为 `DB`，`SETUP_TOKEN` 仍为加密 Secret。
- 第三方行情接口失败项在报告中被明确标注，没有被解释为低分或收益证据。

当前生产继续使用“只读 Pages + D1 成员协作”。
