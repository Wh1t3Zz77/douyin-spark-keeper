# 局域网、软路由、NAS 与小主机部署

## 开启局域网访问

在 `.env` 中设置：

```dotenv
SPARK_BIND_ADDRESS=0.0.0.0
SPARK_PORT=3000
ALLOWED_ORIGINS=http://设备局域网IP:3000
```

重新创建入口容器：

```bash
docker compose up -d
```

然后通过 `http://设备局域网IP:3000` 访问。只应在可信局域网中开放此端口，并使用系统防火墙限制来源网段。不要在路由器上直接把该端口映射到公网。

## 软路由与 NAS

- 需要 Docker/Container Manager 和 Compose 支持。
- 至少预留约 2 GB 内存；Playwright 浏览器任务是主要内存消耗来源。
- 数据卷必须位于可靠、可备份的存储上。
- 避免把 SQLite 数据库存放在不可靠的网络文件系统中。
- 主机需要正确的时间和时区；任务时间按应用显示的北京时间执行。

## CPU 架构

目标平台可能是 `linux/amd64` 或 `linux/arm64`。发布镜像前必须分别验证两种架构。某些低端 ARM 软路由缺少 Playwright/Chromium 所需的指令集或内存，此时不属于受支持设备。

查看架构：

```bash
docker info --format '{{.Architecture}}'
```

## 通过 VPN 访问

如果需要在外网管理家庭设备，优先使用 WireGuard、Tailscale 等 VPN，让服务继续只暴露在可信网络中。直接做公网端口映射会同时暴露登录页和浏览器自动化入口，不建议这样部署。

