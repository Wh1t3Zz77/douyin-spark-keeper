# 参与贡献

感谢你愿意帮助改进火花值守。提交代码前，请先阅读本项目的使用边界和安全策略。

## 提交问题

- 使用 Issue 描述可公开讨论的缺陷、兼容性问题或功能建议。
- 请写明部署环境、CPU 架构、操作系统、Docker/Compose 版本、复现步骤和必要日志。
- 发布日志前必须删除邮箱、Cookie、验证码、邀请码、服务器地址、数据库内容和其他个人数据。
- 安全漏洞不要公开提交，按 [`SECURITY.md`](SECURITY.md) 的方式私密报告。

## 本地开发

前端需要 Node.js 22.13 或更高版本：

```bash
npm ci
npm run lint
npm run build
```

后端建议使用 Python 虚拟环境：

```bash
python -m venv .venv
python -m pip install -r backend/requirements.txt
python -m pytest backend/tests
```

涉及部署的改动还应在全新环境执行：

```bash
docker compose config
docker compose up -d --build
docker compose ps
```

## Pull Request 要求

- 一个 Pull Request 尽量只解决一类问题，并说明动机、实现方式和验证结果。
- 不得提交 `.env`、数据库、浏览器资料、运行日志、构建产物或真实账号数据。
- 新功能应补充相应测试和文档；改变配置或部署方式时同步更新 `.env.example` 与 `docs/`。
- 保持默认本机部署的安全边界：除非用户显式配置，否则服务只监听 `127.0.0.1`。
- 不接受绕过验证码、安全挑战、平台风控或操作未授权账号的实现。

## 许可证

提交贡献即表示你有权提供相关内容，并同意按本项目的 [MIT License](LICENSE) 发布。若代码改编自其他项目，请明确来源并保留其许可证和版权声明。
