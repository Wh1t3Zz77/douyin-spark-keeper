# 在个人电脑上部署

适用于 Windows 10/11、macOS 和常见 Linux 桌面发行版。域名、HTTPS、Nginx 和邮件服务器都不是本机个人使用的前置条件。

## Windows

1. 安装 Docker Desktop，并选择 Linux containers。
2. 在项目目录中复制 `.env.example` 为 `.env`。
3. 替换 `APP_SECRET`、`ADMIN_EMAIL` 和 `BOOTSTRAP_INVITE`。
4. 运行 `docker compose up -d --build`。
5. 打开 `http://localhost:3000`。

如果 Docker Desktop 使用 WSL 2，请确保系统虚拟化已开启。自动任务运行时不要让电脑休眠。

## macOS

安装 Docker Desktop，进入项目目录后执行：

```bash
cp .env.example .env
docker compose up -d --build
open http://localhost:3000
```

Intel Mac 使用 `amd64` 镜像；Apple Silicon 使用 `arm64` 镜像。所有基础镜像都必须具备对应架构后，项目才能在该平台运行。

## Linux 桌面

安装 Docker Engine 和 Compose 插件，将当前用户加入 Docker 组后执行：

```bash
cp .env.example .env
docker compose up -d --build
```

## 本机模式的默认行为

- `SPARK_BIND_ADDRESS=127.0.0.1`：其他设备无法直接连接。
- `EMAIL_VERIFICATION_REQUIRED=false`：注册和密码重置验证码显示在当前页面。
- `SESSION_SECURE=false`：允许通过本机 HTTP 使用登录 Cookie。
- 邮件任务不启动；需要邮件通知时配置 SMTP 并启用 `mail` profile。

## 常见问题

- 页面打不开：运行 `docker compose ps`，确认 `gateway`、`frontend`、`api` 和 `worker` 状态。
- 自动任务不执行：确认电脑没有休眠，并查看 `docker compose logs --tail=200 worker`。
- 端口冲突：修改 `.env` 中的 `SPARK_PORT`，例如改为 `3080`。
- 完全离线：首次构建仍需要下载镜像、依赖和 Playwright 运行环境；构建完成后日常启动不需要重新下载。

