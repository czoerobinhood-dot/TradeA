# 成员收藏与训练反馈

## 架构

- `site/_worker.js`：Cloudflare Pages Worker，提供同源 API 并继续转发静态资源。
- Cloudflare D1 绑定名：`DB`。
- `db/migrations/0001_members.sql`：成员、会话、收藏、标注和审核表。
- `site/member.js`、`site/member.css`：登录、共享收藏、个人标注和管理员审核界面。

密码使用 PBKDF2-SHA256、独立随机盐和 210000 次迭代后保存。登录会话只通过
`HttpOnly`、`SameSite=Strict` Cookie 传递，数据库只保存令牌的 SHA-256 摘要。

## Cloudflare 配置

1. 在 Cloudflare 创建名为 `tradea-members` 的 D1 数据库。
2. 在 D1 控制台执行 `db/migrations/0001_members.sql`。
3. 在 Pages 项目的 Settings > Bindings 中添加 D1 binding，变量名必须为 `DB`。
4. 在 Pages 项目的 Settings > Variables and Secrets 中创建加密 Secret `SETUP_TOKEN`，
   使用至少24位的随机值。
5. 重新部署 `main`。首次打开网页时，成员中心会要求输入该口令来创建唯一管理员。

本地 Wrangler 开发可复制 `wrangler.toml.example` 为被 Git 忽略的
`wrangler.toml`，填入本人的数据库 ID，并在被 Git 忽略的 `.dev.vars` 中填写
`SETUP_TOKEN=...`。仓库不保存初始化口令、密码、会话、数据库 ID 或 API 密钥。

## 反馈边界

成员可以为共享收藏填写日K/周K、观察日期、正样本/反例、置信度和文字标注，并申请
进入训练集。申请默认是 `pending`，只有管理员可以改为 `approved` 或 `rejected`。

导出文件只包含已批准标注。它是提供给 Codex 或本地审查流程的结构化输入，不是网页
指令，也不会自动修改筛选规则。导入前仍需复核样本日期、未来区间、负样本和样本外表现，
避免把主观收藏直接解释成机器学习效果或收益证据。
