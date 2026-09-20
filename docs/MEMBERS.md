# 成员收藏与训练反馈

首次管理员创建和 7 只本机收藏迁移请按 [TradeA 成员初始化教程](INITIALIZATION.md) 操作。

## 架构

- `site/_worker.js`：Cloudflare Pages Worker，提供同源 API 并继续转发静态资源。
- `src/ashare_screener/local_owner.py`：仅在本机回环服务中读取 `%LOCALAPPDATA%\TradeA\owner.vars`，核验主管理员并代理成员 API；凭据和 Cookie 不进入浏览器。
- `src/ashare_screener/server.py`：为 `127.0.0.1:8765` 注入成员前端，严格校验固定 Host、同源写请求并禁止跨站 iframe 嵌入。
- Cloudflare D1 绑定名：`DB`。
- `db/migrations/0001_members.sql`：成员、会话、收藏、标注和审核表。
- `site/member.js`、`site/member.css`：登录、共享收藏、个人标注和管理员审核界面。
- `site/release.json`：当前线上报告对应的本地报告时间与内容指纹，不包含成员数据或密钥。

密码使用 PBKDF2-SHA256、独立随机盐和 100000 次迭代后保存。管理员密码至少 12 位，普通成员密码至少 4 位。登录会话只通过
`HttpOnly`、`SameSite=Strict` Cookie 传递，数据库只保存令牌的 SHA-256 摘要。

## Cloudflare 配置

生产站点为 [https://tradea-3al.pages.dev/](https://tradea-3al.pages.dev/)。D1、`DB` 绑定和 `SETUP_TOKEN` Secret 都属于 Cloudflare 项目配置，不进入 Pages 发布目录。发布新报告必须使用 `publish_pages.cmd`，使成员 Worker、静态报告和版本清单一起验收。

首次配置或重建环境时：

1. 在 Cloudflare 创建名为 `tradea-members` 的 D1 数据库。
2. 在 D1 控制台执行 `db/migrations/0001_members.sql`。
3. 在 Pages 项目的 Settings > Bindings 中添加 D1 binding，变量名必须为 `DB`。
4. 在 Pages 项目的 Settings > Variables and Secrets 中创建加密 Secret `SETUP_TOKEN`，
   使用至少24位的随机值。
5. 重新部署 `main`。首次打开网页时，成员中心会要求输入该口令来创建唯一管理员。

本地 Wrangler 开发可复制 `wrangler.toml.example` 为被 Git 忽略的
`wrangler.toml`，填入本人的数据库 ID，并在被 Git 忽略的 `.dev.vars` 中填写
`SETUP_TOKEN=...`。仓库不保存初始化口令、密码、会话、数据库 ID 或 API 密钥。

未登录时，页面继续使用浏览器本地收藏；登录后切换为当前成员的 D1 收藏，并保留本地收藏备份。成员中心可显式导入本机收藏；退出后恢复登录前的本地收藏，避免两套收藏互相覆盖。

使用 `run.cmd` 启动本地页面且非同步目录 `%LOCALAPPDATA%\TradeA\owner.vars` 配置完整时，本地服务会自动建立唯一主管理员会话。项目根目录和 OneDrive 内的 `.owner.vars` 不会被读取；`LOCALAPPDATA` 缺失时自动登录禁用且不回退。该模式不在浏览器保存管理员密码，也不允许浏览器调用登录、初始化或退出接口；远端会话过期时由本地服务重新认证一次，服务关闭时主动注销。只有绑定固定入口 `127.0.0.1` 才会启用自动管理员功能。管理员页面中的收藏、标注、成员和审核操作通过代理直接作用于生产成员数据库。

每个账号仍独立拥有自己的收藏。管理员登录后可在“成员收藏明细”中按账号查看每位成员收藏了哪些股票；普通成员不会收到完整收藏人清单，共享列表仍保留原有的收藏数量和首次添加者信息。管理员查看明细不会合并、转移或删除成员收藏。

## 反馈边界

成员可以为共享收藏填写日K/周K、观察日期、正样本/反例、置信度和文字标注，并申请
进入训练集。申请默认是 `pending`，只有管理员可以改为 `approved` 或 `rejected`。

导出文件只包含已批准标注。它是提供给 Codex 或本地审查流程的结构化输入，不是网页
指令，也不会自动修改筛选规则。导入前仍需复核样本日期、未来区间、负样本和样本外表现，
避免把主观收藏直接解释成机器学习效果或收益证据。
