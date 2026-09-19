# Cloudflare 接入说明

## 当前边界

本项目的扫描过程依赖 Python、Pandas、NumPy 和 AkShare，并需要访问第三方行情接口。Cloudflare Pages 只能托管生成后的静态报告，Cloudflare Workers 也不适合直接运行当前扫描后端。

因此，GitHub 仓库可以直接保存和审查源码，但接入 Cloudflare 前需要先选择部署方式。

## 方案一：只读报告发布到 Pages

适合公开查看，风险最低。

1. 在可信的 Windows/Linux 主机或 CI 运行扫描。
2. 运行 `export_pages.cmd`，生成只读的 `site/index.html` 和安全响应头。
3. 在 Cloudflare Pages 连接本仓库的 `main` 分支，构建命令留空，输出目录填写 `site`。
4. 扫描命令和缓存不对公网开放。

该方案中，访问网页不会触发实时扫描；扫描频率由计划任务或 CI 决定。

## 方案二：扫描服务接入 Tunnel

适合本人或小范围使用。

1. 在一台持续在线的主机运行 `run.cmd` 或等价服务命令。
2. 安装并登录 `cloudflared`，创建 Tunnel 指向本机服务端口。
3. 为整个站点启用 Cloudflare Access 身份验证。
4. 配置速率限制，并限制谁能访问强制扫描入口。

该方案保留浏览器触发扫描能力，但主机必须持续在线。

## 上线前必须确认

- Cloudflare 域名和账号归属。
- 页面是公开只读，还是仅本人可访问。
- 扫描运行位置和时间表。
- GitHub/Cloudflare 凭据只保存在对应 Secrets 中，绝不提交到仓库。
- 第三方行情接口在部署环境中的可用性与限频情况。

推荐先采用“只读 Pages”，确认报告内容适合公开后，再决定是否开放受保护的扫描服务。
