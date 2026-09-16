<p align="center">
  <img src="docs/assets/spark-keeper-banner.png" alt="Spark Keeper" width="100%">
</p>

# 🔥 火花值守（Spark Keeper）

一个可自行部署的抖音网页端续火管理系统。它通过浏览器自动化维护登录状态、读取最近会话，并按计划发送预先设置的消息。

项目面向自托管使用：可以只运行在自己的 Windows、macOS 或 Linux 电脑上，也可以运行在局域网软路由、NAS、小主机或公网服务器上。域名不是必需条件。

> [!WARNING]
> 本项目依赖抖音网页端和浏览器自动化，不是抖音官方接口。网页改版、登录状态、平台规则或风控均可能使任务失败。请仅操作你本人拥有或已获明确授权的账号。

## 作者站点与公开邀请码

作者运行的火花值守站点：[https://spark.vv23.store](https://spark.vv23.store)

以下邀请码公开提供，可能随时被使用或失效：

- `SPARK-KLTR-WJB7`
- `SPARK-AZRH-GBXA`
- `SPARK-BB8V-2H2R`
- `SPARK-H4FG-GK2C`
- `SPARK-HSTY-T7DG`

## 支持的部署场景

| 场景 | 浏览器入口 | 默认安全边界 |
| --- | --- | --- |
| 当前电脑个人使用 | `http://localhost:3000` | 默认，仅监听 `127.0.0.1` |
| 当前电脑供局域网设备访问 | `http://电脑局域网IP:3000` | 需显式监听 `0.0.0.0` 并配置防火墙 |
| 软路由、NAS、家庭小主机 | `http://设备局域网IP:3000` | 建议仅在可信局域网或 VPN 内访问 |
| 私有服务器 | IP、内网域名或 VPN 地址 | 建议限制来源，不直接暴露端口 |
| 公网服务器 | `https://你的域名` | 必须配置 HTTPS、强密钥和邮件验证 |

## 功能

- 多个抖音账号独立绑定与状态管理。
- 最近会话同步、好友选择和自定义消息。
- 每日执行计划、队列、失败重试与运行记录。
- 登录失效、任务失败等可选邮件通知。
- 邀请码注册和管理员视图，适合单用户或小范围共享。
- SQLite 持久化，支持 Docker 卷备份和迁移。

## 五分钟本机启动

需要 Docker Desktop（Windows/macOS）或 Docker Engine + Compose 插件（Linux）。首次构建会下载镜像和浏览器运行环境，耗时取决于网络。

```bash
cp .env.example .env
```

Windows PowerShell 也可以使用：

```powershell
Copy-Item .env.example .env
```

编辑 `.env`，至少替换以下三项：

```dotenv
APP_SECRET=至少32位的随机字符串
ADMIN_EMAIL=你注册时要使用的邮箱
BOOTSTRAP_INVITE=仅你知道的首次注册邀请码
```

随后启动：

```bash
docker compose up -d --build
```

打开 <http://localhost:3000>，使用 `.env` 中的首次邀请码注册。默认未要求 SMTP，验证码会直接显示在注册页面；默认端口只允许当前电脑访问。

检查状态：

```bash
docker compose ps
docker compose logs --tail=100
```

## 选择你的部署方式

- [个人电脑：Windows、macOS、Linux](docs/DEPLOY-COMPUTER.md)
- [局域网、软路由、NAS 与 ARM 设备](docs/DEPLOY-LAN-NAS.md)
- [公网服务器、域名和 HTTPS](docs/DEPLOY-PUBLIC.md)
- [配置项说明](docs/CONFIGURATION.md)
- [备份、恢复、升级和卸载](docs/OPERATIONS.md)
- [安全说明](SECURITY.md)
- [参与贡献](CONTRIBUTING.md)
- [变更记录](CHANGELOG.md)

## 架构

浏览器只访问一个入口端口。内置 Caddy 将 `/api/*` 转发给 FastAPI，其余请求转发给前端，因此本机和局域网部署不需要额外安装 Nginx，也不存在前端与 API 端口不一致的问题。

```text
浏览器 -> gateway:3000 -> frontend:3000
                       -> api:8000 -> SQLite 数据卷
                                  -> worker (Playwright)
                                  -> mailer（可选）
```

邮件服务默认不启动。配置 SMTP 后使用以下命令启用：

```bash
docker compose --profile mail up -d
```

## 数据与隐私

数据库、登录 Cookie、浏览器资料和任务日志都可能包含敏感信息。它们保存在 Docker 数据卷中，不属于源码，禁止提交到 Git 仓库或公开分享。备份文件也应加密并限制访问。

## 自动检查

仓库内置 GitHub Actions，会在提交到 `main` 或发起 Pull Request 时自动执行前端类型检查与生产构建、npm 安全审计、后端测试和 Compose 配置校验。浏览器自动化的真实账号流程仍必须由维护者在隔离的测试账号和实机环境中人工验证。

## 使用边界

- 系统不会破解验证码或绕过安全验证。
- 出现扫码确认、短信验证、风险提示或结果不确定时，应由账号本人处理。
- 请勿用于骚扰、批量营销、未经授权的账号操作或违反平台规则的行为。
- 电脑关机、休眠或 Docker 停止后，自动任务不会继续执行。需要全天候运行时，请部署到常开设备。

## 🙏 参考与致谢

项目在设计和实现过程中参考了以下开源项目，感谢原作者公开经验：

- [Xiaowu-0916/douyin-spark](https://github.com/Xiaowu-0916/douyin-spark)：保守会话切换校验思路有实现改编，具体说明见 [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md)。
- [DkoBot/TikTokAutoSparkWeb](https://github.com/DkoBot/TikTokAutoSparkWeb)：Web 管理界面与功能组织的参考。
- [Yuriz132/douyin-cloud-streak](https://github.com/Yuriz132/douyin-cloud-streak)：云端定时运行与自行部署方案的参考。
- [2061360308/DouYinSparkFlow](https://github.com/2061360308/DouYinSparkFlow)：自动任务、目标匹配与部署方式的参考。

列为“参考”不表示本项目与其作者存在合作或获得官方背书。涉及代码改编的部分会保留相应版权和许可证说明。

## 开源许可

本项目采用 [MIT License](LICENSE)。你可以使用、修改、分发和再许可本项目，但必须保留许可证中的版权与许可声明。第三方改编部分仍同时受 [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md) 中相应许可约束。

欢迎提交 Issue 和 Pull Request。提交前请阅读 [`CONTRIBUTING.md`](CONTRIBUTING.md)，安全漏洞请按 [`SECURITY.md`](SECURITY.md) 私密报告。
