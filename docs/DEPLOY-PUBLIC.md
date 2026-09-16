# 公网服务器、域名与 HTTPS

公网部署必须使用 HTTPS。不要直接把本项目的 HTTP 端口暴露给互联网。

## 推荐拓扑

```text
互联网 -> 宿主机 Nginx/Caddy/Traefik (HTTPS) -> 127.0.0.1:3000 -> 内置 gateway
```

`.env` 至少应调整为：

```dotenv
APP_ENV=production
SPARK_BIND_ADDRESS=127.0.0.1
SPARK_PORT=3000
ALLOWED_ORIGINS=https://你的域名
SESSION_SECURE=true
EMAIL_VERIFICATION_REQUIRED=true
SMTP_HOST=邮件服务器
SMTP_FROM=发件地址
```

同时使用新的强随机 `APP_SECRET` 和不可猜测的邀请码。启动邮件任务：

```bash
docker compose --profile mail up -d --build
```

## Nginx 示例

```nginx
server {
    listen 443 ssl http2;
    server_name spark.example.com;

    location / {
        proxy_pass http://127.0.0.1:3000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

证书配置因发行版和证书工具而异，请使用受信任证书并配置自动续期。

## 公网检查清单

- 仅 80/443 对公网开放，应用端口只监听 `127.0.0.1`。
- `SESSION_SECURE=true`，并确认登录 Cookie 只通过 HTTPS 发送。
- 邮件验证已启用且实际可送达。
- 未使用默认密钥、示例邮箱或示例邀请码。
- 防火墙、SSH 登录和系统更新已配置。
- 定期备份数据卷并实际演练恢复。
- 日志中不输出 Cookie、密码、验证码和完整用户数据。

