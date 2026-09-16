# 配置项

| 变量 | 默认/示例 | 说明 |
| --- | --- | --- |
| `APP_ENV` | `selfhosted` | 公网部署设为 `production` |
| `APP_SECRET` | 无安全默认值 | 会话摘要和敏感字段加密基础，至少 32 位随机字符 |
| `DATABASE_URL` | `sqlite:///./data/spark.db` | 默认 SQLite 数据库位置 |
| `ADMIN_EMAIL` | 用户填写 | 使用该邮箱注册的账号成为管理员 |
| `BOOTSTRAP_INVITE` | 用户填写 | 首次注册邀请码，只保存摘要 |
| `BOOTSTRAP_INVITE_USES` | `1` | 首次邀请码最多使用次数 |
| `SPARK_BIND_ADDRESS` | `127.0.0.1` | `0.0.0.0` 表示允许外部设备连接 |
| `SPARK_PORT` | `3000` | 浏览器访问端口 |
| `ALLOWED_ORIGINS` | 本机地址 | 逗号分隔的允许来源 |
| `SESSION_SECURE` | `false` | HTTPS 公网部署必须设为 `true` |
| `SESSION_DAYS` | `30` | 登录会话有效天数 |
| `EMAIL_VERIFICATION_REQUIRED` | `false` | `true` 时必须配置 SMTP |
| `SMTP_*` | 空 | 邮件服务器配置 |

修改 `.env` 后运行 `docker compose up -d` 使配置生效。更改 `APP_SECRET` 会使现有会话和加密字段失效，因此不要把它当作日常轮换项；需要轮换时应先规划迁移。

